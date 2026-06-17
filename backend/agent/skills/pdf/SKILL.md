---
name: pdf
description: Use this skill whenever the user wants to do anything with PDF files. This includes reading or extracting text/tables from PDFs, combining or merging multiple PDFs into one, splitting PDFs apart, rotating pages, adding watermarks, creating new PDFs, filling PDF forms, encrypting/decrypting PDFs, extracting images, and OCR on scanned PDFs to make them searchable. If the user mentions a .pdf file or asks to produce one, use this skill.
license: Proprietary. LICENSE.txt has complete terms
---

# PDF Processing Guide

## Overview

This guide covers essential PDF processing operations using Python libraries and command-line tools. For advanced features, JavaScript libraries, and detailed examples, see REFERENCE.md. If you need to fill out a PDF form, read FORMS.md and follow its instructions.

## Quick Start

```python
from pypdf import PdfReader, PdfWriter

# Read a PDF
reader = PdfReader("document.pdf")
print(f"Pages: {len(reader.pages)}")

# Extract text
text = ""
for page in reader.pages:
    text += page.extract_text()
```

## Python Libraries

### pypdf - Basic Operations

#### Merge PDFs
```python
from pypdf import PdfWriter, PdfReader

writer = PdfWriter()
for pdf_file in ["doc1.pdf", "doc2.pdf", "doc3.pdf"]:
    reader = PdfReader(pdf_file)
    for page in reader.pages:
        writer.add_page(page)

with open("merged.pdf", "wb") as output:
    writer.write(output)
```

#### Split PDF
```python
reader = PdfReader("input.pdf")
for i, page in enumerate(reader.pages):
    writer = PdfWriter()
    writer.add_page(page)
    with open(f"page_{i+1}.pdf", "wb") as output:
        writer.write(output)
```

#### Extract Metadata
```python
reader = PdfReader("document.pdf")
meta = reader.metadata
print(f"Title: {meta.title}")
print(f"Author: {meta.author}")
print(f"Subject: {meta.subject}")
print(f"Creator: {meta.creator}")
```

#### Rotate Pages
```python
reader = PdfReader("input.pdf")
writer = PdfWriter()

page = reader.pages[0]
page.rotate(90)  # Rotate 90 degrees clockwise
writer.add_page(page)

with open("rotated.pdf", "wb") as output:
    writer.write(output)
```

### pdfplumber - Text and Table Extraction

#### Extract Text with Layout
```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        print(text)
```

#### Extract Tables
```python
with pdfplumber.open("document.pdf") as pdf:
    for i, page in enumerate(pdf.pages):
        tables = page.extract_tables()
        for j, table in enumerate(tables):
            print(f"Table {j+1} on page {i+1}:")
            for row in table:
                print(row)
```

#### Advanced Table Extraction
```python
import pandas as pd

with pdfplumber.open("document.pdf") as pdf:
    all_tables = []
    for page in pdf.pages:
        tables = page.extract_tables()
        for table in tables:
            if table:  # Check if table is not empty
                df = pd.DataFrame(table[1:], columns=table[0])
                all_tables.append(df)

# Combine all tables
if all_tables:
    combined_df = pd.concat(all_tables, ignore_index=True)
    combined_df.to_excel("extracted_tables.xlsx", index=False)
```

### reportlab - Create PDFs

#### Basic PDF Creation
```python
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

c = canvas.Canvas("hello.pdf", pagesize=letter)
width, height = letter

# Add text
c.drawString(100, height - 100, "Hello World!")
c.drawString(100, height - 120, "This is a PDF created with reportlab")

# Add a line
c.line(100, height - 140, 400, height - 140)

# Save
c.save()
```

#### Create PDF with Multiple Pages
```python
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet

doc = SimpleDocTemplate("report.pdf", pagesize=letter)
styles = getSampleStyleSheet()
story = []

# Add content
title = Paragraph("Report Title", styles['Title'])
story.append(title)
story.append(Spacer(1, 12))

body = Paragraph("This is the body of the report. " * 20, styles['Normal'])
story.append(body)
story.append(PageBreak())

# Page 2
story.append(Paragraph("Page 2", styles['Heading1']))
story.append(Paragraph("Content for page 2", styles['Normal']))

# Build PDF
doc.build(story)
```

#### Subscripts and Superscripts

