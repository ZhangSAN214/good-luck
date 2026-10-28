"""Markdown 块 → PowerPoint（.pptx）。不加新依赖：直接用 zipfile 写 Office Open XML。

分页规则：第一页是标题页（文档标题 + 题目）；之后每个一 / 二级标题一页，
一页放不下时自动续页（标题加「（续）」）。三级及以下标题在页内显示为粗体小标题。
"""

from __future__ import annotations

import io
import math
import unicodedata
import zipfile
from xml.sax.saxutils import escape

from roundtable.core.export.markdown import Block, parse, plain, runs, title_and_sections

W, H = 12192000, 6858000  # 16:9
EMU = 12700  # 1 磅
FONT = "Microsoft YaHei"
MONO = "Consolas"
BODY_PT = 18
LINE_UNITS = 84  # 一行大约能放的半角字符数（正文 18 磅、文本框约 11.6 英寸宽）
PAGE_LINES = 13  # 一页最多的行数（估算）

_NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
)
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_OOX = "http://schemas.openxmlformats.org"
_PKG_NS = f"{_OOX}/package/2006"
_REL = f"{_OOX}/officeDocument/2006/relationships"
_OFF = "application/vnd.openxmlformats-officedocument"
_PKG = "application/vnd.openxmlformats-package"
_CT = f"{_OFF}.presentationml"


def _units(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _lines(b: Block) -> int:
    if b.kind == "code":
        return sum(max(1, math.ceil(_units(s) * 0.7 / LINE_UNITS)) for s in b.text.split("\n"))
    if b.kind == "table":
        return len(b.rows)
    if b.kind == "hr":
        return 1
    return max(1, math.ceil((_units(plain(b.text)) + 4 * b.level + 4) / LINE_UNITS))


def paginate(blocks: list[Block]) -> list[list[Block]]:
    pages: list[list[Block]] = [[]]
    used = 0
    for b in blocks:
        n = _lines(b)
        if pages[-1] and used + n > PAGE_LINES:
            pages.append([])
            used = 0
        pages[-1].append(b)
        used += n
    return [p for p in pages if p]


def _rpr(size: float, bold: bool = False, italic: bool = False, mono: bool = False) -> str:
    face = MONO if mono else FONT
    attrs = f'lang="zh-CN" sz="{int(size * 100)}"' + (' b="1"' if bold else "")
    attrs += ' i="1"' if italic else ""
    return (
        f'<a:rPr {attrs} dirty="0"><a:latin typeface="{face}"/>'
        f'<a:ea typeface="{FONT}"/><a:cs typeface="{face}"/></a:rPr>'
    )


def _para(text: str, size: float, ppr: str = "><a:buNone/>", bold: bool = False) -> str:
    rs = "".join(
        f"<a:r>{_rpr(size, bold or r.bold, r.italic, r.code)}<a:t>{escape(r.text)}</a:t></a:r>"
        for r in runs(text)
    )
    return f'<a:p><a:pPr{ppr}</a:pPr>{rs}<a:endParaRPr lang="zh-CN" sz="{int(size * 100)}"/></a:p>'


def _block_paras(b: Block) -> str:
    plain_ppr = ' marL="0" indent="0"><a:spcBef><a:spcPts val="600"/></a:spcBef><a:buNone/>'
    if b.kind == "bullet":
        mar = 342900 + 342900 * b.level
        bu = (
            '<a:buFont typeface="+mj-lt"/>'
            f'<a:buAutoNum type="arabicPeriod" startAt="{b.number or 1}"/>'
            if b.ordered
            else '<a:buFont typeface="Arial"/><a:buChar char="•"/>'
        )
        return _para(b.text, BODY_PT - 2 * b.level, f' marL="{mar}" indent="-342900">{bu}')
    if b.kind == "heading":
        return _para(b.text, BODY_PT + 2, plain_ppr, bold=True)
    if b.kind == "code":
        return "".join(
            f'<a:p><a:pPr marL="0" indent="0"><a:buNone/></a:pPr><a:r>{_rpr(12, mono=True)}'
            f"<a:t>{escape(s) or ' '}</a:t></a:r></a:p>"
            for s in b.text.split("\n")
        )
    if b.kind == "table":
        return "".join(
            _para(" ｜ ".join(row), BODY_PT - 2, plain_ppr, bold=(i == 0))
            for i, row in enumerate(b.rows)
        )
    if b.kind == "hr":
        return _para("—" * 12, BODY_PT, plain_ppr)
    if b.kind == "quote":
        return _para(b.text, BODY_PT - 2, plain_ppr.replace('marL="0"', 'marL="228600"'))
    return _para(b.text, BODY_PT, plain_ppr)


def _shape(sid: int, name: str, x: int, y: int, cx: int, cy: int, paras: str, anchor: str) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/><p:cNvSpPr txBox="1"/><p:nvPr/>'
        "</p:nvSpPr>"
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" lIns="0" rIns="0" anchor="{anchor}"><a:normAutofit/>'
        "</a:bodyPr>"
        f"<a:lstStyle/>{paras}</p:txBody></p:sp>"
    )


