"""Transactional Word candidate building, validation, publication and restore."""

from __future__ import annotations

import json
from io import BytesIO
from importlib.resources import files
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

import fcntl
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

from .configuration import effective_style
from .utils import atomic_json_write, file_sha256, now_compact_utc
from .workspace import Workspace


class PublicationError(RuntimeError):
    """Publication stopped before damaging a cumulative handout."""


_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "omega": "ω", "Delta": "Δ",
    "Sigma": "Σ", "Pi": "Π", "Phi": "Φ", "Omega": "Ω",
}


def _m(tag: str, text: str | None = None) -> OxmlElement:
    element = OxmlElement(f"m:{tag}")
    if text is not None:
        element.text = text
    return element


def _math_run(text: str, *, normal: bool = False) -> OxmlElement:
    run = _m("r")
    if normal:
        properties = _m("rPr")
        normal_text = _m("nor")
        normal_text.set(qn("m:val"), "1")
        properties.append(normal_text)
        run.append(properties)
    run.append(_m("t", text))
    return run


class _FormulaParser:
    """Small strict LaTeX parser for the equation forms used in lecture notes."""

    def __init__(self, source: str) -> None:
        # Providers sometimes double-escape LaTeX inside a JSON text field.
        # A single backslash is the actual TeX command introducer.
        value = source.strip().replace("\\\\", "\\")
        for left, right in (("$$", "$$"), ("\\[", "\\]"), ("$", "$")):
            if value.startswith(left) and value.endswith(right):
                value = value[len(left):-len(right)].strip()
                break
        self.source = value
        self.position = 0

    def parse(self) -> list[OxmlElement]:
        result = self._sequence(None)
        if self.position != len(self.source):
            raise PublicationError(f"Unsupported formula near: {self.source[self.position:]}")
        if not result:
            raise PublicationError("Formula is empty")
        return result

    def _sequence(self, stop: str | None) -> list[OxmlElement]:
        result: list[OxmlElement] = []
        plain = ""
        while self.position < len(self.source):
            char = self.source[self.position]
            if stop and char == stop:
                break
            if char == "\\":
                if plain:
                    result.append(_math_run(plain)); plain = ""
                result.extend(self._command())
                continue
            if char in "^_":
                if plain:
                    result.append(_math_run(plain)); plain = ""
                if not result:
                    raise PublicationError("A script has no base")
                base = result.pop()
                self.position += 1
                scripts: dict[str, list[OxmlElement]] = {char: self._argument()}
                if self.position < len(self.source) and self.source[self.position] in "^_" and self.source[self.position] != char:
                    other = self.source[self.position]; self.position += 1
                    scripts[other] = self._argument()
                tag = "sSubSup" if len(scripts) == 2 else ("sSup" if "^" in scripts else "sSub")
                node = _m(tag); node.append(_m("e")); node[-1].append(base)
                if "_" in scripts:
                    sub = _m("sub"); [sub.append(item) for item in scripts["_"]]; node.append(sub)
                if "^" in scripts:
                    sup = _m("sup"); [sup.append(item) for item in scripts["^"]]; node.append(sup)
                result.append(node)
                continue
            if char == "{":
                if plain:
                    result.append(_math_run(plain)); plain = ""
                result.extend(self._argument())
                continue
            if char == "}":
                break
            plain += char
            self.position += 1
        if plain:
            result.append(_math_run(plain))
        return result

    def _argument(self) -> list[OxmlElement]:
        while self.position < len(self.source) and self.source[self.position].isspace():
            self.position += 1
        if self.position >= len(self.source):
            raise PublicationError("Formula argument is missing")
        if self.source[self.position] == "{":
            self.position += 1
            value = self._sequence("}")
            if self.position >= len(self.source) or self.source[self.position] != "}":
                raise PublicationError("Unclosed formula group")
            self.position += 1
            return value
        if self.source[self.position] == "\\":
            return self._command()
        char = self.source[self.position]; self.position += 1
        return [_math_run(char)]

    def _literal_argument(self) -> str:
        """Keep words inside LaTeX text commands literal in Word equations."""
        while self.position < len(self.source) and self.source[self.position].isspace():
            self.position += 1
        if self.position >= len(self.source) or self.source[self.position] != "{":
            raise PublicationError("Formula text requires a braced argument")
        self.position += 1
        start = self.position
        depth = 1
        while self.position < len(self.source) and depth:
            if self.source[self.position] == "{":
                depth += 1
            elif self.source[self.position] == "}":
                depth -= 1
            self.position += 1
        if depth:
            raise PublicationError("Unclosed formula text")
        return self.source[start:self.position - 1]

    def _command(self) -> list[OxmlElement]:
        self.position += 1
        match = re.match(r"[A-Za-z]+", self.source[self.position:])
        if not match:
            if self.position >= len(self.source):
                raise PublicationError("Dangling formula escape")
            char = self.source[self.position]; self.position += 1
            return [_math_run(char)]
        name = match.group(0); self.position += len(name)
        if name in _GREEK:
            return [_math_run(_GREEK[name])]
        symbols = {
            "cdot": "·", "times": "×", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥",
            "neq": "≠", "infty": "∞", "pm": "±", "to": "→", "approx": "≈", "sim": "∼",
            "implies": "⇒", "quad": "  ", "dots": "…", "int": "∫",
            "max": "max", "min": "min",
        }
        if name in symbols:
            return [_math_run(symbols[name])]
        if name == "frac":
            numerator, denominator = self._argument(), self._argument()
            fraction = _m("f"); num, den = _m("num"), _m("den")
            [num.append(item) for item in numerator]; [den.append(item) for item in denominator]
            fraction.append(num); fraction.append(den)
            return [fraction]
        if name == "sqrt":
            rad = _m("rad"); props = _m("radPr"); hide = _m("degHide"); hide.set(qn("m:val"), "1")
            props.append(hide); rad.append(props); rad.append(_m("deg")); expression = _m("e")
            [expression.append(item) for item in self._argument()]; rad.append(expression)
            return [rad]
        if name in {"sum", "prod"}:
            # Word displays empty placeholders for an nary element without a
            # full expression. A symbol with native scripts stays editable and
            # renders correctly even when the model omits summation limits.
            return [_math_run("∑" if name == "sum" else "∏")]
        if name == "text":
            return [_math_run(self._literal_argument(), normal=True)]
        if name == "mathbb":
            items = self._argument()
            for item in items:
                for run in item.iter(qn("m:r")):
                    properties = run.find(qn("m:rPr"))
                    if properties is None:
                        properties = _m("rPr")
                        run.insert(0, properties)
                    script = _m("scr"); script.set(qn("m:val"), "double-struck")
                    style = _m("sty"); style.set(qn("m:val"), "p")
                    properties.append(script); properties.append(style)
                    # E/P script styling is ignored by some renderers; Unicode
                    # math letters keep the same notation and remain editable.
                    exceptions = {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"}
                    text = run.find(qn("m:t"))
                    if text is not None and text.text:
                        text.text = "".join(
                            exceptions.get(char, chr(0x1D538 + ord(char) - ord("A"))) if "A" <= char <= "Z"
                            else chr(0x1D552 + ord(char) - ord("a")) if "a" <= char <= "z"
                            else chr(0x1D7D8 + ord(char) - ord("0")) if "0" <= char <= "9"
                            else char for char in text.text
                        )
            return items
        if name in {"mathrm", "mathbf"}:
            return self._argument()
        raise PublicationError(f"Unsupported formula command: \\{name}")


def append_equation(document: Document, formula: str) -> None:
    paragraph = document.add_paragraph(style="MyNotes Body" if "MyNotes Body" in document.styles else None)
    math_para = _m("oMathPara"); math = _m("oMath")
    for item in _FormulaParser(formula).parse():
        math.append(item)
    math_para.append(math); paragraph._p.append(math_para)


def _marker(document: Document, value: str, *, page_break_before: bool = False) -> None:
    paragraph = document.add_paragraph(style="MyNotes Body" if "MyNotes Body" in document.styles else None); run = paragraph.add_run(value)
    run.font.hidden = True; run.font.size = Pt(1)
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = Pt(1)
    paragraph.paragraph_format.page_break_before = page_break_before


def _boundaries(document: Document, lecture_id: str) -> tuple[int, int] | None:
    children = list(document._body._element)
    starts = [index for index, item in enumerate(children) if f"MYNOTES START {lecture_id}" in "".join(item.itertext())]
    ends = [index for index, item in enumerate(children) if f"MYNOTES END {lecture_id}" in "".join(item.itertext())]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise PublicationError("Existing lecture boundaries are missing, duplicated or ambiguous")
    return starts[0], ends[0]


def _append_visual(document: Document, visual: dict[str, Any], decision: dict[str, Any]) -> None:
    image = Path(str(visual.get("image_path", ""))).expanduser()
    if not image.is_file():
        raise PublicationError(f"Selected visual is missing: {image}")
    paragraph = document.add_paragraph(style="MyNotes Body"); paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.keep_with_next = True
    paragraph.add_run().add_picture(str(image), width=Inches(5.4))
    caption = document.add_paragraph(style="MyNotes Caption")
    caption.paragraph_format.keep_with_next = False
    page = f", page {visual['page']}" if visual.get("page") is not None else ""
    source_name = Path(str(visual.get("source_file") or image.name)).name
    caption.add_run(f"Figure — {decision.get('reason', 'Relevant course visual')} ({source_name}{page})")


def _append_rows(document: Document, rows: list[list[str]]) -> None:
    if not rows:
        return
    width = max(len(row) for row in rows)
    table = document.add_table(rows=len(rows), cols=width)
    table.style = "Table Grid"
    table.autofit = True
    repeat_header = OxmlElement("w:tblHeader")
    table.rows[0]._tr.get_or_add_trPr().append(repeat_header)
    for row_index, row in enumerate(rows):
        table.rows[row_index]._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for column_index, value in enumerate(row):
            cell = table.cell(row_index, column_index)
            _append_inline(cell.paragraphs[0], str(value))
            for paragraph in cell.paragraphs:
                paragraph.style = document.styles["MyNotes Body"]
                for run in paragraph.runs:
                    if run.style.name == "Termine tecnico":
                        continue
                    if width >= 6:
                        run.font.size = Pt(8)
                    elif width >= 4:
                        run.font.size = Pt(9)
                    if row_index == 0 and run.style.name != "Termine tecnico":
                        run.bold = True


def _key_terms(blocks: list[dict[str, Any]]) -> tuple[str, ...]:
    """Use declared terms as a per-lecture vocabulary, never a hard-coded course list."""
    declared: set[str] = set()
    for block in blocks:
        for value in (block.get("text", ""), block.get("answer", ""), *block.get("items", [])):
            declared.update(match.group(1).strip() for match in re.finditer(r"\[\[term:([^\]]+)\]\]", str(value)))
    return tuple(sorted(declared, key=len, reverse=True))


def _append_inline(paragraph: Any, line: str, key_terms: tuple[str, ...] = ()) -> None:
    """Accent declared technical terms on recurring narrative uses."""
    term_pattern = (re.compile(r"(?<!\w)(?:" + "|".join(re.escape(term) for term in key_terms) + r")(?!\w)", re.IGNORECASE)
                    if key_terms else None)

    def append_plain(value: str) -> None:
        if not value:
            return
        if term_pattern is None:
            paragraph.add_run(value)
            return
        cursor = 0
        for found in term_pattern.finditer(value):
            if found.start() > cursor:
                paragraph.add_run(value[cursor:found.start()])
            paragraph.add_run(found.group()).style = "Termine tecnico"
            cursor = found.end()
        if cursor < len(value):
            paragraph.add_run(value[cursor:])

    token = re.compile(
        r"(?P<math>\$\$.+?\$\$|(?<!\$)\$(?!\$).+?(?<!\$)\$(?!\$))"
        r"|\[\[term:(?P<term>.+?)\]\]"
        r"|\*\*(?P<bold>.+?)\*\*"
        r"|(?<![\w*])\*(?!\*)(?P<italic>.+?)(?<!\*)\*(?![\w*$])"
        r"|`(?P<code>[^`]+)`"
    )
    cursor = 0
    for match in token.finditer(line):
        if match.start() > cursor:
            append_plain(line[cursor:match.start()])
        if match.group("math") is not None:
            math = _m("oMath")
            for item in _FormulaParser(match.group("math")).parse():
                math.append(item)
            paragraph._p.append(math)
            cursor = match.end()
            continue
        if match.group("bold") is not None and "$" in match.group("bold"):
            first = len(paragraph.runs)
            _append_inline(paragraph, match.group("bold"), key_terms)
            for nested in paragraph.runs[first:]:
                if nested.style.name != "Termine tecnico":
                    nested.bold = True
            cursor = match.end()
            continue
        if match.group("italic") is not None and "$" in match.group("italic"):
            first = len(paragraph.runs)
            _append_inline(paragraph, match.group("italic"), key_terms)
            for nested in paragraph.runs[first:]:
                if nested.style.name != "Termine tecnico":
                    nested.italic = True
            cursor = match.end()
            continue
        run = paragraph.add_run(next(match.group(name) for name in ("term", "bold", "italic", "code") if match.group(name) is not None))
        is_term = match.group("term") is not None or (match.group("bold") is not None and term_pattern is not None and term_pattern.fullmatch(run.text))
        if is_term:
            run.style = "Termine tecnico"
        elif match.group("bold") is not None:
            run.bold = True
        elif match.group("italic") is not None:
            run.italic = True
        cursor = match.end()
    if cursor < len(line):
        append_plain(line[cursor:])


def _append_text(document: Document, value: str, key_terms: tuple[str, ...] = ()) -> None:
    """Turn common provider Markdown residue into native Word elements."""
    text = str(value).replace("\\n", "\n")
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if (line.startswith("$$") and line.endswith("$$") and len(line) > 4) or (line.startswith("\\[") and line.endswith("\\]") and len(line) > 4):
            append_equation(document, line)
            index += 1
            continue
        if line.startswith("|") and line.endswith("|"):
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|") and lines[index].strip().endswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    rows.append(cells)
                index += 1
            if rows:
                _append_rows(document, rows)
            continue
        if line.startswith(("- ", "* ")):
            paragraph = document.add_paragraph(style="MyNotes List Bullet")
            paragraph._p.get_or_add_pPr().append(deepcopy(document.styles["List Bullet"]._element.pPr.numPr))
            line = line[2:]
        else:
            paragraph = document.add_paragraph(style="MyNotes Body")
        # The model sometimes returns Markdown emphasis inside JSON strings.
        if line.startswith("*") and line.endswith("*") and not line.startswith("**") and len(line) > 2:
            first_run = len(paragraph.runs)
            _append_inline(paragraph, line[1:-1], key_terms)
            for run in paragraph.runs[first_run:]:
                if run.style.name != "Termine tecnico":
                    run.italic = True
        else:
            _append_inline(paragraph, line, key_terms)
        index += 1


def _heading(document: Document, text: str, level: int) -> None:
    if level not in {2, 3, 4}:
        raise PublicationError(f"Unsupported heading level: {level}")
    document.add_paragraph(text, style=f"Heading {level - 1}")


def _new_numbered_list(document: Document) -> int:
    """Give each lecture overview its own Word numbering sequence."""
    numbering = document.part.numbering_part.element
    style_num = document.styles["List Number"]._element.pPr.numPr.numId.val
    existing = [item for item in numbering if item.tag == qn("w:num")]
    source = next((item for item in existing if int(item.get(qn("w:numId"))) == style_num), None)
    if source is None:
        raise PublicationError("Word numbering definition is missing")
    abstract_id = source.find(qn("w:abstractNumId")).get(qn("w:val"))
    number_id = max((int(item.get(qn("w:numId"))) for item in existing), default=0) + 1
    number = OxmlElement("w:num"); number.set(qn("w:numId"), str(number_id))
    abstract = OxmlElement("w:abstractNumId"); abstract.set(qn("w:val"), abstract_id)
    override = OxmlElement("w:lvlOverride"); override.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:startOverride"); start.set(qn("w:val"), "1")
    override.append(start); number.append(abstract); number.append(override); numbering.append(number)
    return number_id


def _set_numbered_item(paragraph: Any, number_id: int) -> None:
    properties = paragraph._p.get_or_add_pPr()
    numbering = OxmlElement("w:numPr")
    level = OxmlElement("w:ilvl"); level.set(qn("w:val"), "0")
    number = OxmlElement("w:numId"); number.set(qn("w:val"), str(number_id))
    numbering.append(level); numbering.append(number); properties.append(numbering)


def _lesson_heading(lecture_id: str, title: str) -> str:
    match = re.search(r"-L0*(\d+)$", lecture_id, flags=re.IGNORECASE)
    if not match:
        return title.strip()
    lesson_number = int(match.group(1))
    clean_title = re.sub(r"^\s*lecture\s+\d+\s*[:\-–—]\s*", "", title, flags=re.IGNORECASE).strip()
    return f"Lecture {lesson_number}: {clean_title}" if clean_title else f"Lecture {lesson_number}"


def _append_block(document: Document, notes: dict[str, Any], pack: dict[str, Any], *, page_break_before: bool = False, language: str = "en", recap_language: str = "it") -> None:
    lecture_id = notes["lecture_id"]
    _marker(document, f"MYNOTES START {lecture_id}", page_break_before=page_break_before)
    _heading(document, _lesson_heading(lecture_id, str(notes["title"])), 2)
    italian = language.lower().startswith("it")
    visuals = {str(item.get("evidence_id")): item for item in pack.get("visuals", [])}
    decisions = {str(item.get("evidence_id")): item for item in notes.get("visual_decisions", [])}
    blocks = notes.get("blocks") or []
    if blocks:
        key_terms = _key_terms(blocks)
        qa_heading_added = False
        for block in blocks:
            kind = block["kind"]
            if kind == "overview":
                _heading(document, "Argomenti della lezione" if italian else "Lecture overview", 3)
                number_id = _new_numbered_list(document)
                for item in block["items"]:
                    paragraph = document.add_paragraph(style="MyNotes List Number")
                    _set_numbered_item(paragraph, number_id)
                    _append_inline(paragraph, str(item))
                    for run in paragraph.runs:
                        if run.style.name != "Termine tecnico":
                            run.bold = True
            elif kind == "heading":
                _heading(document, block["text"], int(block["level"]))
            elif kind in {"paragraph", "recap"}:
                if kind == "recap":
                    recap_heading = "Riepilogo in italiano" if recap_language == "it" else ("Riepilogo" if italian else "Recap")
                    _heading(document, recap_heading, 3)
                first_paragraph = len(document.paragraphs)
                _append_text(document, block["text"], key_terms)
                if kind == "recap":
                    recap_paragraphs = document.paragraphs[first_paragraph:]
                    for paragraph in recap_paragraphs[:-1]:
                        paragraph.paragraph_format.keep_with_next = True
                    if recap_paragraphs:
                        recap_paragraphs[-1].paragraph_format.keep_together = True
            elif kind == "bullets":
                for item in block["items"]:
                    _append_text(document, f"- {item}", key_terms)
            elif kind == "qa":
                if not qa_heading_added:
                    _heading(document, "Risposte alle mie domande" if italian else "Answers to my questions", 3)
                    qa_heading_added = True
                question = document.add_paragraph(style="MyNotes Question")
                question.paragraph_format.keep_with_next = True
                question.add_run(str(block["question"]))
                _append_text(document, str(block["answer"]), key_terms)
            elif kind == "table":
                if block.get("label"):
                    _append_text(document, block["label"])
                    document.paragraphs[-1].paragraph_format.keep_with_next = True
                _append_rows(document, block["rows"])
            elif kind == "formula":
                if block.get("label"):
                    _append_text(document, block["label"])
                    document.paragraphs[-1].paragraph_format.keep_with_next = True
                append_equation(document, str(block["expression"]))
            elif kind == "visual":
                identifier = str(block["visual_id"])
                if identifier not in visuals or decisions.get(identifier, {}).get("decision") != "include":
                    raise PublicationError("Ordered visual is unavailable or not approved")
                _append_visual(document, visuals[identifier], decisions[identifier])
    else:
        if notes.get("summary"):
            _append_text(document, notes["summary"])
        if notes.get("bullets"):
            _heading(document, "Key takeaways", 3)
            for bullet in notes["bullets"]:
                _append_text(document, f"- {bullet}")
        for section in notes.get("sections", []):
            _heading(document, section["heading"], int(section["level"]))
            if section.get("body"):
                _append_text(document, section["body"])
            for bullet in section.get("bullets", []):
                _append_text(document, f"- {bullet}")
        _append_rows(document, notes.get("table", []))
        formulas = list(notes.get("formulas", []))
        if notes.get("formula") and all(item.get("expression") != notes["formula"] for item in formulas):
            formulas.insert(0, {"label": "Formula", "expression": notes["formula"]})
        if formulas:
            _heading(document, "Formulas", 3)
            for item in formulas:
                if item.get("label"):
                    _append_text(document, str(item["label"]))
                append_equation(document, str(item["expression"]))
        for identifier, decision in decisions.items():
            if decision.get("decision") == "include":
                visual = visuals.get(identifier)
                if not visual:
                    raise PublicationError("Selected visual does not exist in the source pack")
                _append_visual(document, visual, decision)
    _marker(document, f"MYNOTES END {lecture_id}")


def _profile_color(value: str, fallback: str) -> RGBColor:
    try:
        return RGBColor.from_string(value.lstrip("#"))
    except (TypeError, ValueError):
        return RGBColor.from_string(fallback)


def _configure_document(document: Document, profile: dict[str, Any], *, set_page_layout: bool = False) -> None:
    """Apply the selected study design and native Word heading hierarchy."""
    def named_style(name: str, base: str) -> Any:
        if name in document.styles:
            return document.styles[name]
        result = document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        result.base_style = document.styles[base]
        return result

    def named_character_style(name: str) -> Any:
        if name in document.styles:
            return document.styles[name]
        return document.styles.add_style(name, WD_STYLE_TYPE.CHARACTER)

    def set_font_family(style: Any, family: str) -> None:
        style.font.name = family
        fonts = style._element.get_or_add_rPr().rFonts
        if fonts is not None:
            for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "csTheme", "cstheme"):
                fonts.attrib.pop(qn(f"w:{attribute}"), None)
            fonts.set(qn("w:eastAsia"), family)
            fonts.set(qn("w:cs"), family)

    body_font = str(profile.get("body_font", "Aptos"))
    heading_font = str(profile.get("heading_font", "Aptos Display"))
    body = named_style("MyNotes Body", "Normal")
    set_font_family(body, body_font)
    body.font.size = Pt(float(profile.get("body_pt", 11)))
    body.paragraph_format.space_after = Pt(float(profile.get("body_after_pt", 3)))
    body.paragraph_format.line_spacing = float(profile.get("body_line_spacing", 1.15))
    term = named_character_style("Termine tecnico")
    set_font_family(term, body_font)
    term.font.bold = True
    term.quick_style = True
    term.hidden = False
    term.unhide_when_used = True
    term.priority = 20
    term.font.color.rgb = _profile_color(str(profile.get("accent_color", "ED7D31")), "ED7D31")
    heading_color = _profile_color(str(profile.get("heading_color", "0F4761")), "0F4761")
    for level, point_size in ((2, "lesson_heading_pt"), (3, "section_heading_pt"), (4, "subsection_heading_pt")):
        style = document.styles[f"Heading {level - 1}"]
        set_font_family(style, heading_font)
        style.font.size = Pt(float(profile.get(point_size, {2: 16, 3: 14, 4: 12}[level])))
        style.font.bold = True
        style.font.italic = False
        style.font.color.rgb = heading_color
        style.paragraph_format.space_before = Pt(float(profile.get("heading_before_pt", 8)))
        style.paragraph_format.space_after = Pt(float(profile.get("heading_after_pt", 4)))
        style.paragraph_format.keep_with_next = True
        properties = style._element.get_or_add_pPr()
        outline = properties.find(qn("w:outlineLvl"))
        if outline is None:
            outline = OxmlElement("w:outlineLvl")
            properties.append(outline)
        outline.set(qn("w:val"), str(level - 2))
        style.quick_style = True
        style.hidden = False
        style.priority = level - 1
    for name in ("List Number", "List Bullet"):
        style = named_style(f"MyNotes {name}", name)
        set_font_family(style, body_font)
        style.font.size = Pt(float(profile.get("body_pt", 11)))
        style.paragraph_format.space_after = Pt(3)
        if name == "List Bullet":
            # Unicode bullets avoid a private-use Symbol glyph disappearing in PDF.
            numbering = document.part.numbering_part.element
            num_id = document.styles[name]._element.pPr.numPr.numId.val
            number = next(item for item in numbering if item.tag == qn("w:num") and item.get(qn("w:numId")) == str(num_id))
            abstract_id = number.find(qn("w:abstractNumId")).get(qn("w:val"))
            abstract = next(item for item in numbering if item.tag == qn("w:abstractNum") and item.get(qn("w:abstractNumId")) == abstract_id)
            level = abstract.find(qn("w:lvl"))
            level.find(qn("w:lvlText")).set(qn("w:val"), "•")
            fonts = level.find(qn("w:rPr")).find(qn("w:rFonts"))
            fonts.set(qn("w:ascii"), body_font)
            fonts.set(qn("w:hAnsi"), body_font)
    caption = named_style("MyNotes Caption", "Caption")
    set_font_family(caption, body_font)
    caption.font.size = Pt(9)
    caption.font.bold = True
    caption.font.italic = True
    caption.font.color.rgb = _profile_color(str(profile.get("caption_color", "595959")), "595959")
    question = named_style("MyNotes Question", "Intense Quote")
    question.base_style = document.styles["Intense Quote"]
    set_font_family(question, body_font)
    question.font.size = Pt(float(profile.get("body_pt", 11)))
    question.font.color.rgb = _profile_color(str(profile.get("question_color", "4F81BD")), "4F81BD")
    question.font.bold = True
    question.font.italic = True
    question.paragraph_format.left_indent = Pt(46.8)
    question.paragraph_format.right_indent = Pt(46.8)
    question.paragraph_format.space_before = Pt(10)
    question.paragraph_format.space_after = Pt(14)
    question.paragraph_format.keep_with_next = True
    for section in document.sections:
        if set_page_layout:
            section.page_width = Cm(21)
            section.page_height = Cm(29.7)
            margin = float(profile.get("margin_cm", 1.5))
            section.top_margin = section.bottom_margin = Cm(margin)
            section.left_margin = section.right_margin = Cm(margin)
        if profile.get("page_numbers", True):
            _ensure_page_number(section)
    if profile.get("page_numbers", True):
        settings = document.settings._element
        update = settings.find(qn("w:updateFields"))
        if update is None:
            update = OxmlElement("w:updateFields")
            settings.append(update)
        update.set(qn("w:val"), "true")


