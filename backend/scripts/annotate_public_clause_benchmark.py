# -*- coding: utf-8 -*-
"""为公开条款级评测集生成可复现的保守策展标注。

标签是“是否值得重点核查”，不是对合同效力或责任的最终法律判断。违约金
是否过高、付款迟延责任是否成立都依赖损失、履约、采购规则等合同外事实。

这里的扩展标签是基准维护者根据公开文本做的单轮策展，并非双人律师金标；
不确定、上下文不足或 OCR 破损的样本一律排除，不用来调高指标。
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
    "08_ntp_procurement_01_acceptance",
    "13_desktop_cloud_02_payment",
    "14_software_outsourcing_02_liability",
    "15_customs_system_development_02_payment",
    "16_customs_validation_03_liability",
    "17_on_site_service_02_payment",
    "18_architecture_compliance_02_payment",
    "19_image_recognition_03_liability",
    "22_museum_repair_02_payment",
    "23_laboratory_equipment_02_payment",
    "25_open_source_support_02_payment",
}
PENALTY_SECTIONS = {
    "11_accounting_platform_03_liability",
    "13_desktop_cloud_03_liability",
    "14_software_outsourcing_01_payment",
    "15_customs_system_development_03_liability",
    "17_on_site_service_03_liability",
    "18_architecture_compliance_03_liability",
    "20_express_clearance_03_liability",
    "21_food_supply_01_acceptance",
    "22_museum_repair_03_liability",
    "25_open_source_support_03_liability",
}
NEGATIVE_CURATED = {
    "04_middleware_support_01_acceptance",
    "07_terminal_procurement_02_payment",
    "08_ntp_procurement_02_payment",
    "09_facility_repair_01_acceptance",
    "09_facility_repair_02_payment",
    "11_accounting_platform_01_acceptance",
    "12_medical_equipment_01_acceptance",
    "14_software_outsourcing_03_保密",
    "15_customs_system_development_01_acceptance",
    "16_customs_validation_01_acceptance",
    "16_customs_validation_02_payment",
    "17_on_site_service_01_acceptance",
    "18_architecture_compliance_01_acceptance",
    "19_image_recognition_01_acceptance",
    "19_image_recognition_02_payment",
    "20_express_clearance_01_acceptance",
    "20_express_clearance_02_payment",
    "22_museum_repair_01_acceptance",
    "23_laboratory_equipment_01_acceptance",
    "23_laboratory_equipment_03_liability",
    "24_building_inspection_01_payment",
    "24_building_inspection_02_liability",
    "24_building_inspection_03_保密",
    "25_open_source_support_01_acceptance",
}
EXCLUDE_OCR = {
    "02_unified_communications_01_acceptance",
    "02_unified_communications_03_liability",
    "12_medical_equipment_02_payment",
}
EXCLUDE_CONTEXT = {
    "01_database_maintenance_01_acceptance",
    "01_database_maintenance_02_payment",
    "03_middleware_maintenance_01_acceptance",
    "03_middleware_maintenance_02_payment",
    "05_system_upgrade_01_acceptance",
    "06_system_optimization_01_acceptance",
    "10_cost_consulting_01_保密",
    "11_accounting_platform_02_payment",
    "13_desktop_cloud_01_acceptance",
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
        elif sid in EXCLUDE_CONTEXT:
            item.update({
                "split": "exclude",
                "label": "exclude_insufficient_context",
                "reason": "当前摘录不足以区分完整合同缺项与正常交叉引用，保守排除以免制造伪负例。",
                "gold_articles": [],
            })
        elif sid in PAYMENT_EXEMPTION:
            item.update({
                "split": "positive",
                "label": "needs_fact_review_payment_exemption",
                "reason": "付款迟延时免除一方违约责任的约定，需要结合付款条件、原因与适用采购规则核查。",
                "gold_articles": [[LAW, 577], [LAW, 579]],
                "query": "未支付价款 报酬 金钱债务 违约责任",
                "annotation_status": "single_pass_curated",
            })
        elif sid in PENALTY_SECTIONS:
            item.update({
                "split": "positive",
                "label": "needs_fact_review_penalty",
                "reason": "条款包含按日或按总价计收的违约金、责任叠加或明显不对等，需要结合实际损失等事实核查。",
                "gold_articles": [[LAW, 585]],
                "query": "违约金 过分高于损失 调整",
                "annotation_status": "single_pass_curated",
            })
        elif sid in NEGATIVE_CURATED:
            item.update({
                "split": "negative_curated",
                "label": "no_obvious_risk_on_text",
                "reason": "当前文本为常规履约、价款、验收或保密安排，未见可定位的实质风险信号；可优化不等于需重点核查。",
                "gold_articles": [],
                "annotation_status": "single_pass_curated",
            })
        else:
            item.update({
                "split": "exclude",
                "label": "exclude_unresolved_review",
                "reason": "单轮策展仍有合理分歧，保守排除；需独立法律复核后再进入计分集。",
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
            "annotation_status": "single_pass_curated",
        })
    payload = {
        "version": "v0.4-curated-risk-threshold",
        "label_definition": "重点核查需求，不是法律效力或责任的最终结论。",
        "annotation_limit": "单轮策展标注，不等同于双人律师复核金标。分歧、上下文不足和 OCR 破损样本均排除。",
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
