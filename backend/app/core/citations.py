# -*- coding: utf-8 -*-
"""引用校验（M6 反幻觉）：抽答案里「《法》第X条」，逐条核对是否真实存在于语料，
把编造/张冠李戴的引用如实揪出来。只核对+标注，不静默通过。「绝不编造」红线落地。"""
import re

from .chunking import cn_num_to_int
from .corpus_store import load_chunks

# 法律名别名 → chunks 里的规范名（法律字段）。
# 注意：旧《合同法》的条号与民法典合同编不一一对应（旧114违约金≈民法典585），
# 故不设"合同法→合同编"同号别名——agent 写「合同法」即视为不可核实，应改引民法典。
LAW_ALIAS = {
    "民法典": "民法典（合同编）",
    "中华人民共和国民法典": "民法典（合同编）",
    "民法典合同编": "民法典（合同编）",
    "合同编": "民法典（合同编）",
    "合同编通则解释": "合同编通则解释",
    "合同编解释": "合同编通则解释",
    "通则解释": "合同编通则解释",
    "合同编通则司法解释": "合同编通则解释",
    "买卖合同解释": "买卖合同解释",
    "买卖合同司法解释": "买卖合同解释",
    "劳动合同法": "劳动合同法",
    "中华人民共和国劳动合同法": "劳动合同法",
    "劳动法": "劳动法",
    "中华人民共和国劳动法": "劳动法",
    "劳动合同法实施条例": "劳动合同法实施条例",
    "中华人民共和国劳动合同法实施条例": "劳动合同法实施条例",
    "实施条例": "劳动合同法实施条例",
    "社会保险法": "社会保险法",
    "劳动争议调解仲裁法": "劳动争议调解仲裁法",
    "调解仲裁法": "劳动争议调解仲裁法",
}

# 《法律名》第X条；条号兼容中文数字与阿拉伯数字
CITE_RE = re.compile(r"《([^》]+)》第([一二三四五六七八九十百零\d]+)条")


def _load() -> tuple[dict, dict]:
    """一次读取当前语料后端，同时得到两条全量：
    VALID = {规范法名: {序数 int, ...}}（"真实存在"的引用全集）
    TEXTS = {规范法名: {序数 int: 条文文本}}（供内容忠实度核对）"""
    valid: dict = {}
    texts: dict = {}
    for ch in load_chunks():
        valid.setdefault(ch["法律"], set()).add(ch["序数"])
        texts.setdefault(ch["法律"], {})[ch["序数"]] = ch["文本"]
    return valid, texts


VALID, TEXTS = _load()


def normalize_law(raw: str) -> str:
    """法律名别名归一：未知名保持原样（会落进 invalid，如实标注而非静默通过）。"""
    raw = raw.strip()
    return LAW_ALIAS.get(raw, raw)


def _compact_citations(text: str, offset: int = 0) -> list:
    """抓「法律名+阿拉伯数字+条」的紧凑写法（如「民法典585条」「劳动法19条」）。

    只认 LAW_ALIAS 里已知法律名（按名长降序，避免「民法典」抢先吞掉「中华人民共和国民法典」），
    绝不识别正文里的「第X条」这类合同条款——它们没有法律名。返回带位置信息的引用 dict。"""
    out = []
    keys = sorted(set(LAW_ALIAS), key=len, reverse=True)
    for name in keys:
        for m in re.finditer(re.escape(name) + r"(\d{1,4})条", text):
            raw_law = name
            raw_num = m.group(1)
            out.append({
                "raw_law": raw_law,
                "raw_num": raw_num,
                "num": cn_num_to_int(raw_num),
                "law": normalize_law(raw_law),
                "start": offset + m.start(),
                "end": offset + m.end(),
            })
    return out


def extract_citations(text: str) -> list:
    """抽出答案里所有「《法律名》第X条」及紧凑变体，归一成
    {raw_law, raw_num, num, law, start, end}。start/end 供内容忠实度取复述窗口。"""
    out = []
    for m in CITE_RE.finditer(text):
        raw_law = m.group(1).strip()
        if not raw_law:
            continue
        out.append({
            "raw_law": raw_law,
            "raw_num": m.group(2),
            "num": cn_num_to_int(m.group(2)),
            "law": normalize_law(raw_law),
            "start": m.start(),
            "end": m.end(),
        })
    out.extend(_compact_citations(text))
    return out


