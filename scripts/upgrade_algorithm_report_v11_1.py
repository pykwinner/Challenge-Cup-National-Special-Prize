from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
SOURCE = next(ROOT.glob("*算法增强版V2.docx"))
OUTPUT = ROOT / "为什么数学建模能够支撑供热转型决策_算法增强版V4.docx"
FIGURE = ROOT / "outputs" / "v11_1_figures" / "metering_annual_replay_workflow.png"
DASHBOARD = ROOT / "outputs" / "v11_1_figures" / "metering_demo_dashboard.png"
PREFLIGHT_DASHBOARD = ROOT / "outputs" / "v11_2_figures" / "annual_preflight_dashboard.png"


def set_run_font(run, *, size=None, bold=None, color=None):
    run.font.name = "Calibri"
    rpr = run._element.get_or_add_rPr()
    fonts = rpr.rFonts
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    fonts.set(qn("w:ascii"), "Calibri")
    fonts.set(qn("w:hAnsi"), "Calibri")
    fonts.set(qn("w:eastAsia"), "Noto Sans SC")
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if color is not None:
        run.font.color.rgb = RGBColor.from_string(color)


def move_before(paragraph, anchor):
    anchor._p.addprevious(paragraph._p)
    return paragraph


def add_paragraph_before(doc, anchor, text="", style=None, align=None):
    paragraph = doc.add_paragraph(style=style)
    if text:
        paragraph.add_run(text)
    if align is not None:
        paragraph.alignment = align
    return move_before(paragraph, anchor)


def set_table_geometry(table, widths):
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), str(sum(widths)))
    tbl_ind = tbl_pr.find(qn("w:tblInd"))
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:type"), "dxa")
    tbl_ind.set(qn("w:w"), "120")
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        for cell, width in zip(row.cells, widths):
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:type"), "dxa")
            tc_w.set(qn("w:w"), str(width))
            margins = tc_pr.find(qn("w:tcMar"))
            if margins is None:
                margins = OxmlElement("w:tcMar")
                tc_pr.append(margins)
            for side, value in (("top", 90), ("bottom", 90), ("start", 120), ("end", 120)):
                node = margins.find(qn(f"w:{side}"))
                if node is None:
                    node = OxmlElement(f"w:{side}")
                    margins.append(node)
                node.set(qn("w:w"), str(value))
                node.set(qn("w:type"), "dxa")
    header_pr = table.rows[0]._tr.get_or_add_trPr()
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    header_pr.append(repeat)


