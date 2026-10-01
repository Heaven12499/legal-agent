# -*- coding: utf-8 -*-
"""手写 while 循环实现 OpenAI function calling 多轮检索：先由 LLM 判断是否需要检索，
有工具调用就执行并回填，否则直接回答。trace 记录每轮 query 与命中供前端展示。"""
import json
import os

from .llm import chat
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_SCHEMAS, TOOL_EXECUTORS, retrieve
from ..core.citations import verify_citations, correction_prompt, annotate

# 反思循环上限：verify→feedback→rewrite→re-verify 至多 N 轮（env 可调）。
# 与 max_rounds（检索轮预算）分开，反思不挤占检索轮。
REFLECT_MAX_ROUNDS = int(os.environ.get("REFLECT_MAX_ROUNDS", "2"))

# 只做“识别到特定文字信号后补充检索”，不预先断言条款无效，也不把法条直接塞进答案。
# 这些规则的作用是让 Agent 在常见但易漏的合同语言下看到应当比较的法律证据。
_COVERAGE_RULES = (
    {
        "name": "付款迟延免责",
        "signals": ("财政资金", "资金不到位", "集中支付", "付款延误", "支付延误"),
        "must_also_contain": ("不承担违约责任", "不承担责任", "免责"),
        "query": "未支付价款 报酬 金钱债务 违约责任",
        "expected_any": {("民法典（合同编）", 577), ("民法典（合同编）", 579)},
    },
    {
        "name": "违约金约定",
        "signals": ("违约金",),
        "must_also_contain": (),
        "query": "违约金 过分高于损失 调整",
        "expected_any": {("民法典（合同编）", 585)},
    },
)


def _source_text(query: str, history: list | None) -> str:
    """取得本轮待审文本，仅用于触发补充检索，不把历史 assistant 输出当作合同事实。"""
    parts = [query]
    for item in history or []:
        if item.get("role") == "user" and isinstance(item.get("content"), str):
            parts.append(item["content"])
    return "\n".join(parts)


def _coverage_rules(query: str, history: list | None) -> list[dict]:
    text = _source_text(query, history)
    matched = []
    for rule in _COVERAGE_RULES:
        if not any(word in text for word in rule["signals"]):
            continue
        required = rule["must_also_contain"]
        if required and not any(word in text for word in required):
            continue
        matched.append(rule)
    return matched


def _missing_coverage(answer: str, rules: list[dict], allowed: set[tuple[str, int]]) -> list[dict]:
    """找出已检索到对应依据、但最终答案仍未引用的重点核查类型。"""
    cited = {(c["law"], c["num"]) for c in verify_citations(answer, allowed)["valid"]}
    missing = []
    for rule in rules:
        candidates = rule["expected_any"] & allowed
        if candidates and not (cited & candidates):
            missing.append({**rule, "candidates": sorted(candidates)})
    return missing


def _coverage_prompt(missing: list[dict]) -> str:
    descriptions = []
    for rule in missing:
        laws = "、".join(f"《{law}》第{num}条" for law, num in rule["candidates"])
        descriptions.append(f"“{rule['name']}”情形尚未引用已检索到的重点依据（{laws}）")
    return (
        "另有证据覆盖缺口：" + "；".join(descriptions) + "。"
        "请结合条款事实说明这些依据是否适用；适用时补充引用，不适用时说明理由。"
        "不得把条款直接判定为无效。"
    )


def _dispatch(name: str, args: dict) -> dict:
    """执行单个工具调用（注册表驱动），返回 {text, labels, evidence}；未知工具把错误文本喂回 LLM。

    新增工具只需在 tools.TOOL_SCHEMAS/TOOL_EXECUTORS 注册，本函数无需改动。"""
    executor = TOOL_EXECUTORS.get(name)
    if executor is None:
        return {"text": f"未知工具：{name}", "labels": [], "evidence": []}
    try:
        return executor(**args)
    except TypeError:
        # 参数与 schema 不符（LLM 漏传/错传）——把错误喂回 LLM，让它重试
        return {
            "text": f"工具 {name} 参数错误：{args}，请检查参数后重试",
            "labels": [], "evidence": [],
        }


def _strip_fence(text: str) -> str:
    """剥掉 LLM 可能加的 markdown 围栏（```json ... ```）。"""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.replace("```", "")
        s = s.lstrip("json").lstrip()
    return s


def _parse_reflection(content: str, fallback: str) -> tuple:
    """解析反思阶段 LLM 的 JSON 输出，返回 (答案, 是否替换)。

    LLM 偶尔不老实返回裸 JSON（带围栏或直接给文本），逐级降级解析保证不丢信息：
    只有真解析出非空 fixed_answer 才算替换；解析不出就用整段文本尽力而为。"""
    for candidate in (content, _strip_fence(content)):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("fixed_answer"), str) \
                and data["fixed_answer"].strip():
            return data["fixed_answer"].strip(), True
    s = content.strip()
    return (s, True) if s else (fallback, False)