def _ensure_page_number(section: Any) -> None:
    footer = section.footer
    if "PAGE" in footer._element.xml:
        return
    paragraph = footer.paragraphs[0] if footer.paragraphs and not footer.paragraphs[0].text else footer.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for kind in ("begin", "separate", "end"):
        run = OxmlElement("w:r")
        field = OxmlElement("w:fldChar")
        field.set(qn("w:fldCharType"), kind)
        run.append(field)
        paragraph._p.append(run)
        if kind == "begin":
            instruction = OxmlElement("w:r")
            text = OxmlElement("w:instrText")
            text.set(qn("xml:space"), "preserve")
            text.text = " PAGE "
            instruction.append(text)
            paragraph._p.append(instruction)
        elif kind == "separate":
            fallback = OxmlElement("w:r")
            text = OxmlElement("w:t")
            text.text = "1"
            fallback.append(text)
            paragraph._p.append(fallback)


def _new_document(course: dict[str, Any], profile: dict[str, Any]) -> Document:
    template = files("mynotes").joinpath("resources/base-template.docx")
    document = Document(BytesIO(template.read_bytes()))
    _configure_document(document, profile, set_page_layout=True)
    return document


def preview_style_document(profile: dict[str, Any], output_path: Path) -> Path:
    """Build a self-contained Word preview using synthetic teaching content."""
    from PIL import Image, ImageDraw, ImageFont

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = _new_document({"name": "Anteprima"}, profile)
    writing = profile.get("writing_style", "clear_study")
    explanations = {
        "concise": "Inventory helps meet demand, but carrying it has a cost.",
        "clear_study": "[[term:Inventory]] helps meet demand when replenishment is not immediate. The chosen quantity balances **service level** against holding cost.",
        "detailed": "[[term:Inventory]] helps meet demand when replenishment is not immediate. A larger quantity may reduce missed sales, but raises tied-up capital and holding cost. The choice therefore compares **service level**, demand and lead time together.",
    }
    bullet_counts = {"prose_first": 1, "balanced": 2, "list_first": 4}
    bullets = [
        "Estimate expected demand.",
        "Assess replenishment lead time.",
        "Compare service and cost.",
        "Revisit the choice when conditions change.",
    ][:bullet_counts.get(str(profile.get("list_density")), 2)]
    blocks: list[dict[str, Any]] = [
        {"kind": "overview", "items": ["Inventory and demand", "Service level", "Holding cost"]},
        {"kind": "heading", "text": "Inventory and service level", "level": 3},
        {"kind": "paragraph", "text": explanations.get(str(writing), explanations["clear_study"])},
        {"kind": "heading", "text": "How to assess the choice", "level": 4},
        {"kind": "bullets", "items": bullets},
        {"kind": "table", "rows": [["Period", "Demand", "Stock"], ["1", "100", "120"], ["2", "110", "120"]]},
        {"kind": "formula", "label": "Available stock after demand", "expression": "I_{end}=I_{start}-D"},
    ]
    visuals: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="mynotes-style-preview-") as temporary:
        if profile.get("image_policy", "essential") != "none":
            image_path = Path(temporary) / "diagramma-sintetico.png"
            image = Image.new("RGB", (900, 240), "white")
            draw = ImageDraw.Draw(image)
            fill, outline = "#E7F0F5", "#0F4761"
            font = ImageFont.load_default(size=28)
            for left, label in ((30, "Demand"), (340, "Inventory"), (650, "Service")):
                draw.rounded_rectangle((left, 55, left + 220, 185), radius=18, fill=fill, outline=outline, width=5)
                text_width = draw.textbbox((0, 0), label, font=font)[2]
                draw.text((left + (220 - text_width) / 2, 106), label, fill=outline, font=font)
            draw.line((250, 120, 335, 120), fill="#ED7D31", width=7)
            draw.line((560, 120, 645, 120), fill="#ED7D31", width=7)
            image.save(image_path)
            visuals.append({"evidence_id": "PREVIEW", "image_path": str(image_path), "source_file": "diagramma-sintetico.png"})
            decisions.append({"evidence_id": "PREVIEW", "decision": "include", "reason": "Synthetic preview diagram"})
            blocks.append({"kind": "visual", "visual_id": "PREVIEW"})
        if profile.get("image_policy") == "explanatory":
            blocks.append({"kind": "paragraph", "text": "Explanatory images accompany a step when they add useful information."})
        sample_questions = [
            ("Perché un'impresa mantiene una scorta quando il rifornimento non è immediato?", "La scorta copre la domanda mentre il rifornimento è in corso."),
            ("Come cambia il compromesso tra livello di servizio e costo quando aumenta la scorta?", "Una scorta maggiore può migliorare il servizio, ma aumenta il costo di mantenimento."),
        ]
        for question, answer in sample_questions[:min(int(profile.get("question_count", 5)), 2)]:
            blocks.append({"kind": "qa", "question": question, "answer": answer})
        if profile.get("recap", True):
            recap = "Inventory supports service, but carrying it has a cost." if profile.get("recap_language") in {"en", "same_as_notes"} else "Le scorte sostengono il servizio, ma comportano un costo."
            blocks.append({"kind": "recap", "text": recap})
        notes = {"lecture_id": "PREVIEW-L01", "title": "Layout example", "blocks": blocks, "visual_decisions": decisions}
        _append_block(document, notes, {"visuals": visuals}, recap_language=str(profile.get("recap_language", "it")))
        # This synthetic preview is never published: hidden lesson boundaries
        # are unnecessary and can create a blank trailing page when it fills.
        for paragraph in list(document.paragraphs):
            if paragraph.text in {"MYNOTES START PREVIEW-L01", "MYNOTES END PREVIEW-L01"}:
                paragraph._p.getparent().remove(paragraph._p)
        document.save(output_path)
    return output_path


