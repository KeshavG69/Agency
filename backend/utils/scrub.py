"""Remove what a script can remove from anything we send out: hidden characters, the hidden
properties of the files we make, and the provenance metadata in images.

This is the deterministic half of keeping our outputs free of machine marks. It changes no wording,
so it is safe on every file and every email. (The other half, a model rewriting the writer's prose,
lives in utils/rewrite_pass.py and runs only where the proposal writer wrote the text.)

What it does:
- Text: drops invisible characters (zero-width characters, bidi controls, tag characters, variation
  selectors, soft hyphens) and turns look-alike spaces into plain spaces. The character tables follow
  guillaumemeyer/watermarks-remover (MIT), service/scripts/text_unicode.py. Emoji glue is not
  preserved: business documents and emails don't need it.
- Office files (docx/pptx/xlsx): the company is the author and the library that built the file isn't
  named anywhere (docProps/core.xml, docProps/app.xml); customXml, docProps/custom.xml, the template
  thumbnail and Office add-in parts are removed, with the references to them; hidden characters are
  stripped from every XML part; embedded images are cleaned as below.
- Images (PNG/JPEG/WebP): metadata is dropped without re-encoding, including C2PA Content
  Credentials (PNG caBX, JPEG APP11, WebP C2PA), EXIF and XMP. The pixels are untouched.
- PDF: the Producer and Creator strings are blanked in place, keeping every byte offset valid.
  Only plain literal strings are covered (what ReportLab writes); hex strings, compressed object
  streams and the page text itself are not.

Every function returns the input object itself when there was nothing to change.
"""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from xml.sax.saxutils import escape

# ---- text ---------------------------------------------------------------------------------------

_STRIP = {
    0x00AD, 0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5,
    0x180B, 0x180C, 0x180D, 0x180E, 0x180F,
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F,
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    *range(0x2060, 0x2065), *range(0x2065, 0x2070),   # word joiner, invisible operators, bidi isolates
    0xFEFF, *range(0xFE00, 0xFE10), 0x3164, 0xFFA0, *range(0xFFF0, 0xFFFC),
    *range(0xE0000, 0xE0080),                         # tag characters
    *range(0xE0100, 0xE01F0),                         # variation selectors supplement
}
_SPACES = {0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000}
_TABLE = {**{c: None for c in _STRIP}, **{c: " " for c in _SPACES}}
_ENTITY = re.compile(r"&#(x[0-9a-fA-F]+|[0-9]+);")


def clean_text(text: str) -> str:
    """`text` without invisible characters and with ordinary spaces."""
    if not text:
        return text
    out = text.translate(_TABLE)
    return text if out == text else out


def _clean_xml(xml: str) -> str:
    """The same, for XML, where a character can also be written as an entity (&#8203;)."""
    def entity(m: re.Match) -> str:
        code = int(m.group(1)[1:], 16) if m.group(1)[0] in "xX" else int(m.group(1))
        return "" if code in _STRIP else " " if code in _SPACES else m.group(0)

    return clean_text(_ENTITY.sub(entity, xml))


# ---- images -------------------------------------------------------------------------------------

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# Everything a PNG needs to look the same. All else (caBX = C2PA, tEXt/iTXt/zTXt, eXIf, private
# chunks) is metadata.
_PNG_KEEP = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP",
             b"pHYs", b"sBIT", b"bKGD", b"acTL", b"fcTL", b"fdAT"}


def _clean_png(data: bytes) -> bytes:
    out, i, changed = [_PNG_SIG], 8, False
    while i + 8 <= len(data):
        size = int.from_bytes(data[i:i + 4], "big")
        kind, end = data[i + 4:i + 8], i + 12 + size
        if end > len(data):
            return data  # truncated: leave it as it is rather than guess
        if kind in _PNG_KEEP:
            out.append(data[i:end])
        else:
            changed = True
        i = end
        if kind == b"IEND":
            break
    changed = changed or i < len(data)  # anything after the end is appended metadata
    return b"".join(out) if changed else data


