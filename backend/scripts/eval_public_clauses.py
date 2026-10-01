# -*- coding: utf-8 -*-
"""公开条款级评测：默认只测混合检索；--agent 才会调用 LLM。

检索指标仅面向有金标法条的正样本；Agent 模式要求模型输出结构化判定，
对已复核正负样本计算风险识别 Precision / Recall / F1。待复核扩展样本不计分。
"""
import argparse
import json
from pathlib import Path

from backend.app.core.hybrid import get_hybrid
from backend.app.core.citations import extract_citations


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = PROJECT_ROOT / "sample_contracts" / "public_clause_benchmark"
SCORED_SPLITS = {"positive", "negative_manual_review"}
DECISION_INSTRUCTION = (
    "你正在参加固定评测。回答第一行必须且只能是“判定：需重点核查”或"
    "“判定：无明显风险”，之后再说明理由；不得省略第一行。"
)


def load() -> list[dict]:
    return json.loads((DATASET_DIR / "annotations.json").read_text(encoding="utf-8"))["samples"]


def clause_text(item: dict) -> str:
    """读取一个条款点；长违约责任条款按标注的非重叠边界裁成子条款。"""
    text = (DATASET_DIR / item["text_file"]).read_text(encoding="utf-8")
    start = item.get("clause_start")
    if not start:
        return text
    begin = text.find(start)
    if begin < 0:
        raise ValueError(f"{item['id']} 未找到 clause_start: {start}")
    end_marker = item.get("clause_end")
    end = text.find(end_marker, begin + len(start)) if end_marker else -1
    if end_marker and end < 0:
        raise ValueError(f"{item['id']} 未找到 clause_end: {end_marker}")
    return text[begin:end if end >= 0 else None].strip()


def retrieval_eval(samples: list[dict], k: int) -> list[dict]:
    retriever = get_hybrid()
    rows = []
    for item in samples:
        if item["split"] != "positive":
            continue
        hits = retriever.search(item["query"], k)
        found = {(h["法律"], h["序数"]) for h in hits}
        gold = {tuple(x) for x in item["gold_articles"]}
        ranks = [rank for rank, hit in enumerate(hits, start=1)
                 if (hit["法律"], hit["序数"]) in gold]
        first_gold_rank = min(ranks) if ranks else None
        rows.append({
            "id": item["id"], "label": item["label"], "hit": bool(found & gold),
            "gold": sorted(gold), "retrieved": sorted(found),
            "first_gold_rank": first_gold_rank,
        })
    return rows


def agent_eval(samples: list[dict]) -> list[dict]:
    from backend.app.agent.loop import run

    rows = []
    for item in samples:
        if item["split"] not in SCORED_SPLITS:
            continue
        clause = clause_text(item)
        task_prompt = item.get(
            "agent_prompt",
            "请判断以下条款仅依据当前文本是否存在需要重点核查的合同风险，并说明判断依据。",
        )
        result = run(
            f"{DECISION_INSTRUCTION}\n\n{task_prompt}",
            history=[{"role": "user", "content": f"待审查条款如下：\n\n{clause}"}],
        )
        cited = {(c["law"], c["num"]) for c in extract_citations(result["answer"])}
        gold = {tuple(x) for x in item["gold_articles"]}
        check = result.get("citation_check", {})
        predicted_risk = parse_risk_decision(result["answer"])
        gold_risk = item["split"] == "positive"
        rows.append({
            "id": item["id"],
            "gold_risk": gold_risk,
            "predicted_risk": predicted_risk,
            "hit": bool(cited & gold) if gold_risk else None,
            "gold": sorted(gold),
            "cited": sorted(cited), "rounds": result.get("rounds"),
            "citation_invalid": len(check.get("invalid", [])),
            "citation_ungrounded": len(check.get("ungrounded", [])),
            "answer": result["answer"],
        })
    return rows


def parse_risk_decision(answer: str) -> bool | None:
    """只解析评测协议规定的首行，避免用“风险”等关键词臆测模型判定。"""
    first_line = answer.strip().splitlines()[0].replace(" ", "") if answer.strip() else ""
    if first_line == "判定：需重点核查":
        return True
    if first_line == "判定：无明显风险":
        return False
    return None


def summarize_risk(rows: list[dict]) -> dict:
    decided = [row for row in rows if row["predicted_risk"] is not None]
    tp = sum(row["gold_risk"] and row["predicted_risk"] for row in decided)
    fp = sum(not row["gold_risk"] and row["predicted_risk"] for row in decided)
    fn = sum(row["gold_risk"] and not row["predicted_risk"] for row in decided)
    tn = sum(not row["gold_risk"] and not row["predicted_risk"] for row in decided)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "evaluated": len(rows),
        "decided": len(decided),
        "unparseable": len(rows) - len(decided),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "accuracy": (tp + tn) / len(decided) if decided else 0.0,
    }


def summarize(rows: list[dict]) -> dict:
    rows = [row for row in rows if row.get("hit") is not None]
    summary = {
        "evaluated": len(rows),
        # 这里只测“金标法条是否被引用/检索到”，不是端到端风险识别召回率。
        "gold_article_hit_rate": sum(row["hit"] for row in rows) / len(rows) if rows else 0.0,
        "hits": sum(row["hit"] for row in rows),
    }
    if rows and "first_gold_rank" in rows[0]:
        ranks = [row["first_gold_rank"] for row in rows]
        # 单金标/多金标都取首个金标的倒数排名，衡量精排是否把关键条推到前面。
        summary["mrr"] = sum(1 / rank if rank else 0 for rank in ranks) / len(rows)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", action="store_true", help="调用 LLM，成本与耗时更高")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    samples = load()
    retrieval = retrieval_eval(samples, args.k)
    report = {"retrieval_top_k": args.k, "retrieval": summarize(retrieval), "retrieval_rows": retrieval}
    if args.agent:
        agent = agent_eval(samples)
        report["agent"] = {
            "citation": summarize(agent),
            "risk_classification": summarize_risk(agent),
        }
        report["agent_rows"] = agent
    out = DATASET_DIR / "eval_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if not k.endswith("rows")}, ensure_ascii=False, indent=2))
    print(f"完整报告：{out}")


if __name__ == "__main__":
    main()
