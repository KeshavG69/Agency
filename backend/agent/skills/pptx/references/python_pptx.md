# python-pptx Guide

## Setup & Basic Structure

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR_TYPE
from pptx.enum.chart import XL_CHART_TYPE
from pptx.chart.data import ChartData
from pptx.oxml.ns import qn
from lxml import etree
import io

prs = Presentation()
prs.slide_width = Inches(10)
prs.slide_height = Inches(5.625)  # 16x9

blank_layout = prs.slide_layouts[6]  # 6 = Blank (full control)
slide = prs.slides.add_slide(blank_layout)

# Save to file
prs.save("output.pptx")

# Save to BytesIO (for S3 upload)
buf = io.BytesIO()
prs.save(buf)
buf.seek(0)
```

---

## Layout Dimensions

```python
prs.slide_width = Inches(10)
prs.slide_height = Inches(5.625)   # 16x9  (default)
# prs.slide_height = Inches(6.25)  # 16x10
# prs.slide_height = Inches(7.5)   # 4x3
# prs.slide_width  = Inches(13.3)  # WIDE (13.3 x 7.5)
```

**Slide layouts by index:**
- `0` Title Slide, `1` Title and Content, `5` Title Only, `6` **Blank** ← use this for full control

---

## Text & Formatting

```python
# Add a text box: add_textbox(left, top, width, height)
txBox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1))
tf = txBox.text_frame
tf.word_wrap = True

# Remove internal padding (use when aligning with shapes/lines)
tf.margin_left = 0
tf.margin_right = 0
tf.margin_top = 0
tf.margin_bottom = 0

# Vertical alignment
tf.anchor = MSO_ANCHOR.MIDDLE   # TOP | MIDDLE | BOTTOM

# First paragraph already exists — use it, don't add before using
p = tf.paragraphs[0]
p.alignment = PP_ALIGN.CENTER   # LEFT | CENTER | RIGHT

run = p.add_run()
run.text = "Hello World"
run.font.size = Pt(36)
run.font.bold = True
run.font.italic = False
run.font.color.rgb = RGBColor(0x36, 0x36, 0x36)
run.font.name = "Calibri"

# Add more paragraphs
p2 = tf.add_paragraph()
p2.alignment = PP_ALIGN.LEFT
run2 = p2.add_run()
run2.text = "Subtitle text"
run2.font.size = Pt(18)
run2.font.color.rgb = RGBColor(0x64, 0x74, 0x8B)
```

---

## Shapes

```python
# Rectangle: add_shape(shape_type, left, top, width, height)
shape = slide.shapes.add_shape(
    MSO_SHAPE.RECTANGLE,
    Inches(1), Inches(1), Inches(3), Inches(2)
)
shape.fill.solid()
shape.fill.fore_color.rgb = RGBColor(0xFF, 0x00, 0x00)
shape.line.color.rgb = RGBColor(0x00, 0x00, 0x00)
shape.line.width = Pt(2)

# No fill
shape.fill.background()

# No border
shape.line.fill.background()

# Oval
slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(4), Inches(1), Inches(2), Inches(2))

# Rounded rectangle
slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1), Inches(1), Inches(3), Inches(2))
```

### Lines (Connectors)

```python
# Horizontal line
conn = slide.shapes.add_connector(
    MSO_CONNECTOR_TYPE.STRAIGHT,
    Inches(1), Inches(3),   # start x, y
    Inches(9), Inches(3)    # end x, y
)
conn.line.color.rgb = RGBColor(0xE2, 0xE8, 0xF0)
conn.line.width = Pt(1)
```

### Shadow (via XML)

```python
def add_shadow(shape, color="000000", blur_pt=6, offset_pt=2, angle_deg=135, opacity=0.15):
    """Add outer drop shadow to a shape. All units are points."""
    sp = shape._element
    spPr = sp.find(qn("p:spPr"))
    effectLst = etree.SubElement(spPr, qn("a:effectLst"))
    outerShdw = etree.SubElement(effectLst, qn("a:outerShdw"))
    outerShdw.set("blurRad", str(int(blur_pt * 12700)))   # pt → EMU
    outerShdw.set("dist", str(int(offset_pt * 12700)))
    outerShdw.set("dir", str(int(angle_deg * 60000)))     # degrees → 1/60000
    srgbClr = etree.SubElement(outerShdw, qn("a:srgbClr"))
    srgbClr.set("val", color)                              # 6-char hex, no #
    alpha = etree.SubElement(srgbClr, qn("a:alpha"))
    alpha.set("val", str(int(opacity * 100000)))