def _clean_jpeg(data: bytes) -> bytes:
    """Drop every APPn segment except JFIF (APP0), the colour profile (APP2) and Adobe's colour
    flag (APP14), and comments. APP1 is EXIF/XMP, APP11 is C2PA. From the scan on it's copied as is."""
    out, i, changed = [b"\xff\xd8"], 2, False
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return data
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0xDA:  # start of scan: the image itself
            out.append(data[i:])
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            out.append(data[i:i + 2])
            i += 2
            continue
        end = i + 2 + int.from_bytes(data[i + 2:i + 4], "big")
        if end > len(data):
            return data
        if (0xE0 <= marker <= 0xEF and marker not in (0xE0, 0xE2, 0xEE)) or marker == 0xFE:
            changed = True
        else:
            out.append(data[i:end])
        i = end
    return b"".join(out) if changed else data


def _clean_webp(data: bytes) -> bytes:
    out, i, changed = [], 12, False
    while i + 8 <= len(data):
        kind, size = data[i:i + 4], int.from_bytes(data[i + 4:i + 8], "little")
        end = i + 8 + size + (size & 1)
        if end > len(data):
            return data
        if kind in (b"EXIF", b"XMP ", b"C2PA"):
            changed = True
        else:
            out.append(bytearray(data[i:end]))
        i = end
    if not changed:
        return data
    for chunk in out:
        if chunk[:4] == b"VP8X":
            chunk[8] &= ~0x0C & 0xFF  # the header no longer promises EXIF or XMP
    body = b"WEBP" + b"".join(out)
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def clean_image(data: bytes) -> bytes:
    if data.startswith(_PNG_SIG):
        return _clean_png(data)
    if data.startswith(b"\xff\xd8"):
        return _clean_jpeg(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _clean_webp(data)
    return data


# ---- PDF ----------------------------------------------------------------------------------------

_PDF_TOOL = re.compile(rb"/(?:Producer|Creator)\s*\(((?:\\.|[^\\()])*)\)")


def clean_pdf(data: bytes) -> bytes:
    """Blank the Producer and Creator strings with spaces of the same length, so the
    cross-reference table's byte offsets stay right."""
    def blank(m: re.Match) -> bytes:
        start, end = m.span(1)
        whole = m.group(0)
        return whole[:start - m.start()] + b" " * (end - start) + whole[end - m.start():]

    out = _PDF_TOOL.sub(blank, data)
    return data if out == data else out


# ---- Office -------------------------------------------------------------------------------------

_CORE = "docProps/core.xml"
_APP = "docProps/app.xml"
_SET = ("dc:creator", "cp:lastModifiedBy")               # who made it: the company
_CLEAR = ("dc:description", "cp:keywords", "dc:subject")  # tool notes: nothing
_LIBRARIES = re.compile(r"openpyxl|python-docx|python-pptx|xlsxwriter|reportlab|docx4j|aspose", re.I)
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


def _put(xml: str, tag: str, value: str) -> str:
    value = escape(value)
    full = re.compile(rf"<{tag}(\s[^>]*)?>.*?</{tag}>", re.S)
    empty = re.compile(rf"<{tag}(\s[^>]*)?/>")
    if full.search(xml):
        return full.sub(lambda m: f"<{tag}{m.group(1) or ''}>{value}</{tag}>", xml)
    if empty.search(xml):
        return empty.sub(lambda m: f"<{tag}{m.group(1) or ''}>{value}</{tag}>", xml)
    if value:  # absent: add it, so a viewer shows the company rather than nothing
        return xml.replace("</cp:coreProperties>", f"<{tag}>{value}</{tag}></cp:coreProperties>")
    return xml


def _dropped(name: str) -> bool:
    low = name.lower()
    return (low.startswith("customxml/") or low in ("docprops/custom.xml", "docprops/thumbnail.jpeg")
            or bool(re.match(r"^(?:word|xl|ppt)/webextensions/", low)))


def _rels_source_dir(rels_name: str) -> str:
    """'word/_rels/document.xml.rels' -> 'word'; '_rels/.rels' -> ''."""
    return posixpath.dirname(posixpath.dirname(rels_name))


def _prune_rels(xml: str, rels_name: str, gone: set[str]) -> str:
    base = _rels_source_dir(rels_name)

    def keep(m: re.Match) -> str:
        tag = m.group(0)
        if re.search(r'TargetMode\s*=\s*"External"', tag):
            return tag
        target = re.search(r'Target\s*=\s*"([^"]*)"', tag)
        if not target:
            return tag
        t = target.group(1)
        path = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(base, t))
        return "" if path.lower() in gone else tag

    return re.sub(r"<Relationship\b[^>]*?(?:/>|>.*?</Relationship>)", keep, xml, flags=re.S)