def _dedup(cites: list) -> list:
    """按 (法名, 条号) 去重：同一法条被反复引用只计一次，保留首次出现顺序。

    total/valid/invalid 都以去重后的为准——"引用准确率"的分母是"引了哪几个不同法条"，
    而不是"引了多少次"。否则同一法条在正文 + 参考依据各出现一次会被重复计数，虚高。
    """
    seen: set = set()
    out = []
    for c in cites:
        key = (c["law"], c["num"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _clean_window(window: str) -> str:
    """去掉 markdown 表格行/列表行/枚举项，只留自然叙述。

    4-gram 重叠只该评"人话"——表格（|…|）、列表（-/*/+）是结构化重述，与法条原文
    逐字重叠天然极低，会让 check_faithfulness 误报成 suspect（答案本身是对的）。"""
    kept = []
    for line in window.splitlines():
        s = line.strip()
        if s.startswith("|"):                       # markdown 表格行
            continue
        if s.startswith(("- ", "* ", "+ ")):        # 列表项
            continue
        if re.match(r"^\d+[.、)]", s):              # 枚举项
            continue
        kept.append(line)
    return "\n".join(kept)


def _hit_4grams(window: str, article: str) -> int:
    """复述窗口里命中了原文多少个 ≥4 字连续片段（4-gram）。

    法律条文模板化：违约金/当事人等短词到处都有，2-gram 区分不出『真复述』还是『用同款
    高频词编造』。而 ≥4 字的连续片段各条特有——正常整句复述会命中几十个，纯编造几乎为 0。
    用作内容忠实度的保守信号，只提示、不裁断。"""
    if len(window) < 4 or len(article) < 4:
        return 0
    a = set(article[i:i + 4] for i in range(len(article) - 3))
    return sum(1 for i in range(len(window) - 3) if window[i:i + 4] in a)


def check_faithfulness(text: str) -> list:
    """对每条『条号在语料中真实存在』的引用，抽查其复述内容是否明显偏离原文。

    只取有位置的引用，从其出现处向后取一段窗口，与该条原文比对字符重叠率。
    重叠率极低 → 判定为『条号对但内容复述存疑』，如实标注（不静默放行，也不改变条号真伪）。
    窗口/原文过短（<40 字）不判定，避免小样本偶然性。"""
    suspects = []
    for c in extract_citations(text):
        t = TEXTS.get(c["law"], {}).get(c["num"])
        if not t or len(t) < 40:
            continue
        # 窗口从『条号之后』开始：条号（第X条）与原文开头自身重复，若不排除会被误判为命中。
        # 先剔除表格/列表行，只评自然语句——避免结构化答案被误判为失实引用。
        window = _clean_window(text[c["end"]: c["end"] + 240])
        if len(window) < 12:  # 条号后没有像样的复述句子，无从判断，跳过
            continue
        hits = _hit_4grams(window, t)
        # 阈值 12：正常整句复述命中 32~107，张冠李戴≈8，纯编造=0，据此区分。
        if hits < 12:
            suspects.append({
                "law": c["law"], "num": c["num"],
                "raw": f"《{c['raw_law']}》第{c['raw_num']}条",
                "hits": hits,
            })
    # 去重：同一 (法,条) 多处引用只计一次
    seen, out = set(), []
    for s in suspects:
        key = (s["law"], s["num"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def verify_citations(text: str, allowed: set[tuple[str, int]] | None = None) -> dict:
    """核对答案里的法条引用。

    valid/invalid 回答「这条法条是否在本地语料中存在」；若传入 allowed，ungrounded
    额外回答「这条真实法条是否来自本轮检索证据」。两者必须分开：存在于语料不等于
    本轮模型真的检索到了它。
    """
    valid, invalid = [], []
    for c in _dedup(extract_citations(text)):
        if c["law"] in VALID and c["num"] in VALID[c["law"]]:
            valid.append(c)
        else:
            invalid.append(c)
    ungrounded = []
    if allowed is not None:
        ungrounded = [
            c for c in valid if (c["law"], c["num"]) not in allowed
        ]
    return {
        "total": len(valid) + len(invalid),
        "valid": valid,
        "invalid": invalid,
        "ungrounded": ungrounded,
        "suspect": check_faithfulness(text),
    }


def correction_prompt(check: dict) -> str:
    """返回喂回 LLM 的纠错指令：只保留本轮真实检索到的法条。"""
    bad = "；".join(f"《{c['raw_law']}》第{c['raw_num']}条" for c in check["invalid"])
    ungrounded = "；".join(
        f"《{c['raw_law']}》第{c['raw_num']}条" for c in check.get("ungrounded", [])
    )
    parts = []
    if bad:
        parts.append(f"{len(check['invalid'])} 处法条引用在检索语料中不存在：{bad}")
    if ungrounded:
        parts.append(
            f"{len(check['ungrounded'])} 处法条虽存在，但并非本轮检索工具返回的证据：{ungrounded}"
        )
    prefix = "你刚才的回答存在引用问题：" + "；".join(parts) + "。" if parts else "请保持现有引用真实可追溯。"
    return (
        prefix
        + "请只使用本轮检索工具已经返回的真实条文重写相关引用；"
        "找不到对应条文就如实说明「未检索到直接对应的条文」，不要凭记忆补充条号。"
        "只输出修正后的完整回答。"
    )


def annotate(answer: str, check: dict) -> str:
    """给答案追加一行引用校验脚注：全真打勾，有假如实标注；条号真但复述存疑也如实提示。

    total==0 分两种：真没引用（闲聊、说明检索不到）→ 原样不动；文本里残留『X条/《》』
    这类疑似引用痕迹但未被识别为合法引用 → 如实提示，杜绝静默通过。"""
    if not check["total"]:
        if re.search(r"[0-9一二三四五六七八九十百零]+条", answer) or "《" in answer:
            note = "引用校验：答案中出现疑似法条引用，但未能识别成可核实的「《法》第X条」，请人工核对"
            return f"{answer}\n\n> {note}"
        return answer  # 确实没引用，不动
    if check["invalid"]:
        bad = "、".join(f"《{c['raw_law']}》第{c['raw_num']}条" for c in check["invalid"])
        note = f"⚠️ 引用校验：{len(check['invalid'])} 处未能在语料中核实——{bad}"
    elif check.get("ungrounded"):
        bad = "、".join(f"《{c['raw_law']}》第{c['raw_num']}条" for c in check["ungrounded"])
        note = f"⚠️ 证据校验：{len(check['ungrounded'])} 处引用未来自本轮检索结果——{bad}"
    elif check.get("suspect"):
        bad = "、".join(s["raw"] for s in check["suspect"])
        note = (f"⚠️ 引用校验：{check['total']} 处条号均真实存在，但 "
                f"{len(check['suspect'])} 处复述内容与原文重叠度低，请人工核对——{bad}")
    else:
        note = f"✅ 引用校验：{check['total']} 处引用均真实存在（来源：检索语料）"
    return f"{answer}\n\n> {note}"
