"""Transactional Word candidate building, validation, publication and restore."""

from __future__ import annotations

import json
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
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

from .utils import atomic_json_write, file_sha256, now_compact_utc
from .workspace import Workspace


class PublicationError(RuntimeError):
    """Publication stopped before damaging a cumulative handout."""


_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "omega": "ω", "Delta": "Δ",
    "Sigma": "Σ", "Pi": "Π", "Omega": "Ω",
}


def _m(tag: str, text: str | None = None) -> OxmlElement:
    element = OxmlElement(f"m:{tag}")
    if text is not None:
        element.text = text
    return element


def _math_run(text: str) -> OxmlElement:
    run = _m("r")
    run.append(_m("t", text))
    return run


class _FormulaParser:
    """Small strict LaTeX parser for the equation forms used in lecture notes."""

    def __init__(self, source: str) -> None:
        value = source.strip()
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
        if name in {"cdot", "times", "leq", "geq", "neq", "infty", "pm", "to"}:
            return [_math_run({"cdot": "·", "times": "×", "leq": "≤", "geq": "≥", "neq": "≠", "infty": "∞", "pm": "±", "to": "→"}[name])]
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
            nary = _m("nary"); props = _m("naryPr"); symbol = _m("chr")
            symbol.set(qn("m:val"), "∑" if name == "sum" else "∏"); props.append(symbol); nary.append(props)
            scripts: dict[str, list[OxmlElement]] = {}
            while self.position < len(self.source) and self.source[self.position] in "^_":
                marker = self.source[self.position]; self.position += 1; scripts[marker] = self._argument()
            sub, sup, expression = _m("sub"), _m("sup"), _m("e")
            [sub.append(item) for item in scripts.get("_", [])]; [sup.append(item) for item in scripts.get("^", [])]
            expression.append(_math_run(" ")); nary.append(sub); nary.append(sup); nary.append(expression)
            return [nary]
        if name in {"text", "mathrm", "mathbf"}:
            return self._argument()
        raise PublicationError(f"Unsupported formula command: \\{name}")


def append_equation(document: Document, formula: str) -> None:
    paragraph = document.add_paragraph()
    math_para = _m("oMathPara"); math = _m("oMath")
    for item in _FormulaParser(formula).parse():
        math.append(item)
    math_para.append(math); paragraph._p.append(math_para)


def _marker(document: Document, value: str) -> None:
    paragraph = document.add_paragraph(); run = paragraph.add_run(value)
    run.font.hidden = True; run.font.size = Pt(1)


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
    paragraph = document.add_paragraph(); paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run().add_picture(str(image), width=Inches(5.9))
    caption = document.add_paragraph(style="Caption" if "Caption" in [s.name for s in document.styles] else None)
    page = f", page {visual['page']}" if visual.get("page") is not None else ""
    caption.add_run(f"Figure — {decision.get('reason', 'Relevant course visual')} ({visual.get('source_file', image.name)}{page})")


def _append_block(document: Document, notes: dict[str, Any], pack: dict[str, Any]) -> None:
    lecture_id = notes["lecture_id"]
    _marker(document, f"MYNOTES START {lecture_id}")
    document.add_heading(f"Lecture {lecture_id} — {notes['title']}", level=2)
    if notes.get("summary"):
        document.add_paragraph(notes["summary"])
    if notes.get("bullets"):
        document.add_heading("Key takeaways", level=3)
        for bullet in notes["bullets"]:
            document.add_paragraph(bullet, style="List Bullet")
    for section in notes.get("sections", []):
        document.add_heading(section["heading"], level=int(section["level"]))
        if section.get("body"):
            document.add_paragraph(section["body"])
        for bullet in section.get("bullets", []):
            document.add_paragraph(bullet, style="List Bullet")
    if notes.get("table"):
        rows = notes["table"]
        width = max(len(row) for row in rows)
        table = document.add_table(rows=len(rows), cols=width); table.style = "Table Grid"
        for r_index, row in enumerate(rows):
            for c_index, value in enumerate(row):
                table.cell(r_index, c_index).text = str(value)
    formulas = list(notes.get("formulas", []))
    if notes.get("formula") and all(item.get("expression") != notes["formula"] for item in formulas):
        formulas.insert(0, {"label": "Formula", "expression": notes["formula"]})
    if formulas:
        document.add_heading("Formulas", level=3)
        for item in formulas:
            if item.get("label"):
                document.add_paragraph(str(item["label"]))
            append_equation(document, str(item["expression"]))
    visuals = {str(item.get("evidence_id")): item for item in pack.get("visuals", [])}
    for decision in notes.get("visual_decisions", []):
        if decision.get("decision") == "include":
            visual = visuals.get(str(decision.get("evidence_id")))
            if not visual:
                raise PublicationError("Selected visual does not exist in the source pack")
            _append_visual(document, visual, decision)
    _marker(document, f"MYNOTES END {lecture_id}")


def _new_document(course: dict[str, Any]) -> Document:
    document = Document(); normal = document.styles["Normal"]
    normal.font.name = "Aptos"; normal.font.size = Pt(11)
    title = document.add_paragraph(f"{course['name']} Notes", style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    return document


def _replace_or_append(document: Document, notes: dict[str, Any], pack: dict[str, Any]) -> None:
    lecture_id = notes["lecture_id"]
    boundary = _boundaries(document, lecture_id)
    body = document._body._element
    before = list(body)
    _append_block(document, notes, pack)
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
    return {"markers": {"start": start, "end": end}, "equations": equations, "embedded_media": len(media)}


def build_word_candidate(workspace: Workspace, lecture_id: str) -> dict[str, Any]:
    lecture_id = lecture_id.upper(); code = lecture_id.split("-L", 1)[0]
    configuration = workspace.configuration_store().load(); course = _course(configuration, code)
    candidate, pack = _load_candidate(workspace, lecture_id); notes = candidate["notes"]
    target = _target(course, workspace); run_dir = workspace.runs_dir / lecture_id / "word"
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / f"{lecture_id}-{uuid.uuid4().hex[:10]}.candidate.docx"
    baseline_hash = file_sha256(target) if target.is_file() else None
    document = Document(target) if target.is_file() else _new_document(course)
    _replace_or_append(document, notes, pack); document.save(output)
    expected_visuals = sum(1 for item in notes.get("visual_decisions", []) if item.get("decision") == "include")
    structure = _docx_structure(output, lecture_id, expected_visuals, bool(notes.get("formula") or notes.get("formulas")))
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