def _replace_or_append(document: Document, notes: dict[str, Any], pack: dict[str, Any], *, language: str = "en", recap_language: str = "it") -> None:
    lecture_id = notes["lecture_id"]
    boundary = _boundaries(document, lecture_id)
    body = document._body._element
    before = list(body)
    preceding = before[:boundary[0]] if boundary is not None else before
    page_break_before = any(item.tag != qn("w:sectPr") and "".join(item.itertext()).strip() for item in preceding)
    _append_block(document, notes, pack, page_break_before=page_break_before, language=language, recap_language=recap_language)
    added = [item for item in list(body)[len(before):] if item.tag != qn("w:sectPr")]
    for item in added:
        body.remove(item)
    if boundary is None:
        insertion = len(body) - (1 if len(body) and body[-1].tag == qn("w:sectPr") else 0)
    else:
        start, end = boundary; insertion = start
        for item in before[start:end + 1]:
            body.remove(item)
    for item in added:
        body.insert(insertion, item); insertion += 1


def _course(configuration: dict[str, Any], code: str) -> dict[str, Any]:
    for course in configuration["courses"]:
        if course["code"] == code:
            return course
    raise PublicationError(f"Course is not configured: {code}")


def _target(course: dict[str, Any], workspace: Workspace) -> Path:
    folder = course["folders"].get("handout")
    return (Path(folder) if folder else workspace.runs_dir / "handouts") / f"{course['code']}_COURSE_NOTES.docx"


