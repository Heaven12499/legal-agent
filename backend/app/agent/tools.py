# -*- coding: utf-8 -*-
"""
agent 的工具层：retrieve 工具的 function calling schema + 执行器。

查询改写不设单独工具——检索一次不够就换法律术语再检，在 loop 循环里自然发生。
执行器直接复用 core.hybrid 的混合检索；label 口径与 verify_retrieval.py 一致。
"""
from ..core.hybrid import get_hybrid
from ..core.citations import normalize_law
from ..core.corpus_store import load_chunks

# 命中法条后，把同法相邻条（序数 ±NEIGHBOR_SPAN）也补进上下文。
# 法律条文高度关联（如 585 违约金常要和 584/586 配套引用），单条 chunk 里 LLM 看不到邻居。
NEIGHBOR_SPAN = 1
_nbr_index = None


def _neighbor_index() -> dict:
    """惰性建 {法律: {序数 int: chunk}}，供相邻条扩展按序数查找邻居。"""
    global _nbr_index
    if _nbr_index is None:
        idx: dict = {}
        for ch in load_chunks():
            idx.setdefault(ch["法律"], {})[ch["序数"]] = ch
        _nbr_index = idx
    return _nbr_index


def _expand_neighbors(hits: list) -> list:
    """主命中之外，补同法相邻条（±span），按 (法律, 序数) 排序保持上下文有序。

    只补同法、序数真实存在的条；不重复。返回主命中 + 邻居。"""
    idx = _neighbor_index()
    # 向量主命中和相邻条索引里的对象可能不是同一个 Python 对象，不能用 id() 去重；
    # 否则主命中会被作为“邻居”重复拼入上下文，挤占真正的关联法条。
    by_key = {(h["法律"], h["序数"]): h for h in hits}
    for h in hits:
        law, n = h["法律"], h["序数"]
        for d in range(1, NEIGHBOR_SPAN + 1):
            for nb in (n - d, n + d):
                ch = idx.get(law, {}).get(nb)
                if ch:
                    by_key.setdefault((law, nb), ch)
    primary_keys = {(h["法律"], h["序数"]) for h in hits}
    extra = [c for key, c in by_key.items() if key not in primary_keys]
    return hits + sorted(extra, key=lambda c: (c["法律"], c["序数"]))

RETRIEVE_TOOL = {
    "type": "function",
    "function": {
        "name": "retrieve",
        "description": (
            "检索合同相关法律条文。按待审合同类型选择法律依据：劳动合同相关检索劳动法、"
            "劳动合同法、劳动合同法实施条例、社会保险法；商业/采购/租赁等一般合同检索"
            "民法典合同编及配套的合同编通则解释、买卖合同解释（违约金、格式条款、"
            "合同解除等执行口径）。输入一个用法律术语表达的查询"
            "（例如把口语'违约金太高'改写成'违约金 过分高于损失 调整'），返回最相关的条文。"
            "一次检索结果不足以回答时，请换一种法律表述再次调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索查询，尽量用法律术语而非口语",
                },
                "k": {
                    "type": "integer",
                    "description": "返回条数，默认 5",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}


def label(chunk: dict) -> str:
    """chunk 的展示名：「法律+条号」，如「民法典（合同编）第五百八十五条」。"""
    return f"{chunk['法律']}{chunk['条号']}"


def format_chunk(chunk: dict, idx: int) -> str:
    """把一条检索结果格式化成给 LLM 看的一小段。"""
    return f"[{idx}] {label(chunk)}\n{chunk['文本']}"


def evidence_ref(chunk: dict) -> dict:
    """给 trace/引用校验使用的最小证据标识。"""
    return {"law": chunk["法律"], "num": chunk["序数"], "label": label(chunk)}


def retrieve(query: str, k: int = 5) -> dict:
    """执行一次检索，返回给 LLM 的文本、展示标签及本轮可引用证据。

    labels 只记主命中（top-k），供前端 trace 展示；text 在给 LLM 时补上相邻条上下文。"""
    primary = get_hybrid().search(query, k)
    labels = [label(h) for h in primary]
    if not primary:
        text = f"检索「{query}」未命中任何条文或案例，请换一种法律表述再试。"
        evidence = []
    else:
        hits = _expand_neighbors(primary)
        text = "\n\n".join(format_chunk(h, i + 1) for i, h in enumerate(hits))
        # 相邻条也实际交给了模型，因此同样属于可追溯证据。
        evidence = [evidence_ref(h) for h in hits]
    return {"text": text, "labels": labels, "evidence": evidence}


LOOKUP_TOOL = {
    "type": "function",
    "function": {
        "name": "lookup_article",
        "description": (
            "按法律名和条号精确查找某一条法条的原文。用于核实/复核你打算引用的条文："
            "当你要引用「《法律名》第X条」时，先用本工具调出该条原文，确认内容与你复述的一致，"
            "再落笔；反思阶段也用它拿原文比对可疑引用。法律名与条号必须确切。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "law_name": {
                    "type": "string",
                    "description": "规范法律名，如「民法典（合同编）」「劳动合同法」「合同编通则解释」",
                },
                "article_number": {
                    "type": "integer",
                    "description": "条号（阿拉伯数字），如 585",
                },
            },
            "required": ["law_name", "article_number"],
        },
    },
}


def lookup_article(law_name: str, article_number: int) -> dict:
    """按规范法名（自动别名归一）+ 条号从语料精确查一条法条原文。

    数据全部来自当前法律语料后端，绝不虚构。找不到时如实返回未找到。返回结构化
    {text, labels, found}，found 供调用方/反思循环判断是否命中。"""
    law = normalize_law(law_name or "")
    ch = _neighbor_index().get(law, {}).get(int(article_number))
    if not ch:
        return {
            "text": f"未找到《{law_name}》第{article_number}条：该条不在语料中，"
                    "请核对法律名/条号，或改用 retrieve 检索。",
            "labels": [],
            "evidence": [],
            "found": False,
        }
    return {
        "text": format_chunk(ch, 1),
        "labels": [label(ch)],
        "evidence": [evidence_ref(ch)],
        "found": True,
    }


# 工具注册表：新增工具只需在 TOOL_SCHEMAS 加 schema、TOOL_EXECUTORS 加执行器，loop 零改动。
TOOL_SCHEMAS = [RETRIEVE_TOOL, LOOKUP_TOOL]
TOOL_EXECUTORS = {"retrieve": retrieve, "lookup_article": lookup_article}