**IMPORTANT**: Never use Unicode subscript/superscript characters (₀₁₂₃₄₅₆₇₈₉, ⁰¹²³⁴⁵⁶⁷⁸⁹) in ReportLab PDFs. The built-in fonts do not include these glyphs, causing them to render as solid black boxes.

Instead, use ReportLab's XML markup tags in Paragraph objects:
```python
from reportlab.platypus import Paragraph
from reportlab.lib.styles import getSampleStyleSheet

styles = getSampleStyleSheet()

# Subscripts: use <sub> tag
chemical = Paragraph("H<sub>2</sub>O", styles['Normal'])

# Superscripts: use <super> tag
squared = Paragraph("x<super>2</super> + y<super>2</super>", styles['Normal'])
```

For canvas-drawn text (not Paragraph objects), manually adjust font the size and position rather than using Unicode subscripts/superscripts.

## Command-Line Tools

### pdftotext (poppler-utils)
```bash
# Extract text
pdftotext input.pdf output.txt

# Extract text preserving layout
pdftotext -layout input.pdf output.txt

# Extract specific pages
pdftotext -f 1 -l 5 input.pdf output.txt  # Pages 1-5
```

### qpdf
```bash
# Merge PDFs
qpdf --empty --pages file1.pdf file2.pdf -- merged.pdf

# Split pages
qpdf input.pdf --pages . 1-5 -- pages1-5.pdf
qpdf input.pdf --pages . 6-10 -- pages6-10.pdf

# Rotate pages
qpdf input.pdf output.pdf --rotate=+90:1  # Rotate page 1 by 90 degrees

# Remove password
qpdf --password=mypassword --decrypt encrypted.pdf decrypted.pdf
```

### pdftk (if available)
```bash
# Merge
pdftk file1.pdf file2.pdf cat output merged.pdf

# Split
pdftk input.pdf burst

# Rotate
pdftk input.pdf rotate 1east output rotated.pdf
```

## Batching — Only When Necessary

Generate the entire PDF in a single `python_repl_tool` call. Only split into batches if the script genuinely fails or times out mid-way (e.g. extremely large tables or many dense pages). If batching is needed, save partial PDFs and merge them at the end with `pypdf`.

## Common Tasks

### Extract Text from Scanned PDFs
```python
# Requires: pip install pytesseract pdf2image
import pytesseract
from pdf2image import convert_from_path

# Convert PDF to images
images = convert_from_path('scanned.pdf')

# OCR each page
text = ""
for i, image in enumerate(images):
    text += f"Page {i+1}:\n"
    text += pytesseract.image_to_string(image)
    text += "\n\n"

print(text)
```

### Add Watermark
```python
from pypdf import PdfReader, PdfWriter

# Create watermark (or load existing)
watermark = PdfReader("watermark.pdf").pages[0]

# Apply to all pages
reader = PdfReader("document.pdf")
writer = PdfWriter()

for page in reader.pages:
    page.merge_page(watermark)
    writer.add_page(page)

with open("watermarked.pdf", "wb") as output:
    writer.write(output)
```

### Extract Images
```bash
# Using pdfimages (poppler-utils)
pdfimages -j input.pdf output_prefix

# This extracts all images as output_prefix-000.jpg, output_prefix-001.jpg, etc.
```

## Formatting & Alignment — MANDATORY

Every PDF you produce must pass all of these checks before it is considered done. Do not skip any of them.

### Rules

**Margins**
- `leftMargin`, `rightMargin` = `0.75 * inch` minimum
- `topMargin` = `0.75 * inch` minimum
- `bottomMargin` = `0.75 * inch` minimum
- Never let content touch the edge of the page

**Spacing between elements**
- Always add `Spacer(1, 0.12*inch)` between every block (paragraph → table, table → paragraph, heading → body)
- Never place two `Paragraph` or `Table` objects back-to-back with no spacer
- Use `spaceBefore` and `spaceAfter` in `ParagraphStyle` definitions, not ad-hoc per element

**Font — use ReportLab's built-in Helvetica**

Use ReportLab's built-in `"Helvetica"` and `"Helvetica-Bold"` everywhere. They ship with ReportLab and require NO font registration, NO TTF file paths, NO external font dependencies. This keeps document generation reliable across any environment.

Available built-in faces:
- `"Helvetica"` — regular
- `"Helvetica-Bold"` — bold
- `"Helvetica-Oblique"` — italic
- `"Helvetica-BoldOblique"` — bold italic

Use these as `fontName` in all `ParagraphStyle` and `TableStyle` definitions.

