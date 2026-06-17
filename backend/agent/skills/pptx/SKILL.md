---
name: pptx
description: "Use this skill any time a .pptx file is involved in any way — as input, output, or both. This includes: creating slide decks, pitch decks, or presentations; reading, parsing, or extracting text from any .pptx file (even if the extracted content will be used elsewhere, like in an email or summary); editing, modifying, or updating existing presentations; combining or splitting slide files; working with templates, layouts, speaker notes, or comments. Trigger whenever the user mentions \"deck,\" \"slides,\" \"presentation,\" or references a .pptx filename, regardless of what they plan to do with the content afterward. If a .pptx file needs to be opened, created, or touched, use this skill."
license: Proprietary. LICENSE.txt has complete terms
---

# PPTX Skill

## Quick Reference

| Task | Guide |
|------|-------|
| Read/analyze content | `python -m markitdown presentation.pptx` |
| Edit or create from template | Read [editing.md](references/editing.md) |
| Create from scratch (Python) | Read [python_pptx.md](references/python_pptx.md) |
| Create from scratch (Node.js) | Read [pptxgenjs.md](references/pptxgenjs.md) |

---

## Reading Content

```bash
# Text extraction
python -m markitdown presentation.pptx

# Visual overview
python scripts/thumbnail.py presentation.pptx

# Raw XML
python scripts/office/unpack.py presentation.pptx unpacked/
```

---

## Editing Workflow

**Read [editing.md](references/editing.md) for full details.**

1. Analyze template with `thumbnail.py`
2. Unpack → manipulate slides → edit content → clean → pack

---

## Creating from Scratch

**Prefer Python (`python-pptx`) when using the python_repl_tool** — read [python_pptx.md](references/python_pptx.md).

Use Node.js (`pptxgenjs`) only when a JS environment is available — read [pptxgenjs.md](references/pptxgenjs.md).

Use when no template or reference presentation is available.

### Slide Count — HARD LIMIT

**Maximum 10 slides per deck.** Never generate more than 10 slides regardless of content volume. If the content requires more, condense by:
- Combining related points on one slide
- Reducing bullets to the 3 most impactful per slide
- Moving supporting detail to speaker notes

### Batching — Only When Necessary

Generate all slides in a single `python_repl_tool` call. Only split into batches if the script genuinely fails or times out mid-way. If batching is needed, do a minimum of 4 slides per batch — save after each batch and reload with `Presentation("output.pptx")` to continue adding slides.

---

## Formatting & Alignment — MANDATORY

Every deck you produce must pass all of these checks before it is considered done. Do not skip any of them.

### Rules

**Slide margins — never let content touch the edge**
```python
from pptx.util import Inches

SLIDE_W  = prs.slide_width
SLIDE_H  = prs.slide_height

MARGIN_L = Inches(0.4)
MARGIN_T = Inches(0.4)
MARGIN_R = Inches(0.4)

FOOTER_H     = Inches(0.28)                        # footer bar height
CONTENT_W    = SLIDE_W - MARGIN_L - MARGIN_R       # usable width
CONTENT_BOTTOM = SLIDE_H - FOOTER_H - Inches(0.05) # last pixel of usable area — nothing should go below this
```

**`CONTENT_BOTTOM` is the single most important constant.** Every shape's bottom edge (`top + height`) must be `<= CONTENT_BOTTOM`. If you add too many rows and run out of room, reduce font size, tighten row spacing, or split content across two slides — never let it overflow past the footer.

**Shape positions — always explicit, never guessed**
- Every `add_shape`, `add_textbox`, `add_picture` call must have explicit `left`, `top`, `width`, `height`
- Never rely on default positions — they stack at (0, 0) and overlap each other
- Verify shapes don't exceed slide bounds: `left + width <= SLIDE_W` and `top + height <= SLIDE_H`

**Font sizes — minimum readable sizes**
```python
from pptx.util import Pt

FONT = {
    "title":    Pt(36),   # slide title
    "heading":  Pt(22),   # section label
    "body":     Pt(14),   # body text
    "caption":  Pt(10),   # footnotes, source labels
    "footer":   Pt(7),    # footer bar
}
# Never go below Pt(10) for any user-visible text
```

**Text frames — always set word_wrap, auto_size, and zero margins**

Text boxes have a fixed height by default — text that overflows is silently clipped with no error. Always set `auto_size` to prevent cut-off text.

