# -*- coding: utf-8 -*-
"""为公开条款级评测集生成保守的初始人工标注。

标签是“是否值得重点核查”，不是对合同效力或责任的最终法律判断。违约金
是否过高、付款迟延责任是否成立都依赖损失、履约、采购规则等合同外事实。
"""
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = PROJECT_ROOT / "sample_contracts" / "public_clause_benchmark"

# 30 个“重点核查点”来自 8 个公开违约责任条款中的非重叠子条款，以及 5 个
# 付款迟延免责条款。它们是条款级样本点，而不是 30 份独立合同；评测报告必须按
# contract_id 分组解读，不能把同一合同的多个子条款当成独立合同泛化证据。
PENALTY_POINTS = (
    ("01_database_maintenance_03_liability_p1", "01_database_maintenance_03_liability", "1.甲、乙双方", "2.乙方向甲方"),
    ("01_database_maintenance_03_liability_p2", "01_database_maintenance_03_liability", "2.乙方向甲方", "3.乙方向甲方"),
    ("01_database_maintenance_03_liability_p3", "01_database_maintenance_03_liability", "3.乙方向甲方", "4.如果发生"),
    ("03_middleware_maintenance_03_liability_p1", "03_middleware_maintenance_03_liability", "1.甲、乙双方", "2.乙方向甲方"),
    ("03_middleware_maintenance_03_liability_p2", "03_middleware_maintenance_03_liability", "2.乙方向甲方", "3.乙方向甲方"),
    ("03_middleware_maintenance_03_liability_p3", "03_middleware_maintenance_03_liability", "3.乙方向甲方", "4.如果发生"),
    ("04_middleware_support_03_liability_p1", "04_middleware_support_03_liability", "（一）", "（二）"),
    ("04_middleware_support_03_liability_p2", "04_middleware_support_03_liability", "（二）", "（三）"),
    ("04_middleware_support_03_liability_p3", "04_middleware_support_03_liability", "（三）", "（四）"),
    ("04_middleware_support_03_liability_p4", "04_middleware_support_03_liability", "（四）", None),
    ("05_system_upgrade_03_liability_p1", "05_system_upgrade_03_liability", "（一）", "（二）"),
    ("05_system_upgrade_03_liability_p2", "05_system_upgrade_03_liability", "（二）", "（三）"),
    ("05_system_upgrade_03_liability_p3", "05_system_upgrade_03_liability", "（三）", "（四）"),
    ("05_system_upgrade_03_liability_p4", "05_system_upgrade_03_liability", "（四）", None),
    ("06_system_optimization_03_liability_p1", "06_system_optimization_03_liability", "（一）", "（二）"),
    ("06_system_optimization_03_liability_p2", "06_system_optimization_03_liability", "（二）", "（三）"),
    ("06_system_optimization_03_liability_p3", "06_system_optimization_03_liability", "（三）", "（四)"),
    ("06_system_optimization_03_liability_p4", "06_system_optimization_03_liability", "（四)", None),
    ("07_terminal_procurement_03_liability_p1", "07_terminal_procurement_03_liability", "1.甲乙双方", "2.本合同"),
    ("08_ntp_procurement_03_liability_p1", "08_ntp_procurement_03_liability", "1.甲乙双方", None),
    ("09_facility_repair_03_liability_p2", "09_facility_repair_03_liability", "2、乙方擅自转让", "3、乙方擅自解除"),
    ("09_facility_repair_03_liability_p3", "09_facility_repair_03_liability", "3、乙方擅自解除", "4、乙方未按期限"),
    ("09_facility_repair_03_liability_p4", "09_facility_repair_03_liability", "4、乙方未按期限", "5、乙方提交"),
    ("09_facility_repair_03_liability_p6", "09_facility_repair_03_liability", "6、乙方提供的成果", "7、乙方未按本合同"),
    ("09_facility_repair_03_liability_p7", "09_facility_repair_03_liability", "7、乙方未按本合同", "8、乙方及乙方工作人员"),
)
PARENT_LIABILITY_SECTIONS = {point[1] for point in PENALTY_POINTS}
PAYMENT_EXEMPTION = {
    "02_unified_communications_02_payment",
    "04_middleware_support_02_payment",
    "05_system_upgrade_02_payment",
    "06_system_optimization_02_payment",
    # 文件名沿用 OCR 初始主题，但正文实际是付款条件。
    "07_terminal_procurement_01_acceptance",
}
EXCLUDE_OCR = {
    "02_unified_communications_01_acceptance",
    "02_unified_communications_03_liability",
}

