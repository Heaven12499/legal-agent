from backend.scripts.build_public_clause_benchmark import article_sections, pick_section


def test_section_parser_supports_scanned_contract_numbering_without_splitting_dates():
    text = (
        "4安装、调试和验收\n"
        "乙方完成安装后，甲方应在10日内组织验收，并签署书面验收文件。"
        "验收不合格的，乙方应在收到通知后整改。\n"
        "6付款条件\n"
        "验收合格后，甲方在收到发票之日起10日内支付全部价款。"
        "财政资金延误时，双方另行协商付款期限。乙方应当提交合法有效的发票、"
        "验收材料和付款申请，甲方审核材料完整后办理支付；材料不完整的，"
        "应一次性告知乙方补正。\n"
        "9违约责任\n"
        "乙方逾期交付的，应按日承担违约金；违约金不足以弥补损失的，"
        "还应赔偿实际损失。"
    )

    sections = article_sections(text)

    assert len(sections) == 3
    payment = pick_section(sections, ("付款", "支付"), set())
    assert payment is not None
    assert "10日内支付全部价款" in payment
    assert "9违约责任" not in payment