```python
from pptx.util import Pt
from pptx.enum.text import PP_ALIGN, MSO_AUTO_SIZE
from lxml import etree

def set_textbox(shape, text: str, font_size: Pt, bold=False,
                color: str = "1a1a2e", align=PP_ALIGN.LEFT,
                fit: str = "grow"):
    """
    fit="grow"   → box height expands to fit all text (use for most text boxes)
    fit="shrink" → font shrinks to fit fixed box size (use for cards/cells with fixed dimensions)
    """
    tf = shape.text_frame
    tf.word_wrap = True
    if fit == "shrink":
        tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    else:
        tf.auto_size = MSO_AUTO_SIZE.SHAPE_TO_FIT_TEXT
    # Zero internal margins so box edge matches text edge exactly
    tf.margin_left   = 0
    tf.margin_right  = 0
    tf.margin_top    = 0
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = font_size
    run.font.bold = bold
    run.font.name = "Calibri"
    run.font.color.rgb = RGBColor.from_string(color)
```

**Tables — always explicit column widths summing to content width**
```python
from pptx.util import Inches
from pptx.enum.text import PP_ALIGN

def add_table(slide, data: list[list], col_widths: list,
              left=MARGIN_L, top=Inches(1.5), row_height=Inches(0.38)):
    rows, cols = len(data), len(data[0])
    tbl = slide.shapes.add_table(rows, cols, left, top,
                                  sum(col_widths), row_height * rows).table
    for ci, w in enumerate(col_widths):
        tbl.columns[ci].width = w
    for ri, row in enumerate(data):
        tbl.rows[ri].height = row_height
        for ci, val in enumerate(row):
            cell = tbl.cell(ri, ci)
            cell.text = str(val)
            cell.text_frame.paragraphs[0].alignment = PP_ALIGN.CENTER
            # Header row styling
            if ri == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = RGBColor(0x1a, 0x1a, 0x2e)
                for run in cell.text_frame.paragraphs[0].runs:
                    run.font.color.rgb = RGBColor(0xff, 0xff, 0xff)
                    run.font.bold = True
                    run.font.size = Pt(11)
                    run.font.name = "Calibri"
            else:
                for run in cell.text_frame.paragraphs[0].runs:
                    run.font.size = Pt(10)
                    run.font.name = "Calibri"
    return tbl
```

**Alignment checklist — verify every slide before saving**
```python
from pptx import Presentation
from pptx.util import Inches

def check_alignment(path: str) -> list[str]:
    """Detect shapes outside slide bounds or overlapping the footer zone."""
    prs = Presentation(path)
    issues = []
    footer_h     = Inches(0.28)
    content_bottom = prs.slide_height - footer_h - Inches(0.05)  # usable bottom edge

    for i, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            l, t = shape.left or 0, shape.top or 0
            w, h = shape.width or 0, shape.height or 0

            # Outside right edge
            if l + w > prs.slide_width:
                issues.append(f"Slide {i} '{shape.name}': extends past right edge ({(l+w)/914400:.2f}\" > {prs.slide_width/914400:.2f}\")")
            # Outside bottom edge
            if t + h > prs.slide_height:
                issues.append(f"Slide {i} '{shape.name}': extends past slide bottom ({(t+h)/914400:.2f}\" > {prs.slide_height/914400:.2f}\")")
            # Overlapping footer zone (skip the footer shape itself)
            elif "footer" not in shape.name.lower() and "rectangle" not in shape.name.lower() and t + h > content_bottom:
                issues.append(f"Slide {i} '{shape.name}': bottom edge {(t+h)/914400:.2f}\" overlaps footer zone (limit={content_bottom/914400:.2f}\")")
    return issues

issues = check_alignment("output.pptx")
if issues:
    print("⚠️  Alignment issues:\n" + "\n".join(issues))
else:
    print("✓ Alignment check passed")
```

---

## Image & Infographic Generation — USE image_gen_tool

**Whenever the user asks for an infographic, visual, diagram, banner, or image inside a slide deck — call `image_gen_tool` first, then embed the returned S3 URL.**

### When to use it

- User says "add an infographic", "create a visual", "generate an image", "put a diagram on slide X"
- You decide a slide would benefit from a visual instead of plain text
- Any slide that would otherwise be text-only — add an image to make it memorable

### How to embed the image in a slide

