# -*- coding: utf-8 -*-
"""从临时的公开合同 OCR 文本中裁出轻量、脱敏的条款级评测集。

输出每份合同 3 段（验收、付款、违约；缺失时回退保密/质保），每段约 150~900 字。
原始 OCR 全文不作为最终数据集的一部分。
"""
import argparse
import json
import re
import shutil
from pathlib import Path

from backend.scripts.import_public_contracts import (
    OUT_DIR as FULL_TEXT_DIR,
    RAW_DIR,
    SOURCES,
    contains_obvious_pii,
    redact_text,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "sample_contracts" / "public_clause_benchmark"
TARGETS = [
    ("acceptance", ("验收", "交付")),
    ("payment", ("付款", "支付")),
    ("liability", ("违约", "解除")),
]
FALLBACKS = ("保密", "质保", "不可抗力", "争议")
MIN_CLAUSE_CHARS = 100
# 扫描合同常混用“第十一条”“第11条”“四、付款”“6、验收”“9违约责任”。
# 只在行首识别短标题，避免把 4.2.1 等正文子项误当成顶层章节。
ARTICLE_RE = re.compile(
    r"(?m)^(?:"
    r"第(?:[零〇一二三四五六七八九十百]+|\d+)[条章]"
    r"|[一二三四五六七八九十]{1,3}[、．.]"
    r"|\d{1,2}[、．.]"
    r"|\d{1,2}(?=(?:运输|交付|安装|调试|验收|付款|支付|违约|解除|争议|服务|保密|质保|不可抗力|合同|权利|义务))"
    r")"
)


def clean_body(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("#")).strip()


def article_sections(text: str) -> list[str]:
    positions = [m.start() for m in ARTICLE_RE.finditer(text)]
    if not positions:
        return []
    return [text[start: end].strip() for start, end in zip(positions, positions[1:] + [len(text)])]


def compact(section: str) -> str:
    section = re.sub(r"\n{2,}", "\n", section)
    section = re.sub(r"[ \t]+", "", section)
    # 句子中保留换行，方便人工核对；最长控制在 RAG 更易处理的范围。
    if len(section) > 900:
        cut = max(section.rfind("。", 300, 900), section.rfind("\n", 300, 900))
        section = section[:cut if cut > 300 else 900]
    return section.strip()


def pick_section(sections: list[str], keywords: tuple[str, ...], used: set[str]) -> str | None:
    for section in sections:
        key = section[:80]
        if key not in used and any(word in section[:140] for word in keywords):
            candidate = compact(section)
            if len(candidate) >= MIN_CLAUSE_CHARS:
                used.add(key)
                return candidate
    for section in sections:
        key = section[:80]
        if key not in used and any(word in section for word in keywords):
            candidate = compact(section)
            if len(candidate) >= MIN_CLAUSE_CHARS:
                used.add(key)
                return candidate
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="*", help="只处理指定合同 id，并合并现有 manifest")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / "manifest.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    selected_ids = set(args.ids or [])
    records_by_id = {
        item["id"]: item for item in previous.get("samples", [])
        if not selected_ids or item["contract_id"] not in selected_ids
    }
    missing = []
    selected = [row for row in SOURCES if not selected_ids or row[0] in selected_ids]
    for sample_id, contract_type, source_url in selected:
        source = FULL_TEXT_DIR / f"{sample_id}.txt"
        if not source.exists():
            missing.append(sample_id)
            continue
        text = clean_body(source.read_text(encoding="utf-8"))
        sections = article_sections(text)
        used: set[str] = set()
        selected = []
        for label, keywords in TARGETS:
            picked = pick_section(sections, keywords, used)
            if picked:
                selected.append((label, picked))
        for fallback in FALLBACKS:
            if len(selected) >= 3:
                break
            picked = pick_section(sections, (fallback,), used)
            if picked:
                selected.append((fallback, picked))
        if not selected:
            missing.append(sample_id)
            continue
        for index, (label, clause) in enumerate(selected, start=1):
            clause = redact_text(clause).strip()
            residual = contains_obvious_pii(clause)
            if residual:
                raise RuntimeError(f"{sample_id}/{label} 残留敏感模式：{residual}")
            name = f"{sample_id}_{index:02d}_{label}.txt"
            (OUT_DIR / name).write_text(
                "# 公开披露合同的二次脱敏条款\n"
                f"# 合同类型：{contract_type}\n"
                f"# 条款主题：{label}\n"
                "# 仅用于评测，需人工核对 OCR 文本与原公开附件。\n\n"
                f"{clause}\n",
                encoding="utf-8",
            )
            record = {
                "id": name.removesuffix(".txt"),
                "contract_id": sample_id,
                "contract_type": contract_type,
                "topic": label,
                "source": "中国政府采购网（官方公开合同公告）",
                "source_url": source_url,
                "text_file": name,
                "characters": len(clause),
                "annotation_status": "unlabeled",
                "ocr_review_required": True,
            }
            records_by_id[record["id"]] = record
    records = sorted(records_by_id.values(), key=lambda item: item["id"])
    manifest_path.write_text(
        json.dumps({"samples": records, "missing_contracts": missing}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # 完整 OCR 文本和原始附件不属于轻量数据集，生成条款后立即清理。
    if selected_ids:
        # 缺少可识别章节的全文需保留给人工检查和解析规则修正。
        for sample_id in selected_ids - set(missing):
            (FULL_TEXT_DIR / f"{sample_id}.txt").unlink(missing_ok=True)
        if FULL_TEXT_DIR.exists() and not any(FULL_TEXT_DIR.iterdir()):
            FULL_TEXT_DIR.rmdir()
    else:
        shutil.rmtree(FULL_TEXT_DIR)
    shutil.rmtree(RAW_DIR, ignore_errors=True)
    print(f"完成：{len(records)} 条短条款，缺少合同：{missing}")


if __name__ == "__main__":
    main()
