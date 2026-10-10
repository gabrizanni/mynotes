#!/usr/bin/env python3
"""Distill a content-free Word template from a local design reference.

Only style, numbering, theme, and page geometry definitions are transferred.
The reference path is supplied by the operator and is never stored in the
package. The resulting document contains no body, media, or property parts.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
ET.register_namespace("w", W)

TRANSFERRED_PARTS = (
    "word/styles.xml",
    "word/numbering.xml",
    "word/theme/theme1.xml",
)
SECTION_CHILDREN = ("pgSz", "pgMar", "cols", "docGrid")


def _xml(element: ET.Element) -> bytes:
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def _relationship(identifier: str, type_suffix: str, target: str) -> ET.Element:
    return ET.Element(
        f"{{{REL}}}Relationship",
        {"Id": identifier, "Type": f"{OFFICE_REL}/{type_suffix}", "Target": target},
    )


def _child(parent: ET.Element, name: str, **values: str) -> ET.Element:
    child = parent.find(f"{{{W}}}{name}")
    if child is None:
        child = ET.SubElement(parent, f"{{{W}}}{name}")
    for key, value in values.items():
        child.set(f"{{{W}}}{key}", value)
    return child


def _study_design(styles_content: bytes) -> bytes:
    """Keep native Heading 1/2/3 and expose the technical-term character role."""
    styles = ET.fromstring(styles_content)
    for level, points in ((1, 16), (2, 14), (3, 12)):
        heading = next(style for style in styles if
            (style.find(f"{{{W}}}name") is not None and
             style.find(f"{{{W}}}name").get(f"{{{W}}}val", "").lower() == f"heading {level}"))
        heading.attrib.pop(f"{{{W}}}customStyle", None)
        _child(heading, "qFormat")
        _child(heading, "uiPriority", val=str(level))
        _child(heading, "unhideWhenUsed")
        hidden = heading.find(f"{{{W}}}semiHidden")
        if hidden is not None:
            heading.remove(hidden)
        paragraph = _child(heading, "pPr")
        _child(paragraph, "outlineLvl", val=str(level - 1))
        _child(paragraph, "keepNext")
        _child(paragraph, "spacing", before="160", after="80")
        run = _child(heading, "rPr")
        fonts = _child(run, "rFonts")
        fonts.attrib.clear()
        for name in ("ascii", "hAnsi", "eastAsia", "cs"):
            fonts.set(f"{{{W}}}{name}", "Aptos Display")
        _child(run, "sz", val=str(points * 2))
        _child(run, "b", val="1")
        _child(run, "i", val="0")
        color = _child(run, "color")
        color.attrib.clear()
        color.set(f"{{{W}}}val", "0F4761")
    term = next((style for style in styles if style.get(f"{{{W}}}styleId") == "Terminetecnico"), None)
    if term is None:
        term = ET.SubElement(styles, f"{{{W}}}style", {f"{{{W}}}type": "character", f"{{{W}}}customStyle": "1", f"{{{W}}}styleId": "Terminetecnico"})
    _child(term, "name", val="Termine tecnico")
    _child(term, "qFormat")
    _child(term, "unhideWhenUsed")
    _child(term, "uiPriority", val="20")
    hidden = term.find(f"{{{W}}}semiHidden")
    if hidden is not None:
        term.remove(hidden)
    run = _child(term, "rPr")
    fonts = _child(run, "rFonts")
    fonts.attrib.clear()
    for name in ("ascii", "hAnsi", "eastAsia", "cs"):
        fonts.set(f"{{{W}}}{name}", "Aptos")
    _child(run, "b", val="1")
    color = _child(run, "color")
    color.attrib.clear()
    color.set(f"{{{W}}}val", "ED7D31")
    return _xml(styles)


def build_template(reference: Path, output: Path) -> None:
    with ZipFile(reference) as source:
        definitions = {name: source.read(name) for name in TRANSFERRED_PARTS}
        for name, content in definitions.items():
            root = ET.fromstring(content)
            if any(element.text and element.text.strip() for element in root.iter()):
                raise ValueError(f"Definition part contains text content: {name}")

        original = ET.fromstring(source.read("word/document.xml"))
        sections = original.findall(f".//{{{W}}}sectPr")
        if len(sections) != 1:
            raise ValueError("A single-section design reference is required")
        source_section = sections[0]

    definitions["word/styles.xml"] = _study_design(definitions["word/styles.xml"])
    document = ET.Element(f"{{{W}}}document")
    body = ET.SubElement(document, f"{{{W}}}body")
    section = ET.SubElement(body, f"{{{W}}}sectPr")
    for child_name in SECTION_CHILDREN:
        source_child = source_section.find(f"{{{W}}}{child_name}")
        if source_child is not None:
            section.append(deepcopy(source_child))

    margins = _child(section, "pgMar")
    for side in ("top", "bottom", "left", "right"):
        margins.set(f"{{{W}}}{side}", "850")  # 1.5 cm in Word twips.

    types = ET.Element(f"{{{CT}}}Types")
    ET.SubElement(types, f"{{{CT}}}Default", {"Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"})
    ET.SubElement(types, f"{{{CT}}}Default", {"Extension": "xml", "ContentType": "application/xml"})
    for path, content_type in (
        ("/word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"),
        ("/word/styles.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"),
        ("/word/numbering.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"),
        ("/word/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
    ):
        ET.SubElement(types, f"{{{CT}}}Override", {"PartName": path, "ContentType": content_type})

    package_rels = ET.Element(f"{{{REL}}}Relationships")
    package_rels.append(_relationship("rId1", "officeDocument", "word/document.xml"))
    document_rels = ET.Element(f"{{{REL}}}Relationships")
    for identifier, relationship, target in (
        ("rId1", "styles", "styles.xml"),
        ("rId2", "numbering", "numbering.xml"),
        ("rId3", "theme", "theme/theme1.xml"),
    ):
        document_rels.append(_relationship(identifier, relationship, target))

    parts = {
        "[Content_Types].xml": _xml(types),
        "_rels/.rels": _xml(package_rels),
        "word/document.xml": _xml(document),
        "word/_rels/document.xml.rels": _xml(document_rels),
        **definitions,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(parts.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content, compress_type=ZIP_DEFLATED, compresslevel=9)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    build_template(arguments.reference, arguments.output)