```python
import urllib.request
import tempfile
import os

def embed_url_image(slide, url: str, left, top, width, height=None):
    """
    Download an S3 URL and add it to a slide using add_picture().
    height=None → python-pptx preserves aspect ratio automatically.
    """
    suffix = "." + url.split(".")[-1].split("?")[0]   # ".png", ".jpg", etc.
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        urllib.request.urlretrieve(url, tmp.name)
        pic = slide.shapes.add_picture(tmp.name, left=left, top=top, width=width, height=height)
        return pic
    finally:
        os.unlink(tmp.name)

# Example — embed a generated infographic on a slide
result = image_gen_tool(prompt="...")   # call tool first (see prompt tips below)
if result["success"]:
    embed_url_image(
        slide,
        url=result["s3_url"],
        left=Inches(5.2),    # right-hand column in a two-column layout
        top=Inches(0.4),
        width=Inches(4.4),   # fill right column; height auto-preserved
    )
```

### How to write a great prompt

A vague prompt produces a generic image. Be specific:

```
✅ GOOD — "A clean, modern infographic on a dark navy background showing 5 KPIs:
   Pipeline Drop -52.5%, Deal Count 71, Total Value $1.67M, Avg Deal Size $23.5K,
   Win Rate Delta -34.2%. Use bold white numbers with coral accent color.
   Minimalist, corporate, no clip art."

❌ BAD — "An infographic about sales performance"
```

Always include:
- **Subject & data points** — actual numbers/labels from the deck
- **Background color** — match or complement the slide palette
- **Style** — minimalist / corporate / bold / editorial
- **Layout hint** — vertical list / grid / radial / horizontal bars
- **Mood** — professional, energetic, calm, urgent

### Placement rules

- Infographics work best in **right-side column** of a two-column layout (left = text, right = image)
- For full-bleed visual slides: `left=0, top=0, width=SLIDE_W, height=SLIDE_H` then overlay text shapes on top
- Always keep the image within slide margins (`top >= MARGIN_T`)
- If `height` is given and distorts the image, remove the `height` argument and let python-pptx preserve aspect ratio

---

## Design Ideas

**Don't create boring slides.** Plain bullets on a white background won't impress anyone. Consider ideas from this list for each slide.

### Before Starting

- **Pick a bold, content-informed color palette**: The palette should feel designed for THIS topic. If swapping your colors into a completely different presentation would still "work," you haven't made specific enough choices.
- **Dominance over equality**: One color should dominate (60-70% visual weight), with 1-2 supporting tones and one sharp accent. Never give all colors equal weight.
- **Dark/light contrast**: Dark backgrounds for title + conclusion slides, light for content ("sandwich" structure). Or commit to dark throughout for a premium feel.
- **Commit to a visual motif**: Pick ONE distinctive element and repeat it — rounded image frames, icons in colored circles, thick single-side borders. Carry it across every slide.

### Color Palettes

Choose colors that match your topic — don't default to generic blue. Use these palettes as inspiration:

| Theme | Primary | Secondary | Accent |
|-------|---------|-----------|--------|
| **Midnight Executive** | `1E2761` (navy) | `CADCFC` (ice blue) | `FFFFFF` (white) |
| **Forest & Moss** | `2C5F2D` (forest) | `97BC62` (moss) | `F5F5F5` (cream) |
| **Coral Energy** | `F96167` (coral) | `F9E795` (gold) | `2F3C7E` (navy) |
| **Warm Terracotta** | `B85042` (terracotta) | `E7E8D1` (sand) | `A7BEAE` (sage) |
| **Ocean Gradient** | `065A82` (deep blue) | `1C7293` (teal) | `21295C` (midnight) |
| **Charcoal Minimal** | `36454F` (charcoal) | `F2F2F2` (off-white) | `212121` (black) |
| **Teal Trust** | `028090` (teal) | `00A896` (seafoam) | `02C39A` (mint) |
| **Berry & Cream** | `6D2E46` (berry) | `A26769` (dusty rose) | `ECE2D0` (cream) |
| **Sage Calm** | `84B59F` (sage) | `69A297` (eucalyptus) | `50808E` (slate) |
| **Cherry Bold** | `990011` (cherry) | `FCF6F5` (off-white) | `2F3C7E` (navy) |

### For Each Slide

**Every slide needs a visual element** — image, chart, icon, or shape. Text-only slides are forgettable.