**Text styles — define once, use everywhere**
## Image & Infographic Generation — USE image_gen_tool

**Never try to draw infographics manually with reportlab shapes.** Use `image_gen_tool` instead — it produces a real AI-generated image, uploads it to S3, and gives you a URL you can embed directly.

**When to use:**
- User asks for an infographic, banner, illustration, diagram, or any visual inside the PDF
- You want a visually rich header image, section illustration, or data summary graphic
- Any image that would take more than 5 lines of reportlab code to approximate

**How to use:**
1. Call `image_gen_tool` with a detailed prompt BEFORE building the PDF
2. Embed the returned `s3_url` as an image in the PDF using `canvas.drawImage` or reportlab's `Image` flowable

```python
from reportlab.platypus import Image as RLImage
from reportlab.lib.units import inch
import requests, tempfile, os

def embed_url_image(s3_url: str, width: float, height: float) -> RLImage:
    """Download an S3 image and return a reportlab Image flowable."""
    resp = requests.get(s3_url, timeout=30)
    resp.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    tmp.write(resp.content)
    tmp.close()
    return RLImage(tmp.name, width=width, height=height)

# Step 1 — generate the infographic via image_gen_tool (call this tool first)
# result = image_gen_tool(prompt="A clean KPI infographic showing pipeline drop -52.5%, "
#                                 "win rate 18%, total deals 71, revenue $1.67M. "
#                                 "Dark navy background, white teal text, bold numbers, "
#                                 "minimalist data-viz style, 16:9 aspect ratio")
# s3_url = result["s3_url"]

# Step 2 — embed in the PDF story
# story.append(embed_url_image(s3_url, width=6.5*inch, height=3.6*inch))
# story.append(Spacer(1, 0.15*inch))
```

**Writing a good prompt for infographics:**
- State the exact data values you want shown (copy them from your KPI dict)
- Specify layout: "horizontal", "grid of 4 cards", "timeline", etc.
- Specify style: "dark navy", "white minimal", "corporate blue", "bold colorful"
- Specify font feel: "sans-serif", "bold numbers", "48pt metric values with 12pt labels"
- Specify aspect ratio if the space is constrained: "16:9", "square", "portrait"

---

```python
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER

STYLES = {
    "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, leading=22, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor("#1a1a2e")),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, leading=18, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#2d4a8a")),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=15, spaceBefore=3, spaceAfter=6),
    "caption": ParagraphStyle("caption", fontName="Helvetica-Oblique", fontSize=8, leading=11, textColor=colors.HexColor("#666666")),
    "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=colors.HexColor("#444444")),
}
```

**Tables**
- Always set explicit `colWidths` that sum to the available page width (`page_width - leftMargin - rightMargin`)
- Always set `TOPPADDING` and `BOTTOMPADDING` ≥ 6pt on all rows
- Always set `LEFTPADDING` and `RIGHTPADDING` ≥ 8pt on all rows
- Never let table content overflow its column — use `Paragraph` inside cells for long text, not plain strings

```python
# Available width helper
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch

PAGE_W, PAGE_H = letter
MARGIN = 0.75 * inch
AVAIL_W = PAGE_W - 2 * MARGIN   # 7.0 inches for letter page

# Table with text wrapping in cells
from reportlab.platypus import Paragraph, Table, TableStyle

cell_style = ParagraphStyle("cell", fontName="Helvetica", fontSize=9, leading=13)

data = [
    [Paragraph("<b>Header 1</b>", cell_style), Paragraph("<b>Header 2</b>", cell_style)],
    [Paragraph("Long cell content that wraps properly", cell_style), Paragraph("Value", cell_style)],
]
tbl = Table(data, colWidths=[AVAIL_W * 0.6, AVAIL_W * 0.4])
tbl.setStyle(TableStyle([
    ("TOPPADDING",    (0,0), (-1,-1), 7),
    ("BOTTOMPADDING", (0,0), (-1,-1), 7),
    ("LEFTPADDING",   (0,0), (-1,-1), 9),
    ("RIGHTPADDING",  (0,0), (-1,-1), 9),
    ("GRID",          (0,0), (-1,-1), 0.5, colors.HexColor("#dddddd")),
    ("BACKGROUND",    (0,0), (-1,0),  colors.HexColor("#1a1a2e")),
    ("TEXTCOLOR",     (0,0), (-1,0),  colors.white),
    ("FONTNAME",      (0,0), (-1,0),  "Helvetica-Bold"),
    ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.white, colors.HexColor("#f7f7f7")]),
    ("VALIGN",        (0,0), (-1,-1), "TOP"),
]))
```