```

---

## Slide Backgrounds

```python
# Solid color
bg = slide.background
bg.fill.solid()
bg.fill.fore_color.rgb = RGBColor.from_string("1E2761")  # no # prefix

# Image background (fills entire slide, sent to back)
from PIL import Image
imgbuf = io.BytesIO()
# ... populate imgbuf with image data ...
imgbuf.seek(0)
pic = slide.shapes.add_picture(imgbuf, 0, 0, prs.slide_width, prs.slide_height)
# Move behind all other shapes
slide.shapes._spTree.remove(pic._element)
slide.shapes._spTree.insert(2, pic._element)
```

---

## Images

```python
# From file path
slide.shapes.add_picture("image.png", Inches(1), Inches(1), Inches(5), Inches(3))

# From BytesIO / in-memory
slide.shapes.add_picture(io.BytesIO(img_bytes), Inches(1), Inches(1), Inches(5), Inches(3))

# From URL
import urllib.request
with urllib.request.urlopen(url) as resp:
    slide.shapes.add_picture(io.BytesIO(resp.read()), Inches(1), Inches(1), Inches(5), Inches(3))
```

### Preserve Aspect Ratio

```python
orig_w, orig_h = 1978, 923
max_h = Inches(3.0)
calc_w = int(max_h * orig_w / orig_h)
center_x = int((prs.slide_width - calc_w) / 2)
slide.shapes.add_picture("image.png", center_x, Inches(1.2), calc_w, max_h)
```

---

## Tables

```python
# add_table(rows, cols, left, top, width, height)
tbl = slide.shapes.add_table(3, 2, Inches(1), Inches(1), Inches(8), Inches(2)).table

# Set column widths (must sum to table width)
tbl.columns[0].width = Inches(4)
tbl.columns[1].width = Inches(4)

data = [["Header 1", "Header 2"], ["Cell A", "Cell B"], ["Cell C", "Cell D"]]
for r, row_data in enumerate(data):
    for c, text in enumerate(row_data):
        cell = tbl.cell(r, c)
        cell.text = text
        tf = cell.text_frame
        run = tf.paragraphs[0].runs[0]
        run.font.size = Pt(14)
        if r == 0:
            run.font.bold = True
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor(0x44, 0x72, 0xC4)
```

---

## Charts

```python
from pptx.chart.data import ChartData
from pptx.enum.chart import XL_CHART_TYPE

# Bar / Column chart
chart_data = ChartData()
chart_data.categories = ["Q1", "Q2", "Q3", "Q4"]
chart_data.add_series("Sales", (4500, 5500, 6200, 7100))

chart = slide.shapes.add_chart(
    XL_CHART_TYPE.COLUMN_CLUSTERED,
    Inches(0.5), Inches(0.6), Inches(6), Inches(3),
    chart_data
).chart

chart.has_title = True
chart.chart_title.text_frame.text = "Quarterly Sales"

# Line chart
chart_data2 = ChartData()
chart_data2.categories = ["Jan", "Feb", "Mar"]
chart_data2.add_series("Temp", (32, 35, 42))
slide.shapes.add_chart(XL_CHART_TYPE.LINE, Inches(0.5), Inches(4), Inches(6), Inches(3), chart_data2)