**Layout options:**
- Two-column (text left, illustration on right)
- Icon + text rows (icon in colored circle, bold header, description below)
- 2x2 or 2x3 grid (image on one side, grid of content blocks on other)
- Half-bleed image (full left or right side) with content overlay

**Data display:**
- Large stat callouts (big numbers 60-72pt with small labels below)
- Comparison columns (before/after, pros/cons, side-by-side options)
- Timeline or process flow (numbered steps, arrows)

**Visual polish:**
- Icons in small colored circles next to section headers
- Italic accent text for key stats or taglines

### Typography

**Always use Calibri.** Every text element — titles, headings, body, captions, footers — must use `"Calibri"`. It is PowerPoint's built-in default and ships with the python-pptx environment without any font registration.

```python
# Set on every run
run.font.name = "Calibri"
```

| Element | Font | Size |
|---------|------|------|
| Slide title | Calibri Bold | 36-44pt |
| Section header | Calibri Bold | 20-24pt |
| Body text | Calibri | 14-16pt |
| Captions | Calibri | 10-12pt muted |
| Footer | Calibri | 7pt |

### Spacing

- 0.5" minimum margins
- 0.3-0.5" between content blocks
- Leave breathing room—don't fill every inch

### Avoid (Common Mistakes)

- **Don't repeat the same layout** — vary columns, cards, and callouts across slides
- **Don't center body text** — left-align paragraphs and lists; center only titles
- **Don't skimp on size contrast** — titles need 36pt+ to stand out from 14-16pt body
- **Don't default to blue** — pick colors that reflect the specific topic
- **Don't mix spacing randomly** — choose 0.3" or 0.5" gaps and use consistently
- **Don't style one slide and leave the rest plain** — commit fully or keep it simple throughout
- **Don't create text-only slides** — add images, icons, charts, or visual elements; avoid plain title + bullets
- **Don't forget text box padding** — when aligning lines or shapes with text edges, set `margin: 0` on the text box or offset the shape to account for padding
- **Don't use low-contrast elements** — icons AND text need strong contrast against the background; avoid light text on light backgrounds or dark text on dark backgrounds
- **NEVER use accent lines under titles** — these are a hallmark of AI-generated slides; use whitespace or background color instead
- **Don't overflow past `CONTENT_BOTTOM`** — if too many rows push content into/past the footer, reduce row spacing, shrink font, or split into two slides. Never let shapes go below `CONTENT_BOTTOM = slide_height - footer_height - 0.05"`

---

## PPTX Quality Standards — MANDATORY

These rules apply to every deck you create. Run all checks **before** calling the task done.

---

### 1. All template placeholders must be replaced with real values

Unreplaced placeholders (e.g. "Why the Pipeline Dropped 30%+" when the data says -52.5%) are the most visible quality failure. **Never leave a template string in the final deck.**

```python
from pptx import Presentation

def verify_no_placeholders(path: str, forbidden: list[str]) -> list[str]:
    """Return list of slides still containing forbidden placeholder strings."""
    prs = Presentation(path)
    issues = []
    for i, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text
            for token in forbidden:
                if token.lower() in text.lower():
                    issues.append(f"Slide {i}: found '{token}' in shape '{shape.name}'")
    return issues

# Run before export — fail hard if anything is found
PLACEHOLDER_TOKENS = ["[TITLE]", "[METRIC]", "[DATE]", "TBD", "XXX", "Lorem", "ipsum"]
issues = verify_no_placeholders("output.pptx", PLACEHOLDER_TOKENS)
if issues:
    raise ValueError("Unreplaced placeholders found:\n" + "\n".join(issues))
print("✓ No unreplaced placeholders")
```

---

### 2. All instances of the same KPI must use the same value and rounding

If slide 1 says -53% and slide 2 says -52.5%, a board member will catch it. **Declare all KPIs as constants at the top of your code and reference them everywhere — never type a number twice.**

```python
# ✅ CORRECT — single source of truth for all metrics
KPI = {
    "pipeline_drop":    "-52.5%",
    "deal_count":       "71",
    "total_value":      "$1.67M",
    "avg_deal_size":    "$23.5K",
    "win_rate_delta":   "-34.2%",
    "report_date":      "Apr 08, 2026",
    "data_source":      "HubSpot CRM | 132 deals analyzed",
}

# Then on every slide, reference KPI["pipeline_drop"] — never hardcode "-52.5%" again
```