**Tables must not split across pages — if they do, repeat column headers**

By default reportlab splits tables mid-row across page breaks, leaving the reader with no column context on the next page. Always set `repeatRows` and `splitByRow`:

```python
from reportlab.platypus import Table, TableStyle

tbl = Table(
    data,
    colWidths=[...],
    repeatRows=1,        # repeat the header row (index 0) at the top of every continuation page
    splitByRow=True,     # allow splitting between rows only — never mid-cell
)
```

For small tables that must stay together on one page (e.g. summary cards, metric grids), wrap them in `KeepTogether` so reportlab pushes the whole block to the next page rather than splitting it:

```python
from reportlab.platypus import KeepTogether

# Use for tables with ≤ ~15 rows — anything larger should use repeatRows instead
story.append(KeepTogether([
    Paragraph("Table Title", STYLES["h2"]),
    Spacer(1, 0.08*inch),
    tbl,
]))
```

**Decision rule:**
- Table ≤ ~15 rows → use `KeepTogether` (keeps whole block on one page)
- Table > ~15 rows → use `repeatRows=1, splitByRow=True` (allows split but repeats header)
- Never use neither — a table with no `repeatRows` that splits is always wrong

**Never mix canvas absolute positioning with Platypus flow**
- Body content ONLY goes in Platypus flowables (`Paragraph`, `Table`, `Spacer`, `HRFlowable`)
- Canvas (`drawString`, `drawImage`, `rect`) is ONLY for page-template callbacks (logo, page numbers, footer)
- Mixing both causes text to render on top of each other

**Page numbers — add to every page**
```python
def add_page_decorations(canvas, doc):
    """Page number on every page."""
    page_w, page_h = doc.pagesize
    canvas.saveState()

    # Page number (bottom-right)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#999999"))
    canvas.drawRightString(page_w - 0.5*inch, 0.4*inch, f"Page {doc.page}")

    canvas.restoreState()

doc.build(story, onFirstPage=add_page_decorations, onLaterPages=add_page_decorations)
```

### Pre-export checklist

Run this after every `doc.build()` before uploading:
```python
import pdfplumber

def check_pdf_layout(path: str):
    issues = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            # Check for suspiciously short pages (content cut off)
            if page.height < 400:
                issues.append(f"Page {i}: unusually short ({page.height:.0f}pt)")
            # Check for text outside margins
            margin = 40  # ~0.55 inch in points
            for char in (page.chars or []):
                if char["x0"] < margin or char["x1"] > (page.width - margin):
                    issues.append(f"Page {i}: text near/outside margin at x={char['x0']:.0f}")
                    break  # one warning per page is enough
    if issues:
        print("⚠️  Layout issues:\n" + "\n".join(issues))
    else:
        print("✓ Layout check passed")

check_pdf_layout("output.pdf")
```

### Password Protection
```python
from pypdf import PdfReader, PdfWriter

reader = PdfReader("input.pdf")
writer = PdfWriter()

for page in reader.pages:
    writer.add_page(page)

# Add password
writer.encrypt("userpassword", "ownerpassword")

with open("encrypted.pdf", "wb") as output:
    writer.write(output)
```

## PDF Quality Standards — MANDATORY

These rules apply to every PDF you create. Violations result in broken layouts, invisible content, and unprofessional output.

---

### 1. Metric / summary bars must be Tables — never pipe-separated text

**❌ WRONG** — collapses on narrow screens and in print:
```python
story.append(Paragraph("-52.5% | -34.2% | $1.67M | 71 | $640K", styles["Normal"]))
```