# Pie chart
chart_data3 = ChartData()
chart_data3.categories = ["A", "B", "Other"]
chart_data3.add_series("Share", (35, 45, 20))
slide.shapes.add_chart(XL_CHART_TYPE.PIE, Inches(7), Inches(1), Inches(5), Inches(4), chart_data3)
```

---

## Bullets

```python
txBox = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(3))
tf = txBox.text_frame
tf.word_wrap = True

items = ["First item", "Second item", "Third item"]
for i, item in enumerate(items):
    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
    run = p.add_run()
    run.text = item
    run.font.size = Pt(16)
    # Add bullet via XML
    pPr = p._p.get_or_add_pPr()
    buChar = etree.SubElement(pPr, qn("a:buChar"))
    buChar.set("char", "•")
```

---

## Common Pitfalls

1. **Always use `Inches()` or `Pt()`** — raw numbers are EMUs (914400 per inch), shapes will be invisible
   ```python
   Inches(1)   # ✅
   1           # ❌ = 1 EMU ≈ 0
   ```

2. **`RGBColor` takes 0–255 integers, not hex strings**
   ```python
   RGBColor(0xFF, 0x00, 0x00)       # ✅
   RGBColor.from_string("FF0000")   # ✅ (no # prefix)
   RGBColor("#FF0000")              # ❌ crashes
   ```

3. **`tf.paragraphs[0]` already exists** — use it directly, never call `add_paragraph()` before it
   ```python
   p = tf.paragraphs[0]    # ✅ first paragraph
   p2 = tf.add_paragraph() # ✅ subsequent paragraphs
   ```

4. **Shapes added later appear on top** — add backgrounds and base shapes first

5. **`add_connector` end coords are absolute slide coordinates**, not relative to start
   ```python
   # Line from x=1" to x=9" at y=3"
   add_connector(MSO_CONNECTOR_TYPE.STRAIGHT, Inches(1), Inches(3), Inches(9), Inches(3))
   ```

6. **`table.cell(r, c).text = ...` replaces all content** — access `runs[0]` after setting text to format it

7. **`prs.save()` must be called once at the end** — not per slide

8. **`MSO_SHAPE_TYPE` does not exist** — use `MSO_SHAPE` for shapes and `MSO_CONNECTOR_TYPE` for connectors
   ```python
   from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR_TYPE  # ✅
   from pptx.enum.shapes import MSO_SHAPE_TYPE                 # ❌ does not exist
   ```
   Never use `__import__()` tricks to get these enums — import them at the top of the script.

9. **Import everything at the top** — do not re-import inside helper functions or use inline `__import__()` calls
   ```python
   # ✅ correct — all at the top
   from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR_TYPE
   from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

   # ❌ wrong — buried inside a function or using __import__
   def add_rect(...):
       from pptx.enum.shapes import MSO_SHAPE  # don't do this
   ```

10. **Text getting cut off — always set `auto_size` and zero margins**

    By default text frames have a fixed height and internal padding (~0.05"). If the text is longer than the box, it is silently clipped — no error, just missing content.

    Two strategies:

    **Option A — grow the box to fit the text (preferred for body/title boxes)**
    ```python
    from pptx.enum.text import MSO_AUTO_SIZE

    tf = shape.text_frame
    tf.word_wrap = True
    tf.auto_size = MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT   # box height grows to fit all text
    # Zero out internal margins so the box edge matches the text edge exactly
    tf.margin_left   = 0
    tf.margin_right  = 0
    tf.margin_top    = 0
    tf.margin_bottom = 0
    ```

    **Option B — shrink text to fit the box (for fixed-size cards/cells)**
    ```python
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE   # font shrinks until all text fits
    tf.word_wrap = True
    ```

    **Never leave `auto_size` at its default (`MSO_AUTO_SIZE.NONE`)** unless you have manually verified the text will always fit the given height. Even one extra line of wrapped text will be clipped.

    ```python
    from pptx.enum.text import MSO_AUTO_SIZE

    # ✅ always set one of these
    tf.auto_size = MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT   # grow box
    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE   # shrink font

    # ❌ default — text silently clips if it overflows
    # tf.auto_size = MSO_AUTO_SIZE.NONE
    ```