def _slide(shapes: str) -> str:
    return (
        f'{_XML}<p:sld {_NS}><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/>'
        "</a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree><p:nvGrpSpPr>"
        '<p:cNvPr id="1" name=""/>'
        '<p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/>'
        '<a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f"{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"
    )


def _bar() -> str:
    """标题下的一条细色带。"""
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="4" name="Bar"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{EMU * 54}" y="{EMU * 92}"/>'
        f'<a:ext cx="{EMU * 64}" cy="{EMU * 4}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="2F5597"/>'
        "</a:solidFill>"
        "<a:ln><a:noFill/></a:ln></p:spPr></p:sp>"
    )


def slides_of(md: str) -> list[tuple[str, list[Block]]]:
    """[(页标题, 内容块)]，第一项是标题页（内容为题目引用）。"""
    title, sections = title_and_sections(parse(md))
    cover: list[Block] = []
    if sections and not sections[0][0]:
        lead = sections[0][1]
        while lead and lead[0].kind == "quote":
            cover.append(lead.pop(0))
        if not lead:
            sections.pop(0)
        else:
            sections[0] = ("概要", lead)
    out: list[tuple[str, list[Block]]] = [(title or "圆桌成果", cover)]
    for name, blocks in sections:
        pages = paginate(blocks) or [[]]
        for n, page in enumerate(pages):
            label = name or title or "成果"
            out.append((label + ("（续）" if n else ""), page))
    return out


def _render_slide(index: int, name: str, blocks: list[Block]) -> str:
    margin = EMU * 54
    if index == 0:
        sub = "".join(_para(b.text, 18) for b in blocks)
        return _slide(
            _shape(
                2,
                "Title",
                margin,
                EMU * 150,
                W - 2 * margin,
                EMU * 110,
                _para(name, 36, bold=True),
                "b",
            )
            + _shape(
                3,
                "Subtitle",
                margin,
                EMU * 280,
                W - 2 * margin,
                EMU * 200,
                sub or _para("", 18),
                "t",
            )
        )
    body = "".join(_block_paras(b) for b in blocks) or _para("", BODY_PT)
    return _slide(
        _shape(
            2, "Title", margin, EMU * 28, W - 2 * margin, EMU * 60, _para(name, 28, bold=True), "b"
        )
        + _shape(3, "Body", margin, EMU * 110, W - 2 * margin, H - EMU * 140, body, "t")
        + _bar()
    )


_THEME = (
    f'{_XML}<a:theme xmlns:a="{_OOX}/drawingml/2006/main" name="Roundtable">'
    '<a:themeElements><a:clrScheme name="Roundtable">'
    '<a:dk1><a:srgbClr val="1F2328"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="1F3864"/></a:dk2><a:lt2><a:srgbClr val="EEF1F5"/></a:lt2>'
    '<a:accent1><a:srgbClr val="2F5597"/></a:accent1><a:accent2><a:srgbClr val="C55A11"/>'
    "</a:accent2>"
    '<a:accent3><a:srgbClr val="548235"/></a:accent3><a:accent4><a:srgbClr val="7F6000"/>'
    "</a:accent4>"
    '<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/>'
    "</a:accent6>"
    '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink>'
    '</a:clrScheme><a:fontScheme name="Roundtable">'
    f'<a:majorFont><a:latin typeface="{FONT}"/><a:ea typeface="{FONT}"/><a:cs typeface=""/>'
    "</a:majorFont>"
    f'<a:minorFont><a:latin typeface="{FONT}"/><a:ea typeface="{FONT}"/><a:cs typeface=""/>'
    "</a:minorFont>"
    '</a:fontScheme><a:fmtScheme name="Roundtable"><a:fillStyleLst>'
    + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
    + "</a:fillStyleLst><a:lnStyleLst>"
    + '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' * 3
    + "</a:lnStyleLst><a:effectStyleLst>"
    + "<a:effectStyle><a:effectLst/></a:effectStyle>" * 3
    + "</a:effectStyleLst><a:bgFillStyleLst>"
    + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
    + "</a:bgFillStyleLst></a:fmtScheme></a:themeElements><a:objectDefaults/>"
    "<a:extraClrSchemeLst/></a:theme>"
)