**✅ CORRECT** — styled Table with borders:
```python
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors

metrics = [
    ["-52.5%", "-34.2%", "$1.67M", "71", "$640K"],
    ["Metric A", "Metric B", "Metric C", "Metric D", "Metric E"],
]
tbl = Table(metrics, colWidths=[1.4*inch]*5)
tbl.setStyle(TableStyle([
    ("BACKGROUND",  (0,0), (-1,0), colors.HexColor("#1a1a2e")),
    ("TEXTCOLOR",   (0,0), (-1,0), colors.white),
    ("FONTNAME",    (0,0), (-1,0), "Helvetica-Bold"),
    ("FONTSIZE",    (0,0), (-1,0), 14),
    ("BACKGROUND",  (0,1), (-1,1), colors.HexColor("#f0f0f0")),
    ("FONTSIZE",    (0,1), (-1,1), 8),
    ("TEXTCOLOR",   (0,1), (-1,1), colors.HexColor("#666666")),
    ("ALIGN",       (0,0), (-1,-1), "CENTER"),
    ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
    ("GRID",        (0,0), (-1,-1), 0.5, colors.HexColor("#cccccc")),
    ("ROWBACKGROUNDS", (0,0), (-1,-1), [colors.HexColor("#1a1a2e"), colors.HexColor("#f0f0f0")]),
    ("TOPPADDING",  (0,0), (-1,-1), 8),
    ("BOTTOMPADDING",(0,0),(-1,-1), 8),
]))
story.append(tbl)
story.append(Spacer(1, 0.2*inch))
```

---

### 2. Section headers must have visual weight — not just bold text

Use a coloured background band or a thick rule so skim-readers can navigate. Plain bold in a dense document is invisible.

```python
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors

def section_header(title: str, accent_color: str = "#2d4a8a") -> Table:
    """Returns a full-width colour-band section header."""
    tbl = Table([[title]], colWidths=[7.0*inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor(accent_color)),
        ("TEXTCOLOR",     (0,0), (-1,-1), colors.white),
        ("FONTNAME",      (0,0), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 12),
        ("TOPPADDING",    (0,0), (-1,-1), 8),
        ("BOTTOMPADDING", (0,0), (-1,-1), 8),
        ("LEFTPADDING",   (0,0), (-1,-1), 10),
    ]))
    return tbl

# Usage
story.append(Spacer(1, 0.15*inch))
story.append(section_header("2. Root Cause Analysis"))
story.append(Spacer(1, 0.1*inch))
```

---

### 3. Severity / status badges must have coloured backgrounds

CSS-based colours are stripped in PDF. Always use reportlab `Table` cells for badges.

```python
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors

SEVERITY_COLORS = {
    "CRITICAL": ("#cc0000", "#ffffff"),   # red bg, white text
    "HIGH":     ("#e65c00", "#ffffff"),   # amber bg, white text
    "MEDIUM":   ("#e6b800", "#000000"),   # yellow bg, black text
    "LOW":      ("#2e7d32", "#ffffff"),   # green bg, white text
    "INFO":     ("#1565c0", "#ffffff"),   # blue bg, white text
}

def severity_badge(label: str, level: str) -> Table:
    bg, fg = SEVERITY_COLORS.get(level.upper(), ("#888888", "#ffffff"))
    tbl = Table([[f"{label}  [{level.upper()}]"]], colWidths=[2.5*inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,-1), colors.HexColor(bg)),
        ("TEXTCOLOR",     (0,0), (-1,-1), colors.HexColor(fg)),
        ("FONTNAME",      (0,0), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,-1), 10),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING",   (0,0), (-1,-1), 8),
        ("RIGHTPADDING",  (0,0), (-1,-1), 8),
        ("ROUNDEDCORNERS",(0,0), (-1,-1), [4,4,4,4]),
    ]))
    return tbl

# Usage
story.append(severity_badge("RC-1", "CRITICAL"))
story.append(Spacer(1, 0.08*inch))
story.append(severity_badge("RC-2", "HIGH"))
```

---

### 4. PDF metadata must always be set

A PDF without metadata shows as "Untitled" in email clients and document management systems. **Always set title, author, and date — before calling `doc.build()`.**

```python
from datetime import datetime

doc = SimpleDocTemplate("output.pdf", pagesize=letter, ...)

# Set metadata on the doc object before build
doc.title   = "Your Report Title"
doc.author  = "PriceIQ"
doc.subject = "Brief description of report"
doc.creator = "PriceIQ"

# Build with the metadata already set
doc.build(story, onFirstPage=add_page_decorations, onLaterPages=add_page_decorations)
```

If using `canvas` directly:
```python
c = canvas.Canvas("output.pdf", pagesize=letter)
c.setTitle("Your Report Title")
c.setAuthor("PriceIQ")
c.setSubject("Brief description")
c.setCreator("PriceIQ")
c.setKeywords("priceiq report")
```

---

### 5. Numbers and inline values must stay on the same line

**❌ WRONG** — using plain string concatenation or f-strings with line breaks inside a `Paragraph`:
```python
story.append(Paragraph(f"Total Revenue: \n$1,234,567", styles["Normal"]))
story.append(Paragraph("Win Rate:\n18%", styles["Normal"]))
```

