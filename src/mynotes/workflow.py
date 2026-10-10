"""Generic, local-first workflow and safe cumulative Word publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

import fcntl

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .configuration import ConfigurationError, effective_style
from .providers import (
    ProviderAdapter,
    ProviderError,
    ProviderQuotaError,
    ProviderRequest,
    ProviderResponse,
    ProviderUnavailableError,
    RequestedModel,
    with_phase,
    invoke_provider,
)
from .utils import atomic_json_write, file_sha256, now_compact_utc
from .workspace import Workspace

LECTURE_ID_RE = re.compile(r"^(?P<course>[A-Z0-9]+)-L(?P<number>[0-9]{2})$")
QUALITY_ALIASES = {
    "base": "base",
    "fast": "base",
    "standard": "standard",
    "approfondito": "approfondito",
    "scoped-review": "approfondito",
    "revisione": "revisione",
    "full-review": "revisione",
}

QUALITY_PHASES: dict[str, tuple[tuple[str, str], ...]] = {
    # Italian names are deliberately the persistent/public names.  English
    # aliases are accepted above only for terminal convenience.
    "base": (("writing", "reduced"),),
    "standard": (("writing", "thorough"),),
    "approfondito": (("writing", "thorough"), ("scoped_review", "thorough")),
    "revisione": (("writing", "thorough"), ("evidence_audit", "thorough"), ("editorial_review", "thorough")),
}
GENERATION_CONTRACT_VERSION = 6


class WorkflowError(RuntimeError):
    pass


def parse_lecture_id(value: str) -> tuple[str, int]:
    match = LECTURE_ID_RE.fullmatch(value.upper())
    if not match:
        raise WorkflowError("Lecture id must use COURSE-LNN with an alphanumeric course code")
    return match["course"], int(match["number"])


def normalize_quality(value: str) -> str:
    try:
        return QUALITY_ALIASES[value.casefold()]
    except KeyError as error:
        raise WorkflowError(f"Unknown quality level: {value}") from error


def resolve_model(configuration: dict[str, Any], provider: str | None, quality: str) -> RequestedModel:
    selected = provider or configuration["providers"]["default"]
    if not selected:
        raise WorkflowError("No provider is selected; configure codex or agy before processing")
    options = configuration["providers"]["configured"].get(selected)
    if not isinstance(options, dict):
        raise WorkflowError(f"Provider is not configured: {selected}")
    model = str(options.get("model", "")).strip()
    effort = str(options.get("effort", "")).strip()
    if not model or not effort:
        raise WorkflowError(f"Provider {selected} requires an explicit model and effort")
    return RequestedModel(
        selected,
        model,
        effort,
        normalize_quality(quality),
    )


class WorkflowState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS lesson_runs (
                lecture_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, status TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL,
                quality TEXT NOT NULL, output_path TEXT NOT NULL, output_hash TEXT NOT NULL,
                sources_json TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS candidate_runs (
                lecture_id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, status TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL,
                quality TEXT NOT NULL, candidate_path TEXT NOT NULL, candidate_hash TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS provider_attempts (
                attempt_id TEXT PRIMARY KEY, lecture_id TEXT NOT NULL,
                requested_provider TEXT NOT NULL, requested_model TEXT NOT NULL,
                requested_effort TEXT NOT NULL, quality TEXT NOT NULL,
                actual_provider TEXT, actual_model TEXT, actual_effort TEXT,
                status TEXT NOT NULL, usage_json TEXT NOT NULL,
                error TEXT, created_at TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS source_packs (
                lecture_id TEXT PRIMARY KEY, source_pack_path TEXT NOT NULL,
                source_pack_hash TEXT NOT NULL, status TEXT NOT NULL,
                availability_json TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )

    def close(self) -> None:
        self.connection.close()

    def completed(self, lecture_id: str, input_hash: str) -> bool:
        row = self.connection.execute(
            "SELECT status, input_hash FROM lesson_runs WHERE lecture_id=?", (lecture_id,)
        ).fetchone()
        return bool(row and row[0] == "PUBLISHED" and row[1] == input_hash)

    def candidate_completed(self, lecture_id: str, input_hash: str) -> bool:
        row = self.connection.execute(
            "SELECT status, input_hash, candidate_path FROM candidate_runs WHERE lecture_id=?", (lecture_id,)
        ).fetchone()
        return bool(row and row[0] in {"CANDIDATE_READY", "WORD_CANDIDATE_VALIDATED", "PUBLISHED"} and row[1] == input_hash and Path(row[2]).is_file())

    def record_candidate(self, lecture_id: str, input_hash: str, actual: ProviderResponse, quality: str, candidate: Path) -> None:
        self.connection.execute(
            """INSERT INTO candidate_runs VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(lecture_id) DO UPDATE SET
               input_hash=excluded.input_hash,status=excluded.status,provider=excluded.provider,
               model=excluded.model,effort=excluded.effort,quality=excluded.quality,
               candidate_path=excluded.candidate_path,candidate_hash=excluded.candidate_hash,
               updated_at=excluded.updated_at""",
            (lecture_id, input_hash, "CANDIDATE_READY", actual.provider, actual.model, actual.effort,
             quality, str(candidate), file_sha256(candidate), now_compact_utc()),
        )
        self.connection.commit()

    def expected_output_hash(self, output: Path) -> str | None:
        row = self.connection.execute(
            "SELECT output_hash FROM lesson_runs WHERE output_path=? AND status='PUBLISHED' "
            "ORDER BY updated_at DESC LIMIT 1",
            (str(output),),
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def record_attempt(
        self,
        lecture_id: str,
        requested: RequestedModel,
        *,
        status: str,
        response: ProviderResponse | None = None,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO provider_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex,
                lecture_id,
                requested.provider,
                requested.model,
                requested.effort,
                requested.quality,
                response.provider if response else None,
                response.model if response else None,
                response.effort if response else None,
                status,
                json.dumps(response.usage if response else {}, sort_keys=True),
                error,
                now_compact_utc(),
            ),
        )
        self.connection.commit()

    def record_nonpublished(
        self,
        lecture_id: str,
        input_hash: str,
        requested: RequestedModel,
        output: Path,
        sources: dict[str, bool],
        status: str,
    ) -> None:
        output_hash = file_sha256(output) if output.is_file() else ""
        self.connection.execute(
            """INSERT INTO lesson_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(lecture_id) DO UPDATE SET
               input_hash=excluded.input_hash,status=excluded.status,provider=excluded.provider,
               model=excluded.model,effort=excluded.effort,quality=excluded.quality,
               output_path=excluded.output_path,output_hash=excluded.output_hash,
               sources_json=excluded.sources_json,updated_at=excluded.updated_at""",
            (
                lecture_id, input_hash, status, requested.provider, requested.model, requested.effort,
                requested.quality, str(output), output_hash, json.dumps(sources, sort_keys=True), now_compact_utc(),
            ),
        )
        self.connection.commit()

    def record(self, lecture_id: str, input_hash: str, actual: ProviderResponse, quality: str, output: Path, sources: dict[str, bool]) -> None:
        self.connection.execute(
            """INSERT INTO lesson_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(lecture_id) DO UPDATE SET
               input_hash=excluded.input_hash,status=excluded.status,provider=excluded.provider,
               model=excluded.model,effort=excluded.effort,quality=excluded.quality,
               output_path=excluded.output_path,output_hash=excluded.output_hash,
               sources_json=excluded.sources_json,updated_at=excluded.updated_at""",
            (
                lecture_id, input_hash, "PUBLISHED", actual.provider, actual.model, actual.effort,
                quality, str(output), file_sha256(output), json.dumps(sources, sort_keys=True), now_compact_utc(),
            ),
        )
        self.connection.commit()

    def rows(self) -> list[dict[str, str]]:
        cursor = self.connection.execute(
            "SELECT lecture_id,status,provider,model,quality,output_path,updated_at FROM lesson_runs ORDER BY lecture_id"
        )
        rows = [
            dict(zip(("lecture_id", "status", "provider", "model", "quality", "output_path", "updated_at"), row))
            for row in cursor.fetchall()
        ]
        by_lecture = {item["lecture_id"]: item for item in rows}
        candidates = self.connection.execute(
            "SELECT lecture_id,status,provider,model,quality,candidate_path,updated_at FROM candidate_runs ORDER BY lecture_id"
        ).fetchall()
        for row in candidates:
            candidate = dict(zip(("lecture_id", "status", "provider", "model", "quality", "output_path", "updated_at"), row))
            # A successful candidate is a more recent, recoverable state than
            # an earlier provider failure. A published Word remains decisive.
            if candidate["lecture_id"] not in by_lecture or by_lecture[candidate["lecture_id"]]["status"] != "PUBLISHED":
                by_lecture[candidate["lecture_id"]] = candidate
        known = set(by_lecture)
        prepared = self.connection.execute(
            "SELECT lecture_id,status,source_pack_path,updated_at FROM source_packs ORDER BY lecture_id"
        ).fetchall()
        rows = list(by_lecture.values())
        rows.extend(
            {
                "lecture_id": row[0], "status": row[1], "provider": "", "model": "", "quality": "",
                "output_path": row[2], "updated_at": row[3],
            }
            for row in prepared if row[0] not in known
        )
        return sorted(rows, key=lambda item: item["lecture_id"])

    def record_source_pack(self, lecture_id: str, path: Path, availability: dict[str, Any]) -> None:
        self.connection.execute(
            """INSERT INTO source_packs VALUES(?,?,?,?,?,?)
               ON CONFLICT(lecture_id) DO UPDATE SET
               source_pack_path=excluded.source_pack_path,source_pack_hash=excluded.source_pack_hash,
               status=excluded.status,availability_json=excluded.availability_json,updated_at=excluded.updated_at""",
            (
                lecture_id, str(path), file_sha256(path), "PREPARED",
                json.dumps(availability, sort_keys=True), now_compact_utc(),
            ),
        )
        self.connection.commit()


def status(workspace: Workspace) -> list[dict[str, str]]:
    state = WorkflowState(workspace.database_path)
    try:
        return state.rows()
    finally:
        state.close()


def _course(configuration: dict[str, Any], code: str) -> dict[str, Any]:
    for course in configuration["courses"]:
        if course["code"] == code:
            return course
    raise WorkflowError(f"Course is not configured: {code}")


def _text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise WorkflowError(f"Cannot read text source: {path}") from error


def _sources(
    transcript: Path,
    materials: Path | None,
    personal_notes: Path | None,
    web_source: Path | None,
    course: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, str]]:
    values = {
        "transcript": _text(transcript),
        "materials": _text(materials),
        "personal_notes": _text(personal_notes),
        "web": _text(web_source),
    }
    available = {key: bool(value) for key, value in values.items()}
    return available, values


def _validate_notes(notes: dict[str, Any], lecture_id: str) -> dict[str, Any]:
    required = {"title": str, "summary": str, "bullets": list, "table": list}
    for key, expected in required.items():
        if not isinstance(notes.get(key), expected):
            raise WorkflowError(f"Provider notes require {key} as {expected.__name__}")
    if not all(isinstance(item, str) and item.strip() for item in notes["bullets"]):
        raise WorkflowError("Provider note bullets must be non-empty text")
    result = dict(notes)
    result["lecture_id"] = lecture_id
    result.setdefault("formula", None)
    result.setdefault("formulas", [])
    if not isinstance(result["formulas"], list) or any(not isinstance(item, dict) or not str(item.get("expression", "")).strip() for item in result["formulas"]):
        raise WorkflowError("Provider formulas must be a list of labeled mathematical expressions")
    return result


def _hidden_marker(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.font.hidden = True
    run.font.size = Pt(1)


def _native_formula(document: Document, formula: str) -> None:
    paragraph = document.add_paragraph()
    math = OxmlElement("m:oMath")
    run = OxmlElement("m:r")
    text = OxmlElement("m:t")
    text.text = formula
    run.append(text)
    math.append(run)
    paragraph._p.append(math)


def _table(document: Document, rows: list[tuple[str, str]]) -> None:
    if not rows:
        return
    table = document.add_table(rows=len(rows), cols=2)
    table.style = "Table Grid"
    table_properties = table._tbl.tblPr
    old_borders = table_properties.find(qn("w:tblBorders"))
    if old_borders is not None:
        table_properties.remove(old_borders)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{edge}")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "4")
        border.set(qn("w:color"), "D9D9D9")
        borders.append(border)
    table_properties.append(borders)
    for row_index, values in enumerate(rows):
        for column_index, value in enumerate(values):
            cell = table.cell(row_index, column_index)
            cell.text = value
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            properties = cell._tc.get_or_add_tcPr()
            margins = OxmlElement("w:tcMar")
            for side in ("top", "left", "bottom", "right"):
                margin = OxmlElement(f"w:{side}")
                margin.set(qn("w:w"), "100")
                margin.set(qn("w:type"), "dxa")
                margins.append(margin)
            properties.append(margins)
            if row_index == 0:
                shading = OxmlElement("w:shd")
                shading.set(qn("w:fill"), "D9EAF7")
                properties.append(shading)
                for run in cell.paragraphs[0].runs:
                    run.bold = True


def _style(document: Document) -> None:
    document.styles["Normal"].font.name = "Aptos"
    document.styles["Normal"].font.size = Pt(11)
    for name in ("Title", "Heading 2", "Heading 3", "Heading 4"):
        style = document.styles[name]
        style.font.name = "Aptos"
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.italic = False
        style.font.bold = name != "Title"
    title_properties = document.styles["Title"]._element.get_or_add_pPr()
    title_border = title_properties.find(qn("w:pBdr"))
    if title_border is not None:
        title_properties.remove(title_border)


def _append_lesson(document: Document, notes: dict[str, Any], sources: dict[str, bool]) -> None:
    _hidden_marker(document, f"MYNOTES START {notes['lecture_id']}")
    document.add_heading(f"Lecture {notes['lecture_id']}", level=2)
    document.add_heading(notes["title"], level=3)
    document.add_paragraph(notes["summary"])
    if notes["table"]:
        document.add_heading("Illustrative values", level=4)
        _table(document, notes["table"])
    document.add_heading("Key points", level=4)
    for item in notes["bullets"]:
        document.add_paragraph(item, style="List Bullet")
    if notes["formula"]:
        document.add_heading("Formula", level=4)
        _native_formula(document, notes["formula"])
    document.add_heading("Available sources", level=4)
    document.add_paragraph(", ".join(name.replace("_", " ") for name, present in sources.items() if present) or "No optional source available.")
    _hidden_marker(document, f"MYNOTES END {notes['lecture_id']}")


def _replace_block(document: Document, lecture_id: str, notes: dict[str, Any], sources: dict[str, bool]) -> None:
    children = list(document._body._element)
    starts = [index for index, item in enumerate(children) if f"MYNOTES START {lecture_id}" in "".join(item.itertext())]
    ends = [index for index, item in enumerate(children) if f"MYNOTES END {lecture_id}" in "".join(item.itertext())]
    if not starts and not ends:
        _append_lesson(document, notes, sources)
        return
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise WorkflowError("Existing lecture boundaries are ambiguous; cumulative document was not changed")
    temporary = Document()
    _style(temporary)
    _append_lesson(temporary, notes, sources)
    replacement = [deepcopy(item) for item in list(temporary._body._element) if item.tag != qn("w:sectPr")]
    body = document._body._element
    insertion = starts[0]
    for item in children[starts[0] : ends[0] + 1]:
        body.remove(item)
    for item in replacement:
        body.insert(insertion, item)
        insertion += 1


def _output(course: dict[str, Any], workspace: Workspace) -> Path:
    folder = course["folders"]["handout"]
    root = Path(folder) if folder else workspace.runs_dir / "handouts"
    return root / f"{course['code']}_COURSE_NOTES.docx"


@contextmanager
def _publication_lock(target: Path) -> Iterator[None]:
    lock_path = target.with_suffix(target.suffix + ".mynotes.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkflowError(f"Publication already active for: {target}") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _check_word_conflict(state: WorkflowState, target: Path, *, accept_current_word: bool) -> str | None:
    expected = state.expected_output_hash(target)
    if expected is None:
        return file_sha256(target) if target.is_file() else None
    current = file_sha256(target) if target.is_file() else None
    if current != expected and not accept_current_word:
        raise WorkflowError(
            "Cumulative Word changed after the last MyNotes publication; no file was overwritten. "
            "Review it and rerun with explicit acceptance of the current Word."
        )
    return current


def process(
    workspace: Workspace,
    lecture_id: str,
    transcript: Path,
    *,
    materials: Path | None = None,
    personal_notes: Path | None = None,
    web_source: Path | None = None,
    provider: str | None = None,
    quality: str = "standard",
    redo: bool = False,
    accept_current_word: bool = False,
    provider_adapter: ProviderAdapter | None = None,
) -> dict[str, Any]:
    """Publish one generic lecture and safely skip identical inputs on rerun."""
    course_code, _ = parse_lecture_id(lecture_id)
    configuration = workspace.configuration_store().load()
    course = _course(configuration, course_code)
    requested = resolve_model(configuration, provider, quality)
    sources, texts = _sources(transcript, materials, personal_notes, web_source, course)
    input_hash = hashlib.sha256(
        json.dumps(
            {"texts": texts, "quality": requested.quality, "course": course, "provider": requested.__dict__},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    target = _output(course, workspace)
    with _publication_lock(target):
        state = WorkflowState(workspace.database_path)
        try:
            baseline_hash = _check_word_conflict(state, target, accept_current_word=accept_current_word)
            if not redo and state.completed(lecture_id, input_hash):
                return {"lecture_id": lecture_id, "status": "SKIPPED", "reason": "identical completed input"}
            run_dir = workspace.runs_dir / lecture_id
            run_dir.mkdir(parents=True, exist_ok=True)
            request = ProviderRequest(lecture_id, course, texts, requested)
            try:
                response = invoke_provider(request, provider_adapter)
            except ProviderQuotaError as error:
                state.record_attempt(lecture_id, requested, status="QUOTA_EXHAUSTED", error=str(error))
                state.record_nonpublished(lecture_id, input_hash, requested, target, sources, "PAUSED_QUOTA")
                _write_status_markdown(workspace, state.rows())
                raise WorkflowError(f"Provider quota exhausted; progress was saved as PAUSED_QUOTA: {error}") from error
            except ProviderUnavailableError as error:
                state.record_attempt(lecture_id, requested, status="UNAVAILABLE", error=str(error))
                state.record_nonpublished(lecture_id, input_hash, requested, target, sources, "PROVIDER_UNAVAILABLE")
                _write_status_markdown(workspace, state.rows())
                raise WorkflowError(str(error)) from error
            except ProviderError as error:
                state.record_attempt(lecture_id, requested, status="FAILED", error=str(error))
                state.record_nonpublished(lecture_id, input_hash, requested, target, sources, "FAILED")
                _write_status_markdown(workspace, state.rows())
                raise WorkflowError(f"Provider failed: {error}") from error
            try:
                notes = _validate_notes(response.notes, lecture_id)
            except WorkflowError as error:
                state.record_attempt(lecture_id, requested, status="REJECTED", response=response, error=str(error))
                state.record_nonpublished(lecture_id, input_hash, requested, target, sources, "FAILED")
                _write_status_markdown(workspace, state.rows())
                raise
            state.record_attempt(lecture_id, requested, status="SUCCESS", response=response)
            atomic_json_write(
                run_dir / "notes.json",
                {
                    "notes": notes,
                    "requested_model": requested.__dict__,
                    "actual_model": {
                        "provider": response.provider,
                        "model": response.model,
                        "effort": response.effort,
                    },
                    "usage": response.usage,
                    "sources": sources,
                },
            )
            document = Document(target) if target.exists() else Document()
            _style(document)
            if not target.exists():
                title = document.add_paragraph(f"{course['name']} Notes", style="Title")
                title.alignment = WD_ALIGN_PARAGRAPH.LEFT
                document.add_paragraph("Cumulative notes generated from the sources available for each lecture.")
            _replace_block(document, lecture_id, notes, sources)
            descriptor, temporary_name = tempfile.mkstemp(suffix=".docx", dir=target.parent)
            os.close(descriptor)
            temporary = Path(temporary_name)
            try:
                document.save(temporary)
                current_hash = file_sha256(target) if target.is_file() else None
                if current_hash != baseline_hash:
                    raise WorkflowError("Cumulative Word changed during publication; no file was overwritten")
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            state.record(lecture_id, input_hash, response, requested.quality, target, sources)
            _write_status_markdown(workspace, state.rows())
            return {
                "lecture_id": lecture_id,
                "status": "PUBLISHED",
                "output": str(target),
                "requested_model": requested.__dict__,
                "actual_model": {
                    "provider": response.provider,
                    "model": response.model,
                    "effort": response.effort,
                },
                "usage": response.usage,
                "sources": sources,
            }
        finally:
            state.close()


def _validate_integer_table_totals(rows: list[list[str]]) -> None:
    """Catch exact count errors in model-produced Total rows before Word build."""
    if len(rows) < 3 or not rows[-1] or str(rows[-1][0]).strip().casefold() != "total":
        return
    integer = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)")
    for column in range(1, len(rows[-1])):
        values = [str(row[column]).strip() for row in rows[1:] if len(row) > column]
        if len(values) != len(rows) - 1 or not all(integer.fullmatch(value) for value in values):
            continue
        actual = int(values[-1].replace(",", ""))
        expected = sum(int(value.replace(",", "")) for value in values[:-1])
        if actual != expected:
            raise WorkflowError(f"Candidate table total disagrees with row values in column {column + 1}: {actual} != {expected}")


_NUMBER_IN_TEXT_RE = re.compile(r"(?<![\w])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w])")


def _validate_numeric_table_support(block: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> None:
    """Require a numeric table to cite sources with actual numeric content.

    A qualitative personal note about an allocation rule cannot support a
    multi-row table of invented phase boundaries and marginal-profit values.
    This deliberately does not certify every computed cell; detailed source
    comparisons remain part of candidate review.
    """
    rows = block["rows"]
    numeric_cells = sum(bool(_NUMBER_IN_TEXT_RE.search(cell)) for row in rows[1:] for cell in row)
    if numeric_cells < 4:
        return
    cited_numbers = set()
    for identifier in block["evidence_ids"]:
        item = evidence[identifier]
        cited_numbers.update(_NUMBER_IN_TEXT_RE.findall(str(item.get("content", item.get("text", "")))))
    if len(cited_numbers) < 2:
        raise WorkflowError("Numeric candidate table lacks numeric values in its cited evidence")


_TERM_MARKER_RE = re.compile(r"\[\[term:([^\[\]\n]+)\]\]")


def _validate_inline_term_markers(value: str) -> int:
    """Reject malformed orange-term markup before it reaches the Word renderer."""
    matches = list(_TERM_MARKER_RE.finditer(value))
    if (value.count("[[term:") != len(matches)
            or (bool(matches) and value.count("]]") != len(matches))
            or any(not match.group(1).strip() for match in matches)):
        raise WorkflowError("Candidate contains a malformed technical-term marker")
    return len(matches)


def ordinary_emphasis_diagnostics(notes: dict[str, Any], source_pack: dict[str, Any]) -> dict[str, Any]:
    """Measure only body emphasis that the renderer will keep black, not orange terms."""
    declared = {
        term.strip().casefold()
        for block in notes.get("blocks", [])
        for value in (block.get("text", ""), block.get("answer", ""), *block.get("items", []))
        for term in re.findall(r"\[\[term:([^\]]+)\]\]", str(value))
    }
    body = [str(value) for block in notes.get("blocks", [])
            for value in ([block.get("text", "")] if block.get("kind") == "paragraph"
                          else block.get("items", []) if block.get("kind") == "bullets" else [])]
    words = sum(len(re.findall(r"\b[\w’-]+\b", text)) for text in body)
    spans = [span for text in body for span in re.findall(r"\*\*([^*]+)\*\*", text)
             if span.strip() and "[[term:" not in span and span.strip().casefold() not in declared]
    technical = bool(notes.get("formulas") or source_pack.get("glossary")
                     or any(block.get("kind") in {"table", "formula"} for block in notes.get("blocks", [])))
    required = technical and words >= 500
    return {"narrative_words": words, "ordinary_emphasis_spans": len(spans),
            "required": required, "missing": required and not spans}


def _validate_candidate_notes(notes: dict[str, Any], lecture_id: str, source_pack: dict[str, Any], *, source_root: Path | None = None, style: dict[str, Any] | None = None, final_phase: bool = True) -> dict[str, Any]:
    """Reject a polished-looking draft unless it accounts for its actual evidence."""
    result = _validate_notes(notes, lecture_id)
    # Some providers repeat the document title as a level-2 block even though
    # the Word renderer inserts that heading itself. Remove only that exact
    # duplicate; other unexpected levels remain validation errors.
    if style is not None and isinstance(result.get("blocks"), list):
        result["blocks"] = [
            block for block in result["blocks"]
            if not (isinstance(block, dict) and block.get("kind") == "heading"
                    and block.get("level") == 2 and str(block.get("text", "")).strip() == result["title"].strip())
        ]
        # Agy may put an equation in the required top-level formulas list but
        # leave the corresponding ordered formula block's expression empty.
        # An exact label match lets us place that same source text in the Word
        # narrative without inventing or rewriting an equation.
        formulas_by_label = {
            str(item.get("label", "")).strip(): str(item.get("expression", "")).strip()
            for item in result["formulas"] if str(item.get("label", "")).strip()
        }
        result["blocks"] = [
            {**block, "expression": formulas_by_label[str(block.get("label", "")).strip()]}
            if (isinstance(block, dict) and block.get("kind") == "formula"
                and not str(block.get("expression", "")).strip()
                and str(block.get("label", "")).strip() in formulas_by_label)
            else block
            for block in result["blocks"]
        ]
    required = {
        "sections": list, "evidence_coverage": list, "visual_decisions": list, "model_knowledge": list,
    }
    for key, expected in required.items():
        if not isinstance(result.get(key), expected):
            raise WorkflowError(f"Candidate notes require {key} as {expected.__name__}")
    evidence_ids = {str(item["id"]) for item in source_pack["evidence"]}
    evidence_by_id = {str(item["id"]): item for item in source_pack["evidence"]}
    coverage: dict[str, dict[str, Any]] = {}
    for item in result["evidence_coverage"]:
        if not isinstance(item, dict) or item.get("evidence_id") not in evidence_ids:
            raise WorkflowError("Candidate evidence coverage contains an unknown evidence id")
        if item.get("disposition") not in {"used", "excluded", "uncertain"} or not str(item.get("reason", "")).strip():
            raise WorkflowError("Candidate evidence coverage requires a disposition and a reason")
        if item["evidence_id"] in coverage:
            raise WorkflowError("Candidate evidence coverage contains a duplicate evidence id")
        coverage[item["evidence_id"]] = item
    # Personal notes and written material are the durable, user-reviewable
    # sources.  They must never silently disappear from the generated notes.
    required_coverage = {str(item["id"]) for item in source_pack["evidence"] if str(item["id"]).startswith(("S", "H", "W"))}
    missing = required_coverage - set(coverage)
    if missing:
        raise WorkflowError(f"Candidate omits required evidence coverage: {', '.join(sorted(missing))}")
    for section in result["sections"]:
        if not isinstance(section, dict) or section.get("level") not in {2, 3, 4}:
            raise WorkflowError("Each candidate section needs level 2, 3 or 4")
        identifiers = section.get("evidence_ids")
        if not isinstance(identifiers, list) or not identifiers or any(item not in evidence_ids for item in identifiers):
            raise WorkflowError("Each candidate section needs existing evidence ids")
    visual_ids = {item.get("evidence_id") for item in source_pack["visuals"] if item.get("visual_decision") != "exclude" and item.get("evidence_id")}
    excluded_visual_ids = {item.get("evidence_id") for item in source_pack["visuals"] if item.get("visual_decision") == "exclude" and item.get("evidence_id")}
    # A source-pack visual already excluded before generation needs no model
    # decision. Drop only a matching redundant "exclude"; any attempted
    # inclusion or unknown ID remains an error.
    result["visual_decisions"] = [
        item for item in result["visual_decisions"]
        if not (isinstance(item, dict) and item.get("evidence_id") in excluded_visual_ids
                and item.get("decision") == "exclude")
    ]
    decisions: set[str] = set()
    for item in result["visual_decisions"]:
        if not isinstance(item, dict) or item.get("evidence_id") not in visual_ids:
            raise WorkflowError("Candidate visual decision references an unknown or excluded visual")
        if item.get("decision") not in {"include", "exclude", "review"} or not str(item.get("reason", "")).strip():
            raise WorkflowError("Candidate visual decision requires a valid decision and reason")
        decisions.add(str(item["evidence_id"]))
    if visual_ids - decisions:
        raise WorkflowError("Candidate does not decide every available visual")
    if not all(isinstance(item, str) for item in result["model_knowledge"]):
        raise WorkflowError("Candidate model knowledge must be a list of strings")
    result.setdefault("review_questions", [])
    if not isinstance(result["review_questions"], list) or any(not isinstance(item, str) or not item.strip() for item in result["review_questions"]):
        raise WorkflowError("Candidate review_questions must be a list of non-empty questions")
    blocks = result.get("blocks", [])
    if not isinstance(blocks, list):
        raise WorkflowError("Candidate blocks must be a list")
    visual_decisions = {str(item["evidence_id"]): item["decision"] for item in result["visual_decisions"]}
    narrative_text: list[str] = []
    narrative_term_count = 0
    technical_structure = bool(result["formulas"] or source_pack.get("glossary"))
    for block in blocks:
        if not isinstance(block, dict) or block.get("kind") not in {"overview", "heading", "paragraph", "bullets", "formula", "table", "visual", "qa", "recap"}:
            raise WorkflowError("Candidate has an invalid ordered block")
        identifiers = block.get("evidence_ids")
        if not isinstance(identifiers, list) or not identifiers or any(item not in evidence_ids for item in identifiers):
            raise WorkflowError("Each ordered block needs existing evidence ids")
        kind = block["kind"]
        for field in ("text", "question", "answer", "label"):
            if isinstance(block.get(field), str):
                count = _validate_inline_term_markers(block[field])
                if kind in {"heading", "qa"} and field in {"text", "question"} and count:
                    raise WorkflowError("Technical-term markers belong in explanations, not headings or questions")
                if kind == "paragraph" and field == "text":
                    narrative_text.append(block[field])
                    narrative_term_count += count
        if isinstance(block.get("items"), list):
            for item in block["items"]:
                if isinstance(item, str):
                    count = _validate_inline_term_markers(item)
                    if kind == "bullets":
                        narrative_text.append(item)
                        narrative_term_count += count
        if kind in {"heading", "paragraph", "recap"} and not str(block.get("text", "")).strip():
            raise WorkflowError(f"Candidate {kind} block needs text")
        if kind == "heading" and block.get("level") not in {3, 4}:
            raise WorkflowError("Candidate heading block needs level 3 or 4")
        if style is not None and kind == "heading" and re.match(r"^\s*\d+(?:\.\d+)*[.)]?\s+", block["text"]):
            raise WorkflowError("Editorial headings must omit written section numbers")
        if kind == "formula" and not str(block.get("expression", "")).strip():
            raise WorkflowError("Candidate formula block needs an expression")
        if kind in {"overview", "bullets"} and (not isinstance(block.get("items"), list) or not block["items"] or any(not isinstance(item, str) or not item.strip() for item in block["items"])):
            raise WorkflowError("Candidate bullets block needs non-empty items")
        if style is not None and kind == "overview" and any(re.match(r"^\s*\d+[.)]\s+", item) for item in block["items"]):
            raise WorkflowError("Overview items must omit written numbers; Word numbers the list")
        if kind == "qa" and (not str(block.get("question", "")).strip() or not str(block.get("answer", "")).strip()):
            raise WorkflowError("Candidate qa block needs a question and answer")
        if kind == "table" and (not isinstance(block.get("rows"), list) or not block["rows"] or any(not isinstance(row, list) or not row or any(not isinstance(cell, str) for cell in row) for row in block["rows"])):
            raise WorkflowError("Candidate table block needs rows of text")
        if kind == "table":
            technical_structure = True
            for row in block["rows"]:
                for cell in row:
                    _validate_inline_term_markers(cell)
            _validate_integer_table_totals(block["rows"])
            _validate_numeric_table_support(block, evidence_by_id)
        if kind == "formula":
            technical_structure = True
        if kind == "visual" and visual_decisions.get(str(block.get("visual_id"))) != "include":
            raise WorkflowError("Candidate visual block needs an included visual decision")
    # The requirement applies only to fresh editorial generations. Saved v1
    # candidates remain readable/buildable, and short or nontechnical lessons
    # are not forced to add decorative orange text.
    narrative_words = sum(len(re.findall(r"\b[\w’-]+\b", text)) for text in narrative_text)
    if style is not None and technical_structure and narrative_words >= 500 and narrative_term_count == 0:
        raise WorkflowError("Long technical candidate needs a [[term:...]] key term in its narrative")
    if style is not None and final_phase and ordinary_emphasis_diagnostics(result, source_pack)["missing"]:
        raise WorkflowError("Long technical candidate needs ordinary black **emphasis** in its narrative, distinct from orange terms")
    # Saved candidates from earlier versions may contain only legacy blocks.
    # Once a draft opts into the explicit editorial structure, validate its
    # sequence rather than trusting a prompt alone to produce it consistently.
    kinds = [block["kind"] for block in blocks]
    if style is not None or "overview" in kinds or "qa" in kinds:
        if not kinds or kinds[0] != "overview" or kinds.count("overview") != 1:
            raise WorkflowError("Editorial candidate must start with one overview block")
        count = sum(kind == "qa" for kind in kinds)
        maximum = (style or {}).get("question_count", 5)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum not in range(1, 6):
            maximum = 5
        if not 1 <= count <= maximum:
            raise WorkflowError(f"Editorial candidate needs between one and {maximum} qa blocks")
        first_question = kinds.index("qa")
        if any(kind not in {"qa", "recap"} for kind in kinds[first_question:]):
            raise WorkflowError("Question blocks must follow the main narrative")
        want_recap = (style or {}).get("recap", True)
        if want_recap and (kinds[-1] != "recap" or kinds.count("recap") != 1):
            raise WorkflowError("Editorial candidate must end with one recap block")
        if not want_recap and "recap" in kinds:
            raise WorkflowError("Editorial candidate has a recap disabled by its style")
        if (style or {}).get("image_policy") == "none" and any(item.get("decision") == "include" for item in result["visual_decisions"]):
            raise WorkflowError("Editorial candidate includes an image despite image_policy=none")
    if blocks:
        included = {identifier for identifier, decision in visual_decisions.items() if decision == "include"}
        if any(item.get("decision") == "include" and item.get("inspected") is not True for item in result["visual_decisions"]):
            raise WorkflowError("An included visual in ordered blocks must be confirmed as inspected")
        if included:
            visual_paths = {str(item.get("evidence_id")): item.get("image_path") for item in source_pack["visuals"]}
            # A path plus a provider assertion cannot prove visual understanding,
            # but both the image and the narrowly granted lecture folder must
            # be available before accepting an inclusion.
            for identifier in included:
                value = visual_paths.get(identifier)
                if not value or not Path(str(value)).is_file():
                    raise WorkflowError(f"Included visual image is missing: {identifier}")
                if source_root is not None and not Path(str(value)).resolve().is_relative_to(source_root.resolve()):
                    raise WorkflowError(f"Included visual is outside the lecture source folder: {identifier}")
        placed = [str(block["visual_id"]) for block in blocks if block["kind"] == "visual"]
        if set(placed) != included or len(placed) != len(set(placed)):
            raise WorkflowError("Every included visual must appear exactly once in ordered blocks")
    result["lecture_id"] = lecture_id
    return result


def generate_candidate(
    workspace: Workspace,
    lecture_id: str,
    *,
    source_pack_path: Path | None = None,
    provider: str | None = None,
    quality: str = "standard",
    provider_adapter: ProviderAdapter | None = None,
) -> dict[str, Any]:
    """Generate a non-published candidate from a validated source pack.

    This is deliberately separate from ``process``: until the Word renderer is
    hardened in the next intervention, a real provider may create only a
    reviewable JSON candidate and never overwrite the user's cumulative notes.
    """
    lecture_id = lecture_id.upper()
    course_code, _ = parse_lecture_id(lecture_id)
    configuration = workspace.configuration_store().load()
    course = _course(configuration, course_code)
    requested = resolve_model(configuration, provider, quality)
    path = source_pack_path or workspace.runs_dir / lecture_id / "source_pack.json"
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowError(f"Cannot load source pack: {path}: {error}") from error
    # Imported lazily to keep source-pack construction independent from this
    # workflow module (it records its own PREPARED state).
    from .sourcepack import SourcePackError, validate_source_pack
    try:
        validate_source_pack(pack)
    except SourcePackError as error:
        raise WorkflowError(f"Invalid source pack: {error}") from error
    if pack.get("lecture_id") != lecture_id:
        raise WorkflowError("Source pack lecture id does not match the requested lecture")
    editorial_style = effective_style(configuration, course_code)
    editorial_style["question_language"] = str(configuration.get("personal", {}).get("interface_language") or course.get("languages", {}).get("notes", "en"))
    # Existing free-form instructions remain part of the editorial request
    # until the user chooses to replace them in the guided style editor.
    editorial_style["freeform_preferences"] = configuration["style"].get("freeform_preferences", "")
    input_hash = hashlib.sha256(json.dumps({"source_pack": file_sha256(path), "requested": requested.__dict__, "quality": requested.quality, "style": editorial_style, "course": course, "generation_contract": GENERATION_CONTRACT_VERSION}, sort_keys=True).encode()).hexdigest()
    run_dir = workspace.runs_dir / lecture_id
    candidate_path = run_dir / "lecture_notes.candidate.json"
    sources = {key: bool(value) for key, value in pack.get("availability", {}).items()}
    state = WorkflowState(workspace.database_path)
    try:
        if state.candidate_completed(lecture_id, input_hash):
            return {"lecture_id": lecture_id, "status": "SKIPPED", "reason": "identical candidate already exists", "candidate": str(candidate_path)}
        base = ProviderRequest(lecture_id, course, {}, requested, source_pack=pack, run_dir=run_dir, style=editorial_style)
        previous: dict[str, Any] | None = None
        response: ProviderResponse | None = None
        editorial_diagnostics: list[dict[str, Any]] = []
        for phase, context_mode in QUALITY_PHASES[requested.quality]:
            request = with_phase(base, phase, context_mode, previous)
            try:
                response = invoke_provider(request, provider_adapter)
                previous = _validate_candidate_notes(response.notes, lecture_id, pack, source_root=path.parent, style=editorial_style,
                                                     final_phase=phase == QUALITY_PHASES[requested.quality][-1][0])
                editorial_diagnostics.append({"phase": phase, **ordinary_emphasis_diagnostics(previous, pack)})
                state.record_attempt(lecture_id, requested, status=f"SUCCESS_{phase.upper()}", response=response)
            except ProviderQuotaError as error:
                state.record_attempt(lecture_id, requested, status="QUOTA_EXHAUSTED", error=str(error))
                state.record_nonpublished(lecture_id, input_hash, requested, candidate_path, sources, "PAUSED_QUOTA")
                _write_status_markdown(workspace, state.rows())
                raise WorkflowError(f"Provider quota exhausted; no Word was changed: {error}") from error
            except (ProviderError, WorkflowError) as error:
                state.record_attempt(lecture_id, requested, status=f"REJECTED_{phase.upper()}", response=response, error=str(error))
                state.record_nonpublished(lecture_id, input_hash, requested, candidate_path, sources, "FAILED")
                _write_status_markdown(workspace, state.rows())
                raise WorkflowError(f"Candidate was rejected; no Word was changed: {error}") from error
        assert previous is not None and response is not None
        atomic_json_write(candidate_path, {
            "notes": previous, "source_pack": str(path), "source_pack_hash": file_sha256(path),
            "requested_model": requested.__dict__,
            "actual_model": {"provider": response.provider, "model": response.model, "effort": response.effort},
            "quality": requested.quality, "phases": [phase for phase, _ in QUALITY_PHASES[requested.quality]],
            "usage": response.usage, "published_to_word": False,
            "editorial_diagnostics": editorial_diagnostics,
        })
        state.record_candidate(lecture_id, input_hash, response, requested.quality, candidate_path)
        _write_status_markdown(workspace, state.rows())
        return {"lecture_id": lecture_id, "status": "CANDIDATE_READY", "candidate": str(candidate_path), "published_to_word": False,
                "requested_model": requested.__dict__, "actual_model": {"provider": response.provider, "model": response.model, "effort": response.effort},
                "quality": requested.quality, "phases": [phase for phase, _ in QUALITY_PHASES[requested.quality]]}
    finally:
        state.close()


def _write_status_markdown(workspace: Workspace, rows: list[dict[str, str]]) -> None:
    lines = ["# MyNotes private workspace", "", "## Lecture status", ""]
    if not rows:
        lines.append("No lecture run has been recorded.")
    else:
        lines.extend(("| Lecture | Status | Provider | Model | Quality |", "| --- | --- | --- | --- | --- |"))
        for row in rows:
            lines.append(
                f"| {row['lecture_id']} | {row['status']} | {row['provider']} | "
                f"{row['model']} | {row['quality']} |"
            )
    workspace.status_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