def _prune_content_types(xml: str, gone: set[str]) -> str:
    def keep(m: re.Match) -> str:
        part = re.search(r'PartName\s*=\s*"/?([^"]*)"', m.group(0))
        return "" if part and part.group(1).lower() in gone else m.group(0)

    return re.sub(r"<Override\b[^>]*/>", keep, xml)


def _clean_core(xml: str, author: str | None) -> str:
    """A file we made (`author` given): the company is its author and the tool's notes go. Any
    other file (a person's upload on its way out): only values that name a library are blanked,
    the rest is theirs."""
    if author is not None:
        for tag in _SET:
            xml = _put(xml, tag, author)
        for tag in _CLEAR:
            xml = _put(xml, tag, "")
        return xml
    for tag in (*_SET, *_CLEAR):
        m = re.search(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", xml, re.S)
        if m and _LIBRARIES.search(m.group(1)):
            xml = _put(xml, tag, "")
    return xml


def clean_office(content: bytes, author: str | None = None) -> bytes:
    """The same Office file, cleaned as described above. With `author`, that is its creator (a file
    we generated); without, a person's own properties are kept. Anything that isn't an Office file
    comes back untouched."""
    try:
        src = zipfile.ZipFile(io.BytesIO(content))
        names = src.namelist()
    except zipfile.BadZipFile:
        return content
    if "[Content_Types].xml" not in names:
        return content
    gone = {n.lower() for n in names if _dropped(n)}

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            name = info.filename
            if name.lower() in gone:
                continue
            data = src.read(name)
            low = name.lower()
            if low.endswith((".xml", ".rels")):
                xml = _clean_xml(data.decode("utf-8"))
                if name == _CORE:
                    xml = _clean_core(xml, author)
                elif name == _APP and _LIBRARIES.search(xml):
                    # openpyxl signs itself "Microsoft Excel Compatible / Openpyxl 3.1.5".
                    xml = re.sub(r"<Application>.*?</Application>", "<Application></Application>", xml, flags=re.S)
                    xml = re.sub(r"<AppVersion>.*?</AppVersion>", "", xml, flags=re.S)
                elif low.endswith(".rels"):
                    xml = _prune_rels(xml, name, gone)
                elif name == "[Content_Types].xml":
                    xml = _prune_content_types(xml, gone)
                data = xml.encode("utf-8")
            elif low.endswith(_IMAGE_EXT):
                data = clean_image(data)
            dst.writestr(info, data)
    result = out.getvalue()
    return content if result == content else result


# ---- one entry point ----------------------------------------------------------------------------

_OFFICE_EXT = (".docx", ".docm", ".dotx", ".pptx", ".pptm", ".xlsx", ".xlsm")
_TEXT_EXT = (".txt", ".md", ".csv", ".html", ".htm", ".json", ".xml", ".eml")


def clean_file(filename: str, content: bytes, author: str | None = None) -> bytes:
    """Clean `content` by what it is. Unknown kinds come back untouched."""
    low = (filename or "").lower()
    if low.endswith(_OFFICE_EXT):
        return clean_office(content, author)
    if low.endswith(_IMAGE_EXT) or content[:8] == _PNG_SIG or content[:2] == b"\xff\xd8":
        return clean_image(content)
    if low.endswith(".pdf") or content[:5] == b"%PDF-":
        return clean_pdf(content)
    if low.endswith(_TEXT_EXT):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return content
        out = clean_text(text)
        return content if out is text else out.encode("utf-8")
    return content