def shade(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def replace_version_text(doc):
    containers = list(doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                containers.extend(cell.paragraphs)
    for paragraph in containers:
        for run in paragraph.runs:
            if "V10" in run.text:
                run.text = run.text.replace("V10", "V11.2")

    benchmark_updates = {
        "C1 乌鲁木齐": ("0.0618 s", "HiGHS 最优；旧核心指标最大差 0"),
        "C2 固原": ("0.0488 s", "HiGHS 最优；最大差 1.63×10⁻⁹"),
        "C3 武汉": ("0.0435 s", "HiGHS 最优；旧核心指标最大差 0"),
    }
    for table in doc.tables:
        for row in table.rows[1:]:
            key = row.cells[0].text.strip()
            if key not in benchmark_updates or len(row.cells) < 4:
                continue
            build_time, evidence = benchmark_updates[key]
            row.cells[2].text = build_time
            row.cells[3].text = evidence
            for cell in (row.cells[2], row.cells[3]):
                for run in cell.paragraphs[0].runs:
                    set_run_font(run, size=9.0)


def add_upgrade_section(doc):
    anchor = next(
        paragraph
        for paragraph in doc.paragraphs
        if paragraph.text.strip().startswith("3  为什么该模型具有工程物理可信性")
    )
    add_paragraph_before(
        doc,
        anchor,
        "2.7  V11.2：供热计量、年度数据准入与固定投资连续回放",
        "Heading 2",
    )
    add_paragraph_before(
        doc,
        anchor,
        "V11.2把“推进供热计量改造和按热量收费”转化为可配置的需求侧决策，同时把代表时段规划、全年数据准入和完整年度运行严格分开。代表时段允许选择资产，连续年度只允许调度已建资产；在两次求解之前，系统先检查时序完整性和代表时段覆盖偏差。国家宏观表述只形成机制映射；覆盖率、节热率、热价和户数仍须由地方政策与项目数据校准。",
        "Normal",
    )

    picture = doc.add_paragraph()
    picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
    picture.paragraph_format.keep_with_next = True
    shape = picture.add_run().add_picture(str(FIGURE), width=Inches(6.25))
    shape._inline.docPr.set(
        "descr", "V11.1需求侧计量与固定投资连续回放四阶段闭环图"
    )
    shape._inline.docPr.set(
        "title", "V11.1需求侧计量与固定投资连续回放闭环"
    )
    move_before(picture, anchor)
    caption = add_paragraph_before(
        doc,
        anchor,
        "图 5  V11.1需求侧计量与固定投资连续回放闭环",
        "Caption" if "Caption" in doc.styles else "Normal",
        WD_ALIGN_PARAGRAPH.CENTER,
    )
    caption.paragraph_format.space_after = Pt(8)

    add_paragraph_before(doc, anchor, "2.7.1  计量覆盖率的线性投资模型", "Heading 3")
    add_paragraph_before(
        doc,
        anchor,
        "令 c⁰ₙ 为节点既有计量覆盖率，xᵐₙ 为新增覆盖率，η⁽ˢ⁾ₙ 为完整覆盖时经审计的节热响应。覆盖率满足 0≤xᵐₙ≤x̄ᵐₙ、c⁰ₙ+xᵐₙ≤1；节点有效负荷为 Dᵉᶠᶠₙgt=Dₙgt[1−η⁽ˢ⁾ₙ(c⁰ₙ+xᵐₙ)]。Dₙgt 是输入常数，因此新增覆盖率只与常数相乘，主问题仍为 MILP。",
        "Normal",
    )
    equation = add_paragraph_before(
        doc,
        anchor,
        "Qˢᵘᵖₙgt + η⁽ˢ⁾ₙDₙgt xᵐₙ = Dₙgt(1 − η⁽ˢ⁾ₙc⁰ₙ)",
        "Normal",
        WD_ALIGN_PARAGRAPH.CENTER,
    )
    equation.paragraph_format.keep_together = True

    add_paragraph_before(doc, anchor, "2.7.2  覆盖率目标与居民热费", "Heading 3")
    add_paragraph_before(
        doc,
        anchor,
        "系统覆盖率采用基准热需求加权：ρᵐ=ΣwₜDₙgt(c⁰ₙ+xᵐₙ)/ΣwₜDₙgt。只有地方规划或项目任务提供一致口径目标时，才启用 ρᵐ≥ρ̲ᵐ。给定情景热价 pʰ、每户固定费 Fʰ 和户数 Nʰ 后，按完整计划服务量计算的年度指示性热费为 Bʰ=pʰ(D−S)+FʰNʰ，并可设置 Bʰ/Nʰ≤B̄ʰ。该口径不能靠主动失供降低账单，也不等于系统平准成本。",
        "Normal",
    )

    add_paragraph_before(doc, anchor, "2.7.3  代表时段到8760小时的两层算法", "Heading 3")
    steps = [
        "在保序代表时段和极端日上联合优化共享投资 x 与场景运行 qᴿ；",
        "将热源扩建/退役、储热、管径、管网和末端改造、配电、微网、生物质库存及计量覆盖率写回固定配置；",
        "把所有候选投资上限清零，并输出逐资产 freeze_ok 审计；",
        "在连续8760/8784小时上保留原可靠性约束，仅重新优化运行 qʸ；",
        "若失供、SOC漂移或KPI偏差超限，则返回代表日、可靠容量或候选层修改，而不是在回放阶段补建。",
    ]
    for text in steps:
        add_paragraph_before(doc, anchor, text, "List Number")
    equation = add_paragraph_before(
        doc,
        anchor,
        "qʸ* = arg min { Cʸᵒᵖ(qʸ) : qʸ ∈ Q(x*, Tʸ) }，且 x = x*",
        "Normal",
        WD_ALIGN_PARAGRAPH.CENTER,
    )
    equation.paragraph_format.keep_together = True

    add_paragraph_before(doc, anchor, "2.7.4  全年数据准入与代表时段偏差", "Heading 3")
    add_paragraph_before(
        doc,
        anchor,
        "正式回放在创建MILP之前检查时间戳解析、重复/缺失间隔、配置步长、模型引用列、空值、无穷值和负负荷，并要求连续覆盖8760或8784小时。供暖年可以从任意日期开始；是否按自然年对齐单独报告。代表时段再与全年逐列比较年能量、负荷峰值、最低温、最低资源可用率和最大爬坡。",
        "Normal",
    )
    equation = add_paragraph_before(
        doc,
        anchor,
        "δᴱⱼ=(Eᴿⱼ−Eʸⱼ)/|Eʸⱼ|，ρᵖᵉᵃᵏⱼ=max Dᴿⱼ/max Dʸⱼ，ρʳᵃᵐᵖⱼ=max|ΔDᴿⱼ|/max|ΔDʸⱼ|",
        "Normal",
        WD_ALIGN_PARAGRAPH.CENTER,
    )
    equation.paragraph_format.keep_together = True
    preflight_figure = doc.add_paragraph()
    preflight_figure.alignment = WD_ALIGN_PARAGRAPH.CENTER
    preflight_figure.paragraph_format.keep_with_next = True
    shape = preflight_figure.add_run().add_picture(
        str(PREFLIGHT_DASHBOARD), width=Inches(6.25)
    )
    shape._inline.docPr.set(
        "descr", "全年数据准入状态与代表时段能量峰值爬坡覆盖审计看板"
    )
    shape._inline.docPr.set("title", "V11.2全年数据准入与代表时段覆盖审计")
    move_before(preflight_figure, anchor)
    caption = add_paragraph_before(
        doc,
        anchor,
        "图 7  合成8760小时数据的准入与代表时段覆盖审计",
        "Caption" if "Caption" in doc.styles else "Normal",
        WD_ALIGN_PARAGRAPH.CENTER,
    )
    caption.paragraph_format.space_after = Pt(8)
    add_paragraph_before(
        doc,
        anchor,
        "预检状态分为两层：chronology_ready_for_formal_replay表示年度数据可进入正式回放；representative_coverage_requires_review表示代表时段仍有需要解释的压缩偏差。后者不阻止回放，因为回放本身用于量化偏差，但必须在报告中披露。",
        "Normal",
    )

    add_paragraph_before(doc, anchor, "2.7.5  新增变量—证据映射", "Heading 3")
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    headers = ["算法层", "核心机制", "机器可读输出", "必须声明的口径"]
    for index, value in enumerate(headers):
        cell = table.rows[0].cells[index]
        cell.text = value
        shade(cell, "E8EEF5")
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in cell.paragraphs[0].runs:
            set_run_font(run, size=9.2, bold=True, color="17324D")
    rows = [
        ("需求侧投资", "既有/新增计量覆盖率", "heat_metering_coverage_fraction", "覆盖率需地方数据"),
        ("用户响应", "覆盖率驱动线性节热", "metered_heat_savings_kWh", "节热率需对照校准"),
        ("热费情景", "热价、固定费、户数、账单上限", "average_household_heat_bill", "不等于平准成本"),
        ("投资冻结", "全部候选上限清零", "all_investments_frozen", "压力测试与正式回放分开"),
        ("连续时序", "保序运行与跨季节SOC", "is_full_year_horizon", "短时序不能称全年复核"),
    ]
    for row_data in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row_data):
            cells[index].text = value
            cells[index].paragraphs[0].alignment = (
                WD_ALIGN_PARAGRAPH.CENTER if index != 1 else WD_ALIGN_PARAGRAPH.LEFT
            )
            for run in cells[index].paragraphs[0].runs:
                set_run_font(run, size=8.8)
    set_table_geometry(table, [1600, 2850, 2750, 2160])
    anchor._p.addprevious(table._tbl)
    dashboard = doc.add_paragraph()
    dashboard.alignment = WD_ALIGN_PARAGRAPH.CENTER
    dashboard.paragraph_format.keep_with_next = True
    shape = dashboard.add_run().add_picture(str(DASHBOARD), width=Inches(6.2))
    shape._inline.docPr.set(
        "descr", "供热计量覆盖率、节热量、节点改造和户均热费四联结果看板"
    )
    shape._inline.docPr.set("title", "V11.1供热计量与按热收费情景结果看板")
    move_before(dashboard, anchor)
    caption = add_paragraph_before(
        doc,
        anchor,
        "图 6  计量演示的覆盖率、节热量、节点改造与户均热费",
        "Caption" if "Caption" in doc.styles else "Normal",
        WD_ALIGN_PARAGRAPH.CENTER,
    )
    caption.paragraph_format.space_after = Pt(8)
    add_paragraph_before(
        doc,
        anchor,
        "当前计量示例达到60%覆盖率、年节热35.39万kWh且零失供；168小时连续回放的全部投资余量为0。合成8760小时数据通过年度准入，并识别出四个负荷列年能量低估约14.2%。全工程57项自动化测试通过，三个公共代表日场景均保持最优与零失供。合成序列只证明软件流程，三类场景仍需真实8760小时数据形成正式跨季节证据。",
        "Normal",
    )


def append_policy_references(doc):
    refs = [
        "国务院. 中华人民共和国国民经济和社会发展第十五个五年规划纲要[Z]. 2026-03-13. https://www.gov.cn/yaowen/liebiao/202603/content_7062633.htm",
        "国务院. “十五五”碳达峰行动方案[Z]. 2026-07-05. https://www.mee.gov.cn/zcwj/gwywj/202607/t20260714_1161655.shtml",
    ]
    for text in refs:
        paragraph = doc.add_paragraph(text, style="Normal")
        paragraph.paragraph_format.left_indent = Inches(0.2)
        paragraph.paragraph_format.first_line_indent = Inches(-0.2)
        paragraph.paragraph_format.space_after = Pt(4)


def main():
    for figure in (FIGURE, DASHBOARD, PREFLIGHT_DASHBOARD):
        if not figure.exists():
            raise FileNotFoundError(f"先生成算法图：{figure}")
    doc = Document(SOURCE)
    replace_version_text(doc)
    add_upgrade_section(doc)
    append_policy_references(doc)
    doc.core_properties.title = "为什么数学建模能够支撑供热转型决策——算法增强版V4"
    doc.core_properties.subject = "V11.2供热计量、全年数据准入与固定投资连续回放"
    doc.core_properties.version = "11.2"
    doc.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
