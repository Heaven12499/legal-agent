# -*- coding: utf-8 -*-
"""下载官方公开合同附件，抽取文本并生成二次脱敏的评测样本。

原始 PDF 只写入 tmp/pdfs/public_contracts/ 作为临时中间产物；最终仅保留
sample_contracts/public_disclosed_deidentified/ 下的脱敏文本和来源元数据。

这些样本来自依法公开的政府采购合同公告，并非企业授权的私有脱敏合同。
运行：python -m backend.scripts.import_public_contracts
"""
import html
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from pypdf import PdfReader


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "sample_contracts" / "public_disclosed_deidentified"
RAW_DIR = PROJECT_ROOT / "tmp" / "pdfs" / "public_contracts"

# 每页均为中国政府采购网的合同公告，页面内声明并链接合同 PDF 附件。
SOURCES = [
    ("01_database_maintenance", "软件维保服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202511/t20251105_25639805.htm"),
    ("02_unified_communications", "信息技术服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202510/t20251010_25471878.htm"),
    ("03_middleware_maintenance", "中间件维保服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202510/t20251027_25580862.htm"),
    ("04_middleware_support", "技术支持服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202512/t20251229_26008453.htm"),
    ("05_system_upgrade", "信息系统升级服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202512/t20251205_25870644.htm"),
    ("06_system_optimization", "软件开发服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202510/t20251027_25580876.htm"),
    ("07_terminal_procurement", "IT 设备采购", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202601/t20260106_26041699.htm"),
    ("08_ntp_procurement", "网络设备采购", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202501/t20250102_24006974.htm"),
    ("09_facility_repair", "维修工程", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202604/t20260421_26437210.htm"),
    ("10_cost_consulting", "工程造价咨询服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202604/t20260423_26449043.htm"),
    ("11_accounting_platform", "软件开发服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202606/t20260603_26677438.htm"),
    ("12_medical_equipment", "医疗设备采购", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202502/t20250220_24191705.htm"),
    ("13_desktop_cloud", "信息技术运维服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202505/t20250523_24650626.htm"),
    ("14_software_outsourcing", "软件人力外包服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202502/t20250219_24187003.htm"),
    ("15_customs_system_development", "软件开发服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202511/t20251119_25725994.htm"),
    ("16_customs_validation", "软件开发服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202512/t20251204_25858022.htm"),
    ("17_on_site_service", "驻场服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202512/t20251225_25989588.htm"),
    ("18_architecture_compliance", "软件开发服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202512/t20251226_25994759.htm"),
    ("19_image_recognition", "人工智能技术服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202511/t20251111_25671307.htm"),
    ("20_express_clearance", "软件开发服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202512/t20251225_25989601.htm"),
    ("21_food_supply", "食材采购配送", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202609/t20260911_27315595.htm"),
    ("22_museum_repair", "维修工程", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202608/t20260804_27063861.htm"),
    ("23_laboratory_equipment", "实验室设备采购", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202607/t20260714_26931362.htm"),
    ("24_building_inspection", "房屋安全鉴定服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202607/t20260710_26909633.htm"),
    ("25_open_source_support", "信息技术运维服务", "https://www.ccgp.gov.cn/cggg/zygg/qtgg/202510/t20251015_25509095.htm"),
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def fetch(url: str, referer: str | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read()


def download_url(page_html: str) -> str:
    """政府采购网前端用附件 id 拼接该下载地址，直接复用公开接口。"""
    match = re.search(r"class=['\"]bizDownload['\"][^>]*\bid=['\"]([^'\"]+)", page_html)
    if not match:
        raise ValueError("未找到合同附件标识")
    return f"https://download.ccgp.gov.cn/oss/download?uuid={match.group(1)}"


def page_title(page_html: str) -> str:
    match = re.search(r"<meta name=\"ArticleTitle\" content=\"([^\"]+)", page_html)
    return html.unescape(match.group(1)) if match else "公开合同公告"


def extract_pdf_text(pdf_path: Path, max_pages: int | None = None) -> str:
    reader = PdfReader(str(pdf_path))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    extracted = "\n\n".join(p for p in pages if p)
    if len(extracted) >= 800:
        return extracted
    return extract_with_ocr(pdf_path, max_pages=max_pages)


def extract_with_ocr(pdf_path: Path, max_pages: int | None = None) -> str:
    """扫描版 PDF 的本地 OCR 回退；不把文档发送到第三方服务。"""
    deps_dir = PROJECT_ROOT / "tmp" / "ocr_deps"
    if str(deps_dir) not in sys.path:
        sys.path.insert(0, str(deps_dir))
    from rapidocr_onnxruntime import RapidOCR

    poppler = shutil.which("pdftoppm")
    if not poppler:
        raise RuntimeError("未找到 pdftoppm，无法渲染扫描版 PDF")
    image_dir = RAW_DIR / f"{pdf_path.stem}_pages"
    image_dir.mkdir(parents=True, exist_ok=True)
    prefix = image_dir / "page"
    command = [poppler, "-r", "180", "-png"]
    if max_pages:
        command.extend(["-f", "1", "-l", str(max_pages)])
    command.extend([str(pdf_path), str(prefix)])
    subprocess.run(
        command,
        check=True, capture_output=True,
    )
    ocr = RapidOCR()
    pages = []
    try:
        images = sorted(image_dir.glob("page-*.png"))
        if max_pages:
            images = images[:max_pages]
        for image in images:
            result, _ = ocr(str(image))
            lines = [row[1] for row in (result or []) if len(row) >= 2 and row[1].strip()]
            pages.append("\n".join(lines))
    finally:
        shutil.rmtree(image_dir, ignore_errors=True)
    return "\n\n".join(pages)


def _collect_party_names(text: str) -> dict[str, set[str]]:
    names = {"甲方A": set(), "乙方B": set()}
    patterns = {
        "甲方A": r"(?:甲方|采购人|委托人)(?:[（(][^）)]{0,20}[）)])?\s*[：:]\s*([^\n：:]{2,80})",
        "乙方B": r"(?:乙方|供应商|受托人|咨询人)(?:[（(][^）)]{0,20}[）)])?\s*[：:]\s*([^\n：:]{2,80})",
    }
    for replacement, pattern in patterns.items():
        for value in re.findall(pattern, text):
            value = re.split(r"\s{2,}|[，,]?(?:地址|统一社会信用代码|法定代表人|联系人)[：:]", value)[0].strip()
            if 2 <= len(value) <= 60 and not re.search(r"[。；;]", value):
                names[replacement].add(value)
    return names


def redact_text(text: str) -> str:
    """面向评测语料的保守脱敏：保留权利义务条款，移除识别主体的字段。

    规则不能替代人工复核；文本中未被规则识别出的专有名词仍需在发布前检查。
    """
    text = text.replace("\u00a0", " ").replace("\r", "")
    party_names = _collect_party_names(text)

    # 先按字段脱敏，再以已识别的主体名称做全文替换，避免合同正文反复出现主体名。
    field_patterns = [
        (r"(?im)^(\s*(?:甲方|采购人|委托人)(?:[（(][^）)]{0,20}[）)])?\s*[：:].*)$", "甲方：甲方A"),
        (r"(?im)^(\s*(?:乙方|供应商|受托人|咨询人)(?:[（(][^）)]{0,20}[）)])?\s*[：:].*)$", "乙方：乙方B"),
        (r"(?im)^\s*(?:地址|通讯地址|联系地址|送达地址)\s*[：:].*$", "地址：【已脱敏】"),
        (r"(?im)^\s*(?:联系人|联络人|项目联系人|法定代表人|委托代理人|授权代表|经办人)\s*[：:].*$", "联系人：【已脱敏】"),
        (r"(?im)^\s*(?:联系电话|电话|手机|传真|电子邮箱|邮箱|E-?mail)\s*[：:].*$", "联系方式：【已脱敏】"),
        (r"(?im)^\s*(?:开户行|开户银行|银行账号|账号|统一社会信用代码|纳税人识别号)\s*[：:].*$", "账户/证照信息：【已脱敏】"),
        (r"(?im)^\s*(?:项目编号|合同编号|采购编号|招标编号)\s*[：:].*$", "编号：【已脱敏】"),
        (r"(?im)^\s*(?:项目名称|合同名称)\s*[：:].*$", "项目名称：【已脱敏】"),
    ]
    for pattern, replacement in field_patterns:
        text = re.sub(pattern, replacement, text)

    for replacement, values in party_names.items():
        for value in sorted(values, key=len, reverse=True):
            text = text.replace(value, replacement)

    # 行内敏感数据，保留法律条款中的金额、期限等可评测信息。
    text = re.sub(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", "【已脱敏邮箱】", text)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "【已脱敏手机号】", text)
    text = re.sub(r"(?<!\d)0\d{2,3}-?\d{7,8}(?:-\d{1,6})?(?!\d)", "【已脱敏电话】", text)
    text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "【已脱敏证件号】", text)
    text = re.sub(r"(?<!\d)\d{15}(?!\d)", "【已脱敏证件号】", text)
    text = re.sub(r"(?<!\d)\d{16,22}(?!\d)", "【已脱敏账号】", text)

    # OCR 水印和断行字段常绕过“字段名：值”的常规格式，按行再做一次保守过滤。
    cleaned_lines = []
    for line in text.splitlines():
        compact_line = re.sub(r"\s+", "", line)
        if any(token in compact_line for token in (
            "仅供", "公示使用", "公示使", "示使用", "国际招", "限公司", "有限公",
        )):
            continue
        if any(token in compact_line for token in (
            "地址", "电话", "联系方式", "联系人", "开户", "账户", "账号",
            "统一社会信用代码", "法定代表人", "委托代理人",
        )):
            # 不保留可能与前后行拼接的地址/账户碎片。
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    # 公开项目名称也可反向定位主体；评测只需要条款结构，故统一泛化。
    text = re.sub(r"《[^》]{0,140}(?:项目|合同)[^》]{0,80}》", "《附件技术需求》", text)
    text = re.sub(r"甲方A\s*\d{4}[^。\n]{0,80}(?:项目|合同)", "甲方A项目", text)
    # 正文中的场所简称可能未出现在“甲方：”字段中，无法被主体名称收集规则命中。
    text = re.sub(r"(?:全国)?海关信息中心(?:综合楼)?", "甲方指定服务场所内", text)

    # PDF 提取常造成大量空行，规整后更适合作为 RAG 输入。
    lines = [line.strip() for line in text.splitlines()]
    out = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip() + "\n"


def contains_obvious_pii(text: str) -> list[str]:
    checks = {
        "email": r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b",
        "mobile": r"(?<!\d)1[3-9]\d{9}(?!\d)",
        "landline": r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)",
        "id_number": r"(?<!\d)\d{17}[\dXx](?!\d)",
        "long_account": r"(?<!\d)\d{16,22}(?!\d)",
    }
    return [name for name, pattern in checks.items() if re.search(pattern, text)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="*", help="只处理指定样本 id")
    parser.add_argument("--max-pages", type=int, help="扫描 PDF 最多 OCR 的页数")
    parser.add_argument(
        "--from-raw", action="store_true",
        help="使用 tmp/pdfs/public_contracts 中已下载的 PDF，跳过网页请求",
    )
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    sources_path = OUT_DIR / "sources.json"
    previous = json.loads(sources_path.read_text(encoding="utf-8")) if sources_path.exists() else {}
    manifest_by_id = {item["id"]: item for item in previous.get("samples", [])}
    failures_by_id = {item["id"]: item for item in previous.get("failures", [])}

    selected = [s for s in SOURCES if not args.ids or s[0] in args.ids]
    for sample_id, contract_type, source_url in selected:
        try:
            raw_path = RAW_DIR / f"{sample_id}.pdf"
            if args.from_raw:
                if not raw_path.exists():
                    raise FileNotFoundError(f"缺少已下载附件：{raw_path}")
                title = "公开合同公告"
            else:
                page = fetch(source_url).decode("utf-8", errors="replace")
                pdf_url = download_url(page)
                raw_path.write_bytes(fetch(pdf_url, referer=source_url))
                title = page_title(page)
            extracted = extract_pdf_text(raw_path, max_pages=args.max_pages)
            if len(extracted) < 800:
                raise ValueError(f"PDF 可提取文本过少（{len(extracted)} 字符），疑似扫描件或下载异常")
            redacted = redact_text(extracted)
            residual = contains_obvious_pii(redacted)
            if residual:
                raise ValueError(f"自动脱敏后仍检测到：{', '.join(residual)}")
            out_path = OUT_DIR / f"{sample_id}.txt"
            header = (
                "# 公开披露合同的二次脱敏文本\n"
                "# 仅用于法律 RAG 评测；不构成法律意见。\n"
                "# 已替换主体、联系人、联系方式、地址、账户/证照、项目编号等识别信息。\n"
                "# 公开来源链接仅用于可追溯核验，不应随面向最终用户的语料库一同暴露。\n\n"
            )
            out_path.write_text(header + redacted, encoding="utf-8")
            manifest_by_id[sample_id] = {
                "id": sample_id,
                "contract_type": contract_type,
                "source": "中国政府采购网（官方公开合同公告）",
                "source_url": source_url,
                "title": title,
                "output": str(out_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "characters": len(redacted),
                "redaction": "rule_based_v1 + residual_pattern_check",
                "review_required": True,
            }
            failures_by_id.pop(sample_id, None)
            print(f"[OK] {sample_id}: {len(redacted)} chars")
        except Exception as exc:  # noqa: BLE001 -- 汇总失败样本，方便替换来源
            failures_by_id[sample_id] = {
                "id": sample_id, "source_url": source_url, "error": str(exc),
            }
            print(f"[FAIL] {sample_id}: {exc}")

    manifest = sorted(manifest_by_id.values(), key=lambda item: item["id"])
    failures = sorted(failures_by_id.values(), key=lambda item: item["id"])
    sources_path.write_text(
        json.dumps({"samples": manifest, "failures": failures}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failures:
        selected_failures = [item for item in failures if item["id"] in {row[0] for row in selected}]
        if selected_failures:
            raise SystemExit(
                f"本次完成 {len(selected) - len(selected_failures)}/{len(selected)} 份；"
                "失败详情见 sources.json"
            )
    # 原始公开 PDF 只作为本次中间产物，成功后立即删除，避免误入版本库。
    shutil.rmtree(RAW_DIR)
    print(f"完成：{len(manifest)} 份脱敏文本写入 {OUT_DIR}")


if __name__ == "__main__":
    main()