After generation, run a consistency check:
```python
def check_kpi_consistency(path: str, kpi: dict) -> list[str]:
    """Flag slides that contain a value NOT in the canonical KPI dict."""
    import re
    prs = Presentation(path)
    issues = []
    # Extract all numbers/percentages across all slides
    all_texts = []
    for i, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if shape.has_text_frame:
                all_texts.append((i, shape.text_frame.text))
    # Check each known KPI value appears consistently
    for key, val in kpi.items():
        slides_with_val = [i for i, t in all_texts if val in t]
        if not slides_with_val:
            issues.append(f"KPI '{key}' ({val}) not found in any slide")
    return issues
```

---

### 3. Speaker notes must be populated for all data-heavy slides

Zero notes = presenter flies blind. **For any slide containing forecasts, tables, charts, or scenario ranges, always add notes explaining methodology and anticipated questions.**

```python
from pptx.util import Pt

def set_slide_notes(slide, notes_text: str):
    """Set speaker notes on a slide, creating the notes frame if absent."""
    notes_slide = slide.notes_slide
    tf = notes_slide.notes_text_frame
    tf.text = notes_text

# Example — call this for every data-heavy slide
set_slide_notes(slide3,
    "Pipeline drop of -52.5% calculated from 132 closed/lost deals vs same period last year. "
    "Excludes deals still in negotiation (18 open). "
    "Anticipated Q: Why exclude open deals? Answer: Including them would understate the drop "
    "since they haven't resolved yet."
)
```

After generation, verify notes exist on all slides:
```python
def verify_notes(path: str) -> list[str]:
    prs = Presentation(path)
    missing = []
    for i, slide in enumerate(prs.slides, 1):
        notes = slide.notes_slide.notes_text_frame.text.strip()
        if not notes:
            missing.append(f"Slide {i}: no speaker notes")
    return missing

missing = verify_notes("output.pptx")
if missing:
    print("⚠️  Slides missing notes:\n" + "\n".join(missing))
```

---

### 4. Slide count and section order must match the brief

Verify programmatically before export — don't assume the generation loop produced the right sequence.

```python
EXPECTED_SLIDE_COUNT = 8   # set from the brief
EXPECTED_TITLES = [        # in order
    "Executive Summary",
    "Pipeline Health",
    "Root Cause Analysis",
    "Rep Performance",
    "Deal Velocity",
    "Scenario Forecast",
    "Board Asks",
    "Appendix",
]

def verify_structure(path: str, expected_titles: list[str]) -> list[str]:
    prs = Presentation(path)
    issues = []
    if len(prs.slides) != len(expected_titles):
        issues.append(f"Expected {len(expected_titles)} slides, got {len(prs.slides)}")
    for i, (slide, expected) in enumerate(zip(prs.slides, expected_titles), 1):
        # Get title from title placeholder or first text shape
        title = ""
        for shape in slide.shapes:
            if shape.has_text_frame and shape.shape_type == 13:  # title placeholder
                title = shape.text_frame.text.strip()
                break
        if not title:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    title = shape.text_frame.text.strip().splitlines()[0]
                    break
        if expected.lower() not in title.lower():
            issues.append(f"Slide {i}: expected '{expected}', got '{title}'")
    return issues

issues = verify_structure("output.pptx", EXPECTED_TITLES)
if issues:
    print("⚠️  Structure issues:\n" + "\n".join(issues))
```

---

### 5. Footer must be generated from a single template variable — not typed per slide

Inconsistent footers (date missing on some slides, wrong data source on others) come from typing footer text per slide. **Define once, apply everywhere.**

```python
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

FOOTER = f"{KPI['data_source']} | {KPI['report_date']}  •  CONFIDENTIAL"

def add_footer(slide, prs, text: str = FOOTER):
    """Add a consistent footer bar to the bottom of a slide."""
    slide_w = prs.slide_width
    slide_h = prs.slide_height
    footer_h = Inches(0.28)

    # Dark background bar
    bar = slide.shapes.add_shape(
        1,  # MSO_SHAPE_TYPE.RECTANGLE
        left=0, top=slide_h - footer_h,
        width=slide_w, height=footer_h,
    )
    bar.fill.solid()
    bar.fill.fore_color.rgb = RGBColor(0x1a, 0x1a, 0x2e)
    bar.line.fill.background()

    # Footer text
    tf = bar.text_frame
    tf.word_wrap = False
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    run.text = text
    run.font.size = Pt(7)
    run.font.color.rgb = RGBColor(0xcc, 0xcc, 0xcc)
    run.font.name = "Calibri"

# Call for every slide after building its content
for slide in prs.slides:
    add_footer(slide, prs)
```