_EMPTY_TREE = (
    '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
    '<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree>'
)

_MASTER = (
    f'{_XML}<p:sldMaster {_NS}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/>'
    "</p:bgRef></p:bg>"
    f"{_EMPTY_TREE}</p:cSld>"
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
    'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" '
    'hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    "</p:sldMaster>"
)

_LAYOUT = (
    f'{_XML}<p:sldLayout {_NS} type="blank" preserve="1">'
    f'<p:cSld name="Blank">{_EMPTY_TREE}</p:cSld>'
    "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
)


def _rels(*items: tuple[str, str]) -> str:
    body = "".join(
        f'<Relationship Id="rId{n}" Type="{_REL}/{kind}" Target="{target}"/>'
        for n, (kind, target) in enumerate(items, 1)
    )
    return f'{_XML}<Relationships xmlns="{_PKG_NS}/relationships">{body}</Relationships>'


def render(md: str) -> bytes:
    slides = slides_of(md)
    n = len(slides)
    types = (
        f'<Default Extension="rels" ContentType="{_PKG}.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/ppt/presentation.xml" ContentType="{_CT}.presentation.main+xml"/>'
        f'<Override PartName="/ppt/slideMasters/slideMaster1.xml" '
        f'ContentType="{_CT}.slideMaster+xml"/>'
        f'<Override PartName="/ppt/slideLayouts/slideLayout1.xml" '
        f'ContentType="{_CT}.slideLayout+xml"/>'
        f'<Override PartName="/ppt/theme/theme1.xml" ContentType="{_OFF}.theme+xml"/>'
        f'<Override PartName="/docProps/core.xml" ContentType="{_PKG}.core-properties+xml"/>'
        f'<Override PartName="/docProps/app.xml" ContentType="{_OFF}.extended-properties+xml"/>'
        + "".join(
            f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="{_CT}.slide+xml"/>'
            for i in range(1, n + 1)
        )
    )
    title = escape(slides[0][0])
    presentation = (
        f'{_XML}<p:presentation {_NS} saveSubsetFonts="1">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        "<p:sldIdLst>"
        + "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 2}"/>' for i in range(1, n + 1))
        + f'</p:sldIdLst><p:sldSz cx="{W}" cy="{H}"/><p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            f'{_XML}<Types xmlns="{_PKG_NS}/content-types">{types}</Types>',
        )
        z.writestr(
            "_rels/.rels",
            _rels(
                ("officeDocument", "ppt/presentation.xml"),
                ("metadata/core-properties", "docProps/core.xml"),
                ("extended-properties", "docProps/app.xml"),
            ).replace(
                f"{_REL}/metadata/core-properties",
                f"{_PKG_NS}/relationships/metadata/core-properties",
            ),
        )
        z.writestr(
            "docProps/core.xml",
            f'{_XML}<cp:coreProperties xmlns:cp="{_PKG_NS}/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f"<dc:title>{title}</dc:title><dc:creator>圆桌</dc:creator></cp:coreProperties>",
        )
        z.writestr(
            "docProps/app.xml",
            f'{_XML}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            f"<Application>Roundtable</Application><Slides>{n}</Slides></Properties>",
        )
        z.writestr("ppt/presentation.xml", presentation)
        z.writestr(
            "ppt/_rels/presentation.xml.rels",
            _rels(
                ("slideMaster", "slideMasters/slideMaster1.xml"),
                ("theme", "theme/theme1.xml"),
                *[("slide", f"slides/slide{i}.xml") for i in range(1, n + 1)],
            ),
        )
        z.writestr("ppt/slideMasters/slideMaster1.xml", _MASTER)
        z.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            _rels(
                ("slideLayout", "../slideLayouts/slideLayout1.xml"),
                ("theme", "../theme/theme1.xml"),
            ),
        )
        z.writestr("ppt/slideLayouts/slideLayout1.xml", _LAYOUT)
        z.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            _rels(("slideMaster", "../slideMasters/slideMaster1.xml")),
        )
        z.writestr("ppt/theme/theme1.xml", _THEME)
        for i, (name, blocks) in enumerate(slides):
            z.writestr(f"ppt/slides/slide{i + 1}.xml", _render_slide(i, name, blocks))
            z.writestr(
                f"ppt/slides/_rels/slide{i + 1}.xml.rels",
                _rels(("slideLayout", "../slideLayouts/slideLayout1.xml")),
            )
    return buf.getvalue()