def _load_candidate(workspace: Workspace, lecture_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = workspace.runs_dir / lecture_id / "lecture_notes.candidate.json"
    try:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        pack = json.loads(Path(candidate["source_pack"]).read_text(encoding="utf-8"))
    except (OSError, KeyError, json.JSONDecodeError) as error:
        raise PublicationError(f"Cannot load candidate/source pack for {lecture_id}: {error}") from error
    if candidate.get("published_to_word") is True:
        pass
    if candidate.get("source_pack_hash") != file_sha256(Path(candidate["source_pack"])):
        raise PublicationError("Source pack changed after candidate generation")
    return candidate, pack


def _soffice() -> str | None:
    bundled = sorted((Path.home() / ".cache" / "codex-runtimes").glob("*/dependencies/bin/override/soffice"))
    candidates = [os.environ.get("MYNOTES_SOFFICE"), shutil.which("soffice"), "/Applications/LibreOffice.app/Contents/MacOS/soffice", *(str(item) for item in bundled)]
    return next((str(Path(item)) for item in candidates if item and Path(item).is_file()), None)


def _render_and_validate(docx: Path, output_dir: Path) -> dict[str, Any]:
    soffice = _soffice()
    if not soffice:
        raise PublicationError("LibreOffice/soffice is required to validate Word rendering before publication")
    output_dir.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="mynotes-lo-")
    try:
        completed = subprocess.run([soffice, f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf", "--outdir", str(output_dir), str(docx)], text=True, capture_output=True, timeout=180, check=False)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    pdf = output_dir / f"{docx.stem}.pdf"
    if completed.returncode != 0 or not pdf.is_file():
        raise PublicationError(f"Word render failed: {completed.stderr or completed.stdout}")
    rendered = pdfium.PdfDocument(pdf)
    pages = []
    try:
        for index, page in enumerate(rendered):
            text = page.get_textpage().get_text_range().strip()
            images = sum(1 for obj in page.get_objects() if obj.type == pdfium_raw.FPDF_PAGEOBJ_IMAGE)
            if not text and not images:
                raise PublicationError(f"Rendered Word contains an empty page at {index + 1}")
            pages.append({"page": index + 1, "characters": len(text), "images": images})
    finally:
        rendered.close()
    if not pages:
        raise PublicationError("Rendered Word has no pages")
    return {"pdf": str(pdf), "pages": pages, "renderer": soffice}


def _docx_structure(path: Path, lecture_id: str, expected_visuals: int, expected_formula: bool) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
    start = document_xml.count(f"MYNOTES START {lecture_id}".encode())
    end = document_xml.count(f"MYNOTES END {lecture_id}".encode())
    if (start, end) != (1, 1):
        raise PublicationError("Candidate Word does not contain exactly one lecture boundary pair")
    equations = document_xml.count(b"<m:oMath")
    if expected_formula and not equations:
        raise PublicationError("Formula was not written as a native Word equation")
    if len(media) < expected_visuals:
        raise PublicationError("One or more selected visuals were not embedded in Word")
    document = Document(path)
    in_lesson = False
    for paragraph in document.paragraphs:
        value = paragraph.text
        if f"MYNOTES START {lecture_id}" in value:
            in_lesson = True
            continue
        if f"MYNOTES END {lecture_id}" in value:
            break
        if in_lesson and ("\\n" in value or "**" in value or "[[term:" in value or re.search(r"(?m)^\s*\|[^|]+\|", value)):
            raise PublicationError("Candidate Word contains literal Markdown or escaped newlines")
    return {"markers": {"start": start, "end": end}, "equations": equations, "embedded_media": len(media)}


def build_word_candidate(workspace: Workspace, lecture_id: str) -> dict[str, Any]:
    lecture_id = lecture_id.upper(); code = lecture_id.split("-L", 1)[0]
    configuration = workspace.configuration_store().load(); course = _course(configuration, code)
    profile = effective_style(configuration, code)
    candidate, pack = _load_candidate(workspace, lecture_id); notes = candidate["notes"]
    if profile.get("image_policy") == "none" and any(item.get("decision") == "include" for item in notes.get("visual_decisions", [])):
        raise PublicationError("Image policy is none, but this candidate includes visuals; regenerate the notes with the current course style")
    target = _target(course, workspace); run_dir = workspace.runs_dir / lecture_id / "word"
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / f"{lecture_id}-{uuid.uuid4().hex[:10]}.candidate.docx"
    baseline_hash = file_sha256(target) if target.is_file() else None
    if target.is_file():
        document = Document(target)
        _configure_document(document, profile)
    else:
        document = _new_document(course, profile)
    _replace_or_append(document, notes, pack, language=str(course.get("languages", {}).get("notes", "en")), recap_language=str(profile.get("recap_language", "it")))
    document.save(output)
    expected_visuals = sum(1 for item in notes.get("visual_decisions", []) if item.get("decision") == "include")
    expected_formula = bool(notes.get("formula") or notes.get("formulas") or any(block.get("kind") == "formula" for block in notes.get("blocks", [])))
    structure = _docx_structure(output, lecture_id, expected_visuals, expected_formula)
    render = _render_and_validate(output, run_dir / f"{output.stem}-render")
    validation = {"lecture_id": lecture_id, "candidate": str(output), "candidate_hash": file_sha256(output), "target": str(target), "baseline_hash": baseline_hash, "structure": structure, "render": render, "validated_at": now_compact_utc()}
    atomic_json_write(output.with_suffix(".validation.json"), validation)
    from .workflow import WorkflowState
    state = WorkflowState(workspace.database_path)
    try:
        state.connection.execute("UPDATE candidate_runs SET status='WORD_CANDIDATE_VALIDATED', updated_at=? WHERE lecture_id=?", (now_compact_utc(), lecture_id))
        state.connection.commit()
    finally:
        state.close()
    return {"status": "WORD_CANDIDATE_VALIDATED", **validation}


@contextmanager
def _lock(target: Path) -> Iterator[None]:
    lock = target.with_suffix(target.suffix + ".mynotes.lock"); lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise PublicationError(f"Another publication is active for {target}") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _journal(workspace: Workspace, payload: dict[str, Any]) -> Path:
    directory = workspace.state_dir / "publication-transactions"; directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{payload['transaction_id']}.json"; atomic_json_write(path, payload); return path


def _archive(workspace: Workspace, target: Path, course_code: str, *, reason: str) -> dict[str, Any] | None:
    if not target.is_file():
        return None
    directory = workspace.archives_dir / "word" / course_code; directory.mkdir(parents=True, exist_ok=True)
    backup_id = f"{now_compact_utc()}-{file_sha256(target)[:12]}"
    path = directory / f"{backup_id}.docx"; shutil.copy2(target, path)
    metadata = {"backup_id": backup_id, "course": course_code, "path": str(path), "sha256": file_sha256(path), "source": str(target), "reason": reason, "created_at": now_compact_utc()}
    atomic_json_write(directory / f"{backup_id}.json", metadata); return metadata


def publish_word_candidate(workspace: Workspace, lecture_id: str, candidate_path: Path | None = None, *, accept_current_word: bool = False) -> dict[str, Any]:
    lecture_id = lecture_id.upper(); code = lecture_id.split("-L", 1)[0]
    from .workflow import WorkflowState
    state_bootstrap = WorkflowState(workspace.database_path); state_bootstrap.close()
    course = _course(workspace.configuration_store().load(), code); target = _target(course, workspace)
    if candidate_path is None:
        matches = sorted((workspace.runs_dir / lecture_id / "word").glob("*.candidate.docx"), key=lambda path: path.stat().st_mtime)
        if not matches:
            raise PublicationError("No validated Word candidate exists; run mynotes build first")
        candidate_path = matches[-1]
    validation_path = candidate_path.with_suffix(".validation.json")
    try:
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublicationError("Candidate has no valid render/structure report") from error
    if validation.get("candidate_hash") != file_sha256(candidate_path):
        raise PublicationError("Candidate changed after validation")
    transaction = {"transaction_id": uuid.uuid4().hex, "lecture_id": lecture_id, "target": str(target), "candidate": str(candidate_path), "candidate_hash": file_sha256(candidate_path), "baseline_hash": validation.get("baseline_hash"), "status": "PREPARED", "created_at": now_compact_utc()}
    journal = _journal(workspace, transaction)
    with _lock(target):
        current = file_sha256(target) if target.is_file() else None
        if current != transaction["baseline_hash"] and not accept_current_word:
            transaction["status"] = "NEEDS_REVIEW"; transaction["reason"] = "target_changed"; _journal(workspace, transaction)
            raise PublicationError("Cumulative Word changed after candidate creation; rebuild on the current file or explicitly accept it")
        transaction["backup"] = _archive(workspace, target, code, reason=f"before {lecture_id} publication")
        transaction["status"] = "BACKED_UP"; _journal(workspace, transaction)
        descriptor, temporary_name = tempfile.mkstemp(suffix=".docx", dir=target.parent); os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(candidate_path, temporary)
            if (file_sha256(target) if target.is_file() else None) != current:
                raise PublicationError("Cumulative Word changed during publication")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        transaction["status"] = "PUBLISHED"; transaction["target_hash"] = file_sha256(target); _journal(workspace, transaction)
        if transaction["target_hash"] != transaction["candidate_hash"]:
            raise PublicationError("Published Word hash does not match validated candidate")
        source_candidate = workspace.runs_dir / lecture_id / "lecture_notes.candidate.json"
        payload = json.loads(source_candidate.read_text(encoding="utf-8")); payload["published_to_word"] = True
        payload["published_path"] = str(target); payload["published_hash"] = transaction["target_hash"]
        atomic_json_write(source_candidate, payload)
        requested = payload.get("requested_model", {}); actual = payload.get("actual_model", {})
        sources = _load_candidate(workspace, lecture_id)[1].get("availability", {})
        with sqlite3.connect(workspace.database_path) as connection:
            row = connection.execute("SELECT input_hash FROM candidate_runs WHERE lecture_id=?", (lecture_id,)).fetchone()
            input_hash = row[0] if row else payload.get("source_pack_hash", "")
            connection.execute(
                """INSERT INTO lesson_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(lecture_id) DO UPDATE SET input_hash=excluded.input_hash,status=excluded.status,
                   provider=excluded.provider,model=excluded.model,effort=excluded.effort,quality=excluded.quality,
                   output_path=excluded.output_path,output_hash=excluded.output_hash,sources_json=excluded.sources_json,
                   updated_at=excluded.updated_at""",
                (lecture_id, input_hash, "PUBLISHED", actual.get("provider", requested.get("provider", "unknown")), actual.get("model", requested.get("model", "unknown")), actual.get("effort", requested.get("effort", "unknown")), payload.get("quality", requested.get("quality", "standard")), str(target), transaction["target_hash"], json.dumps(sources, sort_keys=True), now_compact_utc()),
            )
            connection.execute("UPDATE candidate_runs SET status='PUBLISHED', updated_at=? WHERE lecture_id=?", (now_compact_utc(), lecture_id))
        from .workflow import WorkflowState, _write_status_markdown
        state = WorkflowState(workspace.database_path)
        try:
            _write_status_markdown(workspace, state.rows())
        finally:
            state.close()
        transaction["status"] = "COMPLETED"; transaction["completed_at"] = now_compact_utc(); _journal(workspace, transaction)
    return {"status": "PUBLISHED", "lecture_id": lecture_id, "output": str(target), "backup": transaction.get("backup"), "transaction": str(journal)}


def publication_history(workspace: Workspace, course_code: str | None = None) -> list[dict[str, Any]]:
    root = workspace.archives_dir / "word"
    files = root.glob("*/*.json") if not course_code else (root / course_code.upper()).glob("*.json")
    result = []
    for path in sorted(files):
        try:
            result.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return result


def restore_backup(workspace: Workspace, backup_id: str) -> dict[str, Any]:
    matches = [item for item in publication_history(workspace) if item.get("backup_id") == backup_id]
    if len(matches) != 1:
        raise PublicationError("Backup id was not found or is ambiguous")
    metadata = matches[0]; source = Path(metadata["path"]); target = Path(metadata["source"])
    if not source.is_file() or file_sha256(source) != metadata["sha256"]:
        raise PublicationError("Backup is missing or corrupted")
    with _lock(target):
        safety = _archive(workspace, target, metadata["course"], reason=f"before restore {backup_id}")
        descriptor, name = tempfile.mkstemp(suffix=".docx", dir=target.parent); os.close(descriptor); temporary = Path(name)
        try:
            shutil.copy2(source, temporary); os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"status": "RESTORED", "backup_id": backup_id, "output": str(target), "pre_restore_backup": safety}


def recover_publications(workspace: Workspace) -> list[dict[str, Any]]:
    directory = workspace.state_dir / "publication-transactions"
    results = []
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") in {"COMPLETED", "ABORTED", "NEEDS_REVIEW"}:
            continue
        target = Path(payload["target"]); current = file_sha256(target) if target.is_file() else None
        if current == payload.get("candidate_hash"):
            payload["status"] = "COMPLETED"; payload["recovered_at"] = now_compact_utc()
        elif current == payload.get("baseline_hash") or (current is None and payload.get("baseline_hash") is None):
            payload["status"] = "ABORTED"; payload["recovered_at"] = now_compact_utc()
        else:
            payload["status"] = "NEEDS_REVIEW"; payload["reason"] = "ambiguous_recovery_state"
        atomic_json_write(path, payload); results.append(payload)
    return results