LAW = "民法典（合同编）"


def main() -> None:
    manifest = json.loads((DATASET_DIR / "manifest.json").read_text(encoding="utf-8"))
    annotations = []
    for sample in manifest["samples"]:
        sid = sample["id"]
        item = {
            "id": sid,
            "text_file": sample["text_file"],
            "contract_id": sample["contract_id"],
            "contract_type": sample["contract_type"],
            "ocr_review_required": True,
        }
        if sid in EXCLUDE_OCR:
            item.update({
                "split": "exclude",
                "label": "exclude_ocr_noise",
                "reason": "条款主体或语义被 OCR 水印/缺字破坏，不能作为可信评测样本。",
                "gold_articles": [],
            })
        elif sid in PARENT_LIABILITY_SECTIONS:
            item.update({
                "split": "exclude",
                "label": "exclude_parent_section_split_into_points",
                "reason": "该长条款已拆为非重叠子条款评测点，避免与子条款重复计分。",
                "gold_articles": [],
            })
        elif sid in PAYMENT_EXEMPTION:
            item.update({
                "split": "positive",
                "label": "needs_fact_review_payment_exemption",
                "reason": "付款迟延时免除一方违约责任的约定，需要结合付款条件、原因与适用采购规则核查。",
                "gold_articles": [[LAW, 577], [LAW, 579]],
                "query": "未支付价款 报酬 金钱债务 违约责任",
                "agent_prompt": "请审查以下付款条款中“财政资金不到位或集中支付延误时不承担违约责任”的约定是否需要重点核查。不得直接认定无效；说明需要结合哪些事实判断，并检索法律依据。",
            })
        elif int(sample["contract_id"][:2]) >= 11:
            item.update({
                "split": "pending_review",
                "label": "pending_human_review",
                "reason": "扩展集新增条款；完成独立人工复核前不进入准确率、召回率或 F1 统计。",
                "gold_articles": [],
                "annotation_status": "pending",
            })
        else:
            item.update({
                "split": "negative_manual_review",
                "label": "no_obvious_risk_on_text",
                "reason": "仅从脱敏条款文本未见可直接判定的风险，仍需人工结合合同全文、采购文件和履约事实复核。",
                "gold_articles": [],
            })
        annotations.append(item)

    by_id = {item["id"]: item for item in annotations}
    for point_id, parent_id, start, end in PENALTY_POINTS:
        parent = by_id[parent_id]
        annotations.append({
            "id": point_id,
            "parent_sample_id": parent_id,
            "text_file": parent["text_file"],
            "contract_id": parent["contract_id"],
            "contract_type": parent["contract_type"],
            "ocr_review_required": True,
            "clause_start": start,
            "clause_end": end,
            "split": "positive",
            "label": "needs_fact_review_penalty",
            "reason": "约定违约金、损失赔偿或解除后责任；是否过高需结合实际损失等事实判断。",
            "gold_articles": [[LAW, 585]],
            "query": "违约金 过分高于损失 调整",
            "agent_prompt": "请审查以下合同条款中的违约金和违约责任是否需要重点核查。不得直接认定无效；说明需要结合哪些事实判断，并检索法律依据。",
        })
    payload = {
        "version": "v0.3-expanded-pending-review",
        "label_definition": "重点核查需求，不是法律效力或责任的最终结论。",
        "samples": annotations,
    }
    (DATASET_DIR / "annotations.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    counts = {}
    for item in annotations:
        counts[item["label"]] = counts.get(item["label"], 0) + 1
    print(f"[OK] 标注 {len(annotations)} 条：{counts}")


if __name__ == "__main__":
    main()
