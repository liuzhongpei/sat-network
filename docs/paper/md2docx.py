# -*- coding: utf-8 -*-
"""将 Markdown 论文转换为 Word (.docx)。图片用 figures/png/*.png 嵌入。"""
import re, os
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

HERE = os.path.dirname(os.path.abspath(__file__))
MD = os.path.join(HERE, "星载路由器数字样机及星座仿真验证.md")
OUT = os.path.join(HERE, "星载路由器数字样机及星座仿真验证.docx")

EAST = "宋体"
LATIN = "Times New Roman"

doc = Document()

# 全局默认字体
normal = doc.styles["Normal"]
normal.font.name = LATIN
normal.font.size = Pt(10.5)
normal.element.rPr.rFonts.set(qn("w:eastAsia"), EAST)

for hname, sz in (("Heading 1", 15), ("Heading 2", 13), ("Title", 17)):
    st = doc.styles[hname]
    st.font.name = LATIN
    st.font.color.rgb = RGBColor(0, 0, 0)
    st.font.size = Pt(sz)
    st.font.bold = True
    try:
        st.element.rPr.rFonts.set(qn("w:eastAsia"), EAST)
    except Exception:
        pass


def set_font(run, size=10.5, bold=False, italic=False, name=LATIN, east=EAST, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run._element.rPr.rFonts.set(qn("w:eastAsia"), east)
    if color is not None:
        run.font.color.rgb = color


TOKEN = re.compile(r'(\*\*.+?\*\*|\*[^*\s].*?\*|`.+?`|\$[^$]+?\$)')


def add_rich(par, text, size=10.5):
    pos = 0
    for m in TOKEN.finditer(text):
        if m.start() > pos:
            set_font(par.add_run(text[pos:m.start()].replace("  ", "　")), size=size)
        tok = m.group(1)
        if tok.startswith("**"):
            set_font(par.add_run(tok[2:-2]), size=size, bold=True)
        elif tok.startswith("`"):
            set_font(par.add_run(tok[1:-1]), size=size, name="Consolas", east=EAST)
        elif tok.startswith("$"):
            set_font(par.add_run(tok[1:-1]), size=size)
        else:  # *italic*
            set_font(par.add_run(tok[1:-1]), size=size, italic=True)
        pos = m.end()
    if pos < len(text):
        set_font(par.add_run(text[pos:].replace("  ", "　")), size=size)


def add_image(path):
    p = path.replace("figures/fig", "figures/png/fig").replace(".svg", ".png")
    abs_p = os.path.join(HERE, p)
    if os.path.exists(abs_p):
        par = doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = par.add_run()
        run.add_picture(abs_p, width=Inches(5.9))
    else:
        par = doc.add_paragraph()
        set_font(par.add_run("（图缺失：%s）" % abs_p), size=9, color=RGBColor(0x99, 0x99, 0x99))


lines = open(MD, encoding="utf-8").read().splitlines()
i = 0
n = len(lines)
first_heading_done = False

while i < n:
    line = lines[i]
    s = line.strip()

    # 表格
    if s.startswith("|"):
        rows = []
        while i < n and lines[i].strip().startswith("|"):
            rows.append(lines[i].strip())
            i += 1
        cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
        # 去掉分隔行
        data = [cells[0]] + [r for r in cells[1:] if not all(re.match(r'^-+$', c) for c in r)]
        table = doc.add_table(rows=len(data), cols=len(data[0]))
        table.style = "Light Grid Accent 1"
        for ri, row in enumerate(data):
            for ci, val in enumerate(row):
                cell = table.cell(ri, ci)
                cell.text = ""
                par = cell.paragraphs[0]
                add_rich(par, val, size=9.5)
                if ri == 0:
                    for run in par.runs:
                        run.bold = True
        doc.add_paragraph()
        continue

    # 图片
    m = re.match(r'^!\[(.*?)\]\((.*?)\)$', s)
    if m:
        add_image(m.group(2))
        i += 1
        continue

    # 标题
    if s.startswith("### "):
        h = doc.add_heading(level=2)
        set_font(h.add_run(s[4:]), size=13)
        i += 1
        continue
    if s.startswith("## "):
        h = doc.add_heading(level=1)
        set_font(h.add_run(s[3:]), size=15)
        i += 1
        continue
    if s.startswith("# "):
        h = doc.add_heading(level=0)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_font(h.add_run(s[2:]), size=17)
        first_heading_done = True
        i += 1
        continue

    # 水平线
    if s == "---":
        i += 1
        continue

    # 空行
    if s == "":
        i += 1
        continue

    # 图表注（居中、略小、不加粗）
    if (s.startswith("**图 ") or s.startswith("**表 ")) and s.endswith("**"):
        par = doc.add_paragraph()
        par.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_font(par.add_run(s[2:-2]), size=9, color=RGBColor(0x44, 0x44, 0x44))
        i += 1
        continue

    # 列表
    mli = re.match(r'^([-•]|\d+\.)\s+(.*)$', s)
    if mli:
        par = doc.add_paragraph()
        par.paragraph_format.left_indent = Pt(18)
        add_rich(par, s, size=10.5)
        i += 1
        continue

    # 普通段落
    par = doc.add_paragraph()
    if s.startswith("**摘") or s.startswith("**关键"):
        par.paragraph_format.space_before = Pt(6)
    add_rich(par, s, size=10.5)
    i += 1

doc.save(OUT)
print("saved ->", OUT)