def _reflect(messages: list, answer: str, allowed: set[tuple[str, int]], coverage_rules: list[dict]) -> tuple:
    """有界反思循环：对不存在或未从本轮证据取得的引用触发校验→反馈→重写→再校验，
    至多 REFLECT_MAX_ROUNDS 轮。

    刻意不把 suspect（条号真但复述偏离）纳入反思：真实运行中 agent 已很少编造（invalid 常为 0），
    而 suspect 又常是 check_faithfulness 对结构化答案（表格/列表）的误报——为它调 LLM 重写
    性价比低。suspect 只交给 annotate 如实标 ⚠️，不静默通过、也不为它烧 token。
    反思阶段用 response_format=json_object 强约束 LLM 返回 JSON，解析 fixed_answer；失败则保留。
    返回 (答案, 校验, reflections, stats)，stats 供评测量化 invalid 修复率。
    """
    check = verify_citations(answer, allowed)
    initial_invalid = len(check["invalid"])
    initial_ungrounded = len(check["ungrounded"])
    initial_suspect = len(check.get("suspect", []))
    initial_coverage_missing = _missing_coverage(answer, coverage_rules, allowed)
    reflections = []
    replaced_any = False
    for k in range(1, REFLECT_MAX_ROUNDS + 1):
        coverage_missing = _missing_coverage(answer, coverage_rules, allowed)
        if not check["invalid"] and not check["ungrounded"] and not coverage_missing:
            break
        # DeepSeek/OpenAI 的 json_object 模式要求 prompt 里含 "json" 字样，否则不启用。
        # 显式引导 JSON 结构（含 "JSON"），correction_prompt 核心纠错文本不动。
        json_hint = (
            '请以 JSON 格式返回，字段：{"verdict": "pass" 或 "fix", '
            '"fixed_answer": "修正后的完整回答"}；不要输出 JSON 之外的任何内容。'
            '若用户规定了首行或其他输出协议，fixed_answer 必须完整保留该协议。'
        )
        try:
            resp = chat(
                messages + [{"role": "user", "content": (
                    correction_prompt(check) + "\n\n" + _coverage_prompt(coverage_missing) + "\n\n" + json_hint
                )}],
                response_format={"type": "json_object"},
            )
            new_answer, replaced = _parse_reflection(
                resp.choices[0].message.content or "", answer
            )
            if replaced:
                answer = new_answer
                replaced_any = True
            check = verify_citations(answer, allowed)
            reflections.append({
                "round": k,
                "invalid_remaining": len(check["invalid"]),
                "ungrounded_remaining": len(check["ungrounded"]),
                "replaced": replaced,
            })
        except Exception:  # noqa: BLE001 —— 反思调用失败就保留当前答案，仍会 annotate
            break
    stats = {
        "rounds": len(reflections),
        "initial_invalid": initial_invalid,
        "final_invalid": len(check["invalid"]),
        "initial_ungrounded": initial_ungrounded,
        "final_ungrounded": len(check["ungrounded"]),
        "initial_suspect": initial_suspect,  # suspect 仅记录/如实标注
        "final_suspect": len(check.get("suspect", [])),
        "initial_coverage_missing": len(initial_coverage_missing),
        "final_coverage_missing": len(_missing_coverage(answer, coverage_rules, allowed)),
        "replaced": replaced_any,
    }
    return annotate(answer, check), check, reflections, stats


def run(query: str, history: list | None = None, max_rounds: int = 10) -> dict:
    """跑一轮 agent，返回 {"answer": str, "rounds": int, "trace": [...]}。

    history 是上一轮对话的干净 user/assistant 轮次（不含中间 tool 消息），
    拼在 system 之后、本轮 query 之前。
    max_rounds 是 LLM 调用轮次上限：合同审查需检索多个风险点，放宽到 10；
    问答通常 1~3 轮即结束，上限只影响最坏情况。
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": query})
    trace = []
    allowed_evidence: set[tuple[str, int]] = set()
    coverage_rules = _coverage_rules(query, history)
    coverage_prefetched = False

    for rnd in range(1, max_rounds + 1):
        # 首轮即允许模型路由：实体法律问题按 system 纪律必须调用检索；寒暄、功能说明、
        # 澄清和不涉及法律判断的纯文本任务可以直接回答，避免无意义检索。
        resp = chat(
            messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
        )
        msg = resp.choices[0].message

        # 无工具调用 => 信息已够，直接给答案（M6 反思：校验→必要时重写→脚注）
        if not msg.tool_calls:
            answer, check, reflections, stats = _reflect(
                messages, msg.content or "", allowed_evidence, coverage_rules
            )
            return {
                "answer": answer, "rounds": rnd, "trace": trace,
                "citation_check": check, "reflections": reflections,
                "reflection_stats": stats,
            }

        # assistant 消息必须原样回填（含 tool_calls），API 按 tool_call_id 对齐
        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            result = _dispatch(tc.function.name, args)
            for evidence in result.get("evidence", []):
                allowed_evidence.add((evidence["law"], evidence["num"]))
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result["text"]}
            )
            trace.append(
                {"round": rnd, "tool": tc.function.name, "query": args.get("query", ""),
                 "hits": result["labels"], "evidence": result.get("evidence", [])}
            )

        # 只有模型已经判断本轮需要工具后，才补充常见易漏风险的候选依据；这样既保留
        # 合同审查覆盖率，也不会让非法律问题因历史合同中的关键词被提前强制检索。
        if coverage_rules and not coverage_prefetched:
            searched_queries = {item.get("query") for item in trace}
            for rule in coverage_rules:
                if rule["query"] in searched_queries:
                    continue
                result = retrieve(rule["query"])
                for evidence in result.get("evidence", []):
                    allowed_evidence.add((evidence["law"], evidence["num"]))
                messages.append({
                    "role": "user",
                    "content": (
                        f"系统为“{rule['name']}”补充了候选法律依据。请结合条款事实判断是否适用，"
                        f"不要仅因出现该词语直接作出无效结论：\n\n{result['text']}"
                    ),
                })
                trace.append({
                    "round": rnd,
                    "tool": "coverage_prefetch",
                    "query": rule["query"],
                    "hits": result["labels"],
                    "evidence": result.get("evidence", []),
                })
            coverage_prefetched = True

    # 超轮次仍无答案：返回最后已知信息
    return {"answer": "（达到最大检索轮次，仍未给出最终答案）", "rounds": max_rounds, "trace": trace}