---

### 6. Audience tone must be locked at template level — not inferred per slide

Board deck → formal language, "Board Asks", no emoji. Internal review → conversational, structured bullets. **Set the tone constant at the top and enforce it.**

```python
# Set once at the top of your generation script
AUDIENCE = "board"   # or "internal", "client", "exec"

TONE_CONFIG = {
    "board": {
        "use_emoji":        False,
        "bullet_style":     "—",          # em-dash, no emoji
        "cta_label":        "Board Asks",
        "language":         "formal",
        "font":             "Calibri",
    },
    "internal": {
        "use_emoji":        False,         # never use emoji in PDFs/PPTX
        "bullet_style":     "•",
        "cta_label":        "Action Items",
        "language":         "direct",
        "font":             "Calibri",
    },
}

cfg = TONE_CONFIG[AUDIENCE]

# ❌ NEVER use emoji in slide text — they render as boxes on many systems
# ✅ Use cfg["bullet_style"] for all bullet characters
# ✅ Use cfg["cta_label"] for the call-to-action slide title
```

---

## QA (Required)

**Assume there are problems. Your job is to find them.**

Your first render is almost never correct. Approach QA as a bug hunt, not a confirmation step. If you found zero issues on first inspection, you weren't looking hard enough.

### Content QA

```bash
python -m markitdown output.pptx
```

Check for missing content, typos, wrong order.

**When using templates, check for leftover placeholder text:**

```bash
python -m markitdown output.pptx | grep -iE "xxxx|lorem|ipsum|this.*(page|slide).*layout"
```

If grep returns results, fix them before declaring success.

### Visual QA

**⚠️ USE SUBAGENTS** — even for 2-3 slides. You've been staring at the code and will see what you expect, not what's there. Subagents have fresh eyes.

Convert slides to images (see [Converting to Images](#converting-to-images)), then use this prompt:

```
Visually inspect these slides. Assume there are issues — find them.

Look for:
- Overlapping elements (text through shapes, lines through words, stacked elements)
- Text overflow or cut off at edges/box boundaries
- Decorative lines positioned for single-line text but title wrapped to two lines
- Source citations or footers colliding with content above
- Elements too close (< 0.3" gaps) or cards/sections nearly touching
- Uneven gaps (large empty area in one place, cramped in another)
- Insufficient margin from slide edges (< 0.5")
- Columns or similar elements not aligned consistently
- Low-contrast text (e.g., light gray text on cream-colored background)
- Low-contrast icons (e.g., dark icons on dark backgrounds without a contrasting circle)
- Text boxes too narrow causing excessive wrapping
- Leftover placeholder content

For each slide, list issues or areas of concern, even if minor.

Read and analyze these images:
1. /path/to/slide-01.jpg (Expected: [brief description])
2. /path/to/slide-02.jpg (Expected: [brief description])

Report ALL issues found, including minor ones.
```

### Verification Loop

1. Generate slides → Convert to images → Inspect
2. **List issues found** (if none found, look again more critically)
3. Fix issues
4. **Re-verify affected slides** — one fix often creates another problem
5. Repeat until a full pass reveals no new issues

**Do not declare success until you've completed at least one fix-and-verify cycle.**

---

## Converting to Images

Convert presentations to individual slide images for visual inspection:

```bash
python scripts/office/soffice.py --headless --convert-to pdf output.pptx
pdftoppm -jpeg -r 150 output.pdf slide
```

This creates `slide-01.jpg`, `slide-02.jpg`, etc.

To re-render specific slides after fixes:

```bash
pdftoppm -jpeg -r 150 -f N -l N output.pdf slide-fixed
```

---

## Dependencies

- `pip install "markitdown[pptx]"` - text extraction
- `pip install Pillow` - thumbnail grids
- `npm install -g pptxgenjs` - creating from scratch
- LibreOffice (`soffice`) - PDF conversion (auto-configured for sandboxed environments via `scripts/office/soffice.py`)
- Poppler (`pdftoppm`) - PDF to images