**❌ WRONG** — building numeric content as separate `Paragraph` objects that end up visually disconnected:
```python
story.append(Paragraph("Total Revenue:", label_style))
story.append(Paragraph("$1,234,567", value_style))  # renders on its own line, looks broken
```

**✅ CORRECT** — label and value in a single `Paragraph` with a non-breaking space, or in adjacent Table cells:
```python
# Option A: single Paragraph — label and value never split
story.append(Paragraph("Total Revenue: <b>$1,234,567</b>", styles["Normal"]))
story.append(Paragraph("Win Rate: <b>18%</b>", styles["Normal"]))

# Option B: two-column Table — keeps label and value on the same visual row
from reportlab.platypus import Table, TableStyle
row_style = ParagraphStyle("row", fontName="Helvetica", fontSize=10, leading=14)
data = [
    [Paragraph("Total Revenue:", row_style), Paragraph("<b>$1,234,567</b>", row_style)],
    [Paragraph("Win Rate:",      row_style), Paragraph("<b>18%</b>",        row_style)],
]
tbl = Table(data, colWidths=[2.5*inch, 2.5*inch])
tbl.setStyle(TableStyle([
    ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING",    (0,0), (-1,-1), 4),
    ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ("LEFTPADDING",   (0,0), (-1,-1), 0),
    ("RIGHTPADDING",  (0,0), (-1,-1), 8),
]))
story.append(tbl)
```

**Rules:**
- A label and its numeric value MUST appear on the same line — never on separate lines
- Never insert `\n` inside a `Paragraph` string that contains a number and its label
- Never place a label `Paragraph` immediately before a value `Paragraph` without a table — they will render as separate lines
- For key-value pairs (e.g. "Revenue: $1.2M", "Count: 42"), always use a two-column Table or a single inline `Paragraph`
- For metric grids (multiple KPIs in a row), always use a multi-column Table (see rule 1 above)

---

### 6. Prevent text overlap — spacing and layout rules  <!-- was §5 -->

Text overlap is caused by insufficient margins, missing `Spacer` between elements, or absolute-positioned canvas text that ignores content flow. Follow these rules:

- **Always use `SimpleDocTemplate` / Platypus flowables** (`Paragraph`, `Table`, `Spacer`) for body content — never mix canvas `drawString` absolute positioning with Platypus flow on the same page.
- **Set `topMargin` to at least `0.75 * inch`** to give content breathing room from the page edge.
- **Add `Spacer(1, 0.15*inch)` between every major block** (after tables, before/after section headers).
- **Use `spaceAfter` and `spaceBefore` on ParagraphStyles** rather than hard-coding spacing in individual elements.
- **Never use `canvas.drawString` for body text** — only use it in page-template callbacks (page numbers, headers/footers). Body text belongs in `Paragraph` flowables.
- **Wrap long strings** — set an explicit `wordWrap="CJK"` or use `Paragraph` (which wraps automatically) instead of `drawString` for any text longer than a few words.

```python
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT

# Define styles with explicit spacing — no guesswork
body_style = ParagraphStyle(
    "body",
    fontName="Helvetica",
    fontSize=10,
    leading=15,          # line height = 1.5x font size
    spaceAfter=8,
    spaceBefore=4,
    wordWrap="LTR",
)

heading_style = ParagraphStyle(
    "heading",
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=18,
    spaceBefore=14,
    spaceAfter=6,
)
```

---

## Quick Reference

| Task | Best Tool | Command/Code |
|------|-----------|--------------|
| Merge PDFs | pypdf | `writer.add_page(page)` |
| Split PDFs | pypdf | One page per file |
| Extract text | pdfplumber | `page.extract_text()` |
| Extract tables | pdfplumber | `page.extract_tables()` |
| Create PDFs | reportlab | Canvas or Platypus |
| Command line merge | qpdf | `qpdf --empty --pages ...` |
| OCR scanned PDFs | pytesseract | Convert to image first |
| Fill PDF forms | pdf-lib or pypdf (see FORMS.md) | See FORMS.md |

## Next Steps

- For advanced pypdfium2 usage, see REFERENCE.md
- For JavaScript libraries (pdf-lib), see REFERENCE.md
- If you need to fill out a PDF form, follow the instructions in FORMS.md
- For troubleshooting guides, see REFERENCE.md
