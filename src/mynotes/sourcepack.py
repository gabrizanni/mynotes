"""Build a validated, evidence-labelled package for one lecture."""

from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .asr import SpeechEngine, speaker_role, transcribe_audio
from .extraction import (
    ExtractionError,
    extract_material,
    linked_urls,
    source_descriptor,
    split_directives,
)
from .extraction_cache import extract_cached
from .utils import atomic_json_write, file_sha256
from .workflow import WorkflowState, parse_lecture_id
from .workspace import Workspace


class SourcePackError(RuntimeError):
    """Raised when evidence cannot satisfy the source-pack contract."""


SUPPORTED_MATERIALS = {".pdf", ".ppt", ".pptx", ".xlsx", ".docx", ".txt", ".md", ".ipynb"}
NOTEBOOK_FORMATS = {".docx", ".txt", ".md"}
EVIDENCE_ID_RE = re.compile(r"[TSHWK]\d{3,}")
TERM_RE = re.compile(r"\b(?:[A-Z]{2,12}|[A-Z][A-Za-z0-9&./+-]{2,}|[A-Za-z]+-[A-Za-z-]+)\b")


def course_for(configuration: dict[str, Any], code: str) -> dict[str, Any]:
    for course in configuration["courses"]:
        if course["code"] == code:
            return course
    raise SourcePackError(f"Course is not configured: {code}")


def _configured_paths(value: str, *, recursive: bool = False) -> list[Path]:
    if not value:
        return []
    path = Path(value)
    if path.is_file():
        return [path]
    iterator = path.rglob("*") if recursive else path.iterdir()
    return [item for item in sorted(iterator) if item.is_file() and not item.name.startswith("~$")] if path.is_dir() else []


def _matches_lesson(path: Path, lesson_number: int) -> bool:
    normalized = path.stem.replace("_", " ").replace("-", " ")
    if re.search(rf"(?:^|\b)(?:L|lecture|lesson|lezione)\s*0*{lesson_number}(?:\b|$)", normalized, re.IGNORECASE):
        return True
    # A deck labelled L1-2 is evidence for both lessons; L10 is not L1.
    for match in re.finditer(r"(?:^|\b)L\s*0*(\d+)\s*[-–]\s*(\d+)(?:\b|$)", path.stem, re.IGNORECASE):
        if int(match.group(1)) <= lesson_number <= int(match.group(2)):
            return True
    return any(
        re.fullmatch(rf"(?:L|lecture|lesson|lezione)[ _-]*0*{lesson_number}", part, re.IGNORECASE)
        for part in path.parts[-3:-1]
    )


def _manifest_paths(course: dict[str, Any], lesson_number: int) -> tuple[list[Path], Path | None] | None:
    entry = course.get("lesson_sources", {}).get(str(lesson_number))
    if entry is None:
        return None
    materials = [Path(value) for value in entry["materials"]]
    for path in materials:
        if not path.is_file() or path.suffix.casefold() not in SUPPORTED_MATERIALS:
            raise SourcePackError(f"Configured lesson material is missing or unsupported: {path}")
    notes = Path(entry["personal_notes"]) if entry.get("personal_notes") else None
    if notes is not None and (not notes.is_file() or notes.suffix.casefold() not in NOTEBOOK_FORMATS):
        raise SourcePackError(f"Configured lesson notebook is missing or unsupported: {notes}")
    return materials, notes


def discover_materials(course: dict[str, Any], lesson_number: int) -> tuple[list[Path], Path | None]:
    """Find lesson-labelled files; include a configured syllabus for context."""
    manifest = _manifest_paths(course, lesson_number)
    if manifest is not None:
        materials, personal_notes = manifest
    else:
        materials = [
            path for path in _configured_paths(course["folders"]["materials"], recursive=True)
            if path.suffix.casefold() in SUPPORTED_MATERIALS
            and _matches_lesson(path, lesson_number)
            and not re.search(r"course[_ -]?notes|generated[_ -]?notes", path.stem, re.IGNORECASE)
        ]
        notes_candidates = [
            path for path in _configured_paths(course["folders"].get("personal_notes", ""))
            if path.suffix.casefold() in NOTEBOOK_FORMATS
            and not re.search(r"course[_ -]?notes|generated[_ -]?notes", path.stem, re.IGNORECASE)
        ]
        personal_notes = notes_candidates[0] if len(notes_candidates) == 1 else next(
            (path for path in notes_candidates if _matches_lesson(path, lesson_number)), None
        )
    syllabus_value = course["folders"].get("syllabus", "")
    syllabus = Path(syllabus_value) if syllabus_value and Path(syllabus_value).is_file() else None
    unique = list(dict.fromkeys([*materials, *([syllabus] if syllabus else [])]))
    return unique, personal_notes


def _glossary(course: dict[str, Any], chunks: list[dict[str, Any]], limit: int = 150) -> list[str]:
    counts: Counter[str] = Counter()
    for chunk in chunks:
        counts.update(TERM_RE.findall(str(chunk.get("content", ""))))
    fixed = [course["name"], course["code"], *course["transcription"]["vocabulary"]]
    result: list[str] = []
    for term in [*fixed, *(term for term, _ in counts.most_common(limit))]:
        if term and term.casefold() not in {item.casefold() for item in result}:
            result.append(term)
    return result[:limit]


def _tokens(value: str) -> set[str]:
    ignored = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "you", "your"}
    return {item for item in re.findall(r"[a-z0-9]+", value.casefold()) if len(item) >= 3 and item not in ignored}


def _align_visuals(visuals: list[dict[str, Any]], slide_evidence: list[dict[str, Any]], transcript: list[dict[str, Any]]) -> None:
    """Attach a conservative monotonic transcript match to every visual candidate."""
    last_index = 0
    by_locator = {(item["source_file"], item.get("page")): item for item in slide_evidence}
    for visual in sorted(visuals, key=lambda item: (item["source_file"], int(item["page"]))):
        evidence = by_locator.get((visual["source_file"], visual.get("page")))
        visual["evidence_id"] = evidence["id"] if evidence else None
        if not evidence or not transcript:
            visual["alignment"] = {"status": "unavailable", "confidence": 0.0}
            continue
        target = _tokens(str(evidence.get("content", "")))
        ranked: list[tuple[float, int, dict[str, Any]]] = []
        for index, segment in enumerate(transcript[last_index:], start=last_index):
            words = _tokens(str(segment.get("text", "")))
            score = len(target & words) / max(1, len(target | words))
            ranked.append((score, index, segment))
        best = max(ranked, default=(0.0, last_index, {}), key=lambda item: item[0])
        confidence, index, segment = best
        if confidence >= 0.08:
            last_index = index
            visual["alignment"] = {
                "status": "matched" if confidence >= 0.18 else "review",
                "confidence": round(confidence, 4), "transcript_id": segment.get("id"),
                "timestamp": segment.get("start"),
            }
        else:
            visual["alignment"] = {"status": "review", "confidence": round(confidence, 4)}


def validate_source_pack(pack: dict[str, Any]) -> None:
    required = ("schema_version", "lecture_id", "course", "availability", "evidence", "visuals", "directives", "sources")
    missing = [key for key in required if key not in pack]
    if missing:
        raise SourcePackError(f"Source pack misses fields: {', '.join(missing)}")
    ids = [str(item.get("id", "")) for item in pack["evidence"]]
    if any(not EVIDENCE_ID_RE.fullmatch(value) for value in ids):
        raise SourcePackError("Every evidence row requires a valid T/S/H/W/K id")
    if len(ids) != len(set(ids)):
        raise SourcePackError("Evidence ids must be unique")
    existing = set(ids)
    for visual in pack["visuals"]:
        if visual.get("evidence_id") and visual["evidence_id"] not in existing:
            raise SourcePackError("A visual references a nonexistent evidence id")
        transcript_id = visual.get("alignment", {}).get("transcript_id")
        if transcript_id and transcript_id not in existing:
            raise SourcePackError("A visual alignment references a nonexistent transcript id")
    for item in pack["evidence"]:
        if item["id"].startswith("T") and not (0 <= float(item.get("start_seconds", -1)) <= float(item.get("end_seconds", -1))):
            raise SourcePackError("Transcript timestamps must be ordered and non-negative")


def _import_transcript(path: Path, lecture_id: str, *, bound_recording_id: str | None = None) -> dict[str, Any]:
    """Import a normalized transcript without running ASR or changing its source."""
    if not path.is_file():
        raise SourcePackError(f"Transcript does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourcePackError(f"Transcript is not readable JSON: {path}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list) or not payload["segments"]:
        raise SourcePackError("Imported transcript must have a nonempty segments array")
    if payload.get("lecture_id") and payload["lecture_id"] != lecture_id and payload["lecture_id"] != ("_recordings/" + bound_recording_id if bound_recording_id else None):
        raise SourcePackError("Imported transcript belongs to a different lecture")
    segments: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_start = -1.0
    for number, segment in enumerate(payload["segments"], start=1):
        if not isinstance(segment, dict):
            raise SourcePackError(f"Transcript segment {number} is not an object")
        identifier = segment.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"T\d{3,}", identifier) or identifier in seen:
            raise SourcePackError(f"Transcript segment {number} has an invalid or duplicate ID")
        seen.add(identifier)
        try:
            start = float(segment["start_seconds"])
            end = float(segment["end_seconds"])
        except (KeyError, TypeError, ValueError) as error:
            raise SourcePackError(f"Transcript segment {identifier} needs numeric timestamps") from error
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end and start >= previous_start):
            raise SourcePackError(f"Transcript segment {identifier} has invalid or unordered timestamps")
        previous_start = start
        text = segment.get("text")
        if not isinstance(text, str) or not text.strip():
            raise SourcePackError(f"Transcript segment {identifier} has no text")
        speaker = str(segment.get("speaker") or "UNKNOWN")
        segments.append({
            **segment, "speaker": speaker, "speaker_role": speaker_role(speaker),
            "speaker_role_source": "imported_transcript_label" if speaker_role(speaker) != "UNKNOWN" else "unverified",
            "speaker_role_confidence": segment.get("speaker_role_confidence"),
        })
    descriptor = source_descriptor(path)
    return {
        "schema_version": 1,
        "lecture_id": lecture_id,
        "language": payload.get("language", ""),
        "imported": True,
        "source_path": str(path),
        "source_sha256": descriptor["sha256"],
        "segments": segments,
    }


def _prior_hashes(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        import json
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {item["path"]: item["sha256"] for item in payload.get("sources", []) if item.get("path") and item.get("sha256")}
    except (OSError, ValueError, KeyError):
        return {}


def _extract_prepared_source(workspace: Workspace, path: Path, **options):
    """Bind extracted content to the descriptor observed before extraction."""
    descriptor = source_descriptor(path)
    chunks, visuals = extract_cached(path, workspace.state_dir / "extraction-cache", **options)
    if file_sha256(path) != descriptor["sha256"]:
        raise SourcePackError(f"Source changed during preparation; retry: {path}")
    return chunks, visuals, descriptor


def _build_source_pack_locked(
    workspace: Workspace,
    lecture_id: str,
    *,
    audio: Path | None = None,
    transcript_path: Path | None = None,
    materials: Iterable[Path] = (),
    personal_notes: Path | None = None,
    web_snapshots: Iterable[Path] = (),
    engines: dict[str, SpeechEngine] | None = None,
) -> dict[str, Any]:
    """Extract sources, transcribe optional audio, label evidence, and validate."""
    code, lesson_number = parse_lecture_id(lecture_id)
    configuration = workspace.configuration_store().load()
    course = course_for(configuration, code)
    run_dir = workspace.runs_dir / lecture_id
    source_pack_path = run_dir / "source_pack.json"
    previous = _prior_hashes(source_pack_path)
    run_dir.mkdir(parents=True, exist_ok=True)

    material_paths = list(dict.fromkeys(Path(path) for path in materials))
    if not material_paths and personal_notes is None:
        material_paths, discovered_notes = discover_materials(course, lesson_number)
        personal_notes = discovered_notes
    material_chunks: list[dict[str, Any]] = []
    visuals: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for material_number, path in enumerate(material_paths, start=1):
        role = "syllabus" if course["folders"].get("syllabus") and Path(course["folders"]["syllabus"]) == path else "lecture_material"
        chunks, extracted_visuals, source_before = _extract_prepared_source(workspace, path, visual_dir=run_dir / "visuals" / f"{material_number:03d}-{path.stem}")
        for chunk in chunks:
            material_chunks.append({**chunk, "source_file": str(path), "material_role": role})
        visuals.extend(extracted_visuals)
        descriptor = {**source_before, "role": role}
        descriptor["modified_since_last_prepare"] = bool(previous.get(str(path)) and previous[str(path)] != descriptor["sha256"])
        sources.append(descriptor)

    notes_chunks: list[dict[str, Any]] = []
    directives: list[dict[str, Any]] = []
    if personal_notes is not None:
        extracted, _, source_before = _extract_prepared_source(workspace, personal_notes, lesson_number=lesson_number)
        notes_chunks, directives = split_directives(extracted)
        descriptor = {**source_before, "role": "personal_notes"}
        descriptor["modified_since_last_prepare"] = bool(previous.get(str(personal_notes)) and previous[str(personal_notes)] != descriptor["sha256"])
        sources.append(descriptor)

    transcription_course = copy.deepcopy(course)
    # Extraction already produced the text used by the direct ASR glossary.
    # Legacy PPT requires Office even for text; exclude it in both paths so
    # starting transcription never needs Office or changes its cache later.
    glossary_chunks = [item for item in material_chunks if Path(item["source_file"]).suffix.casefold() != ".ppt"]
    transcription_course["transcription"]["vocabulary"] = _glossary(course, glossary_chunks)
    transcript_manifest: dict[str, Any] | None = None
    bound_recording_id = None
    if transcript_path is None:
        from .capture_associations import linked_recording, validated_transcript
        recording = linked_recording(workspace, lecture_id)
        if recording and recording.get("storage_recording_id") and (audio is None or Path(audio).resolve() == Path(recording["path"]).resolve()):
            existing = validated_transcript(workspace, recording)
            if existing:
                transcript_path = existing[0]
                audio = Path(recording["path"])
                bound_recording_id = recording["id"]
    if audio is not None:
        if not audio.is_file():
            raise SourcePackError(f"Audio does not exist: {audio}")
        if transcript_path is None:
            transcript_manifest = transcribe_audio(workspace, lecture_id, transcription_course, audio, engines=engines, _lecture_locked=True)
        descriptor = {**source_descriptor(audio), "role": "recording"}
        descriptor["modified_since_last_prepare"] = bool(previous.get(str(audio)) and previous[str(audio)] != descriptor["sha256"])
        sources.append(descriptor)
    if transcript_path is not None:
        # Duplicate audio can legitimately share an ASR result produced under
        # another recording ID. The verified audio hash authorizes this binding.
        original = json.loads(transcript_path.read_text()) if bound_recording_id else None
        origin_id = original.get("lecture_id", "").removeprefix("_recordings/") if original else None
        transcript_manifest = _import_transcript(transcript_path, lecture_id, bound_recording_id=origin_id)
        if bound_recording_id:
            transcript_manifest["capture_recording_id"] = bound_recording_id
            transcript_manifest["original_asr"] = {key: original.get(key) for key in ("lecture_id", "settings_hash", "cache_key", "audio_sha256", "segments_sha256", "language") }
        descriptor = {**source_descriptor(transcript_path), "role": "imported_transcript"}
        descriptor["modified_since_last_prepare"] = bool(
            previous.get(str(transcript_path)) and previous[str(transcript_path)] != descriptor["sha256"]
        )
        sources.append(descriptor)

    evidence: list[dict[str, Any]] = []
    transcript = transcript_manifest["segments"] if transcript_manifest else []
    evidence.extend(
        {**item, "speaker_role": speaker_role(item.get("speaker")), "kind": "transcript", "untrusted_evidence": True}
        for item in transcript
    )
    slide_evidence: list[dict[str, Any]] = []
    source_hashes = {item["path"]: item["sha256"] for item in sources}
    slide_inventory: list[dict[str, Any]] = []
    for index, chunk in enumerate(material_chunks, start=1):
        item = {
            "id": f"S{index:03d}", "kind": "syllabus" if chunk["material_role"] == "syllabus" else "slide",
            "content": chunk["content"], "source_file": chunk["source_file"], "page": chunk.get("page"),
            "paragraph": chunk.get("paragraph"), "sheet": chunk.get("sheet"), "row": chunk.get("row"),
            "cell": chunk.get("cell"), "material_role": chunk["material_role"], "untrusted_evidence": True,
        }
        if item["kind"] == "slide" and isinstance(item.get("page"), int):
            digest = source_hashes[chunk["source_file"]]
            item["canonical_slide_id"] = f"sha256:{digest}:page:{item['page']}"
            item["textless"] = not bool(str(item["content"]).strip())
            slide_inventory.append({"canonical_slide_id": item["canonical_slide_id"],
                                    "evidence_id": item["id"], "source_file": item["source_file"],
                                    "source_sha256": digest, "page": item["page"], "textless": item["textless"]})
        slide_evidence.append(item)
        evidence.append(item)
    for index, chunk in enumerate(notes_chunks, start=1):
        evidence.append(
            {
                "id": f"H{index:03d}", "kind": "personal_note", "content": chunk["content"],
                "source_file": str(personal_notes), "paragraph": chunk.get("paragraph"),
                "priority": "high", "untrusted_evidence": True,
            }
        )

    linked = linked_urls([*material_chunks, *notes_chunks]) if course["web_access"] == "linked_resources" else []
    for path in web_snapshots:
        chunks, _, source_before = _extract_prepared_source(workspace, Path(path))
        sources.append({**source_before, "role": "web_snapshot"})
        for chunk in chunks:
            linked.extend(linked_urls([chunk]))
            evidence.append(
                {
                    "id": f"W{sum(item['id'].startswith('W') for item in evidence) + 1:03d}", "kind": "web_snapshot",
                    "content": chunk["content"], "source_file": str(path), "page": chunk.get("page"),
                    "untrusted_evidence": True,
                }
            )
    _align_visuals(visuals, slide_evidence, transcript)
    pack = {
        "schema_version": 1, "lecture_id": lecture_id,
        "course": {"code": code, "name": course["name"], "lesson_number": lesson_number},
        "availability": {
            "audio": audio is not None, "transcript": bool(transcript), "materials": bool(material_chunks),
            "personal_notes": bool(notes_chunks or directives), "visual_candidates": any(item["visual_decision"] == "candidate" for item in visuals),
            "missing_audio": audio is None,
        },
        "transcription": transcript_manifest,
        "glossary": transcription_course["transcription"]["vocabulary"],
        "evidence": evidence, "slide_inventory": slide_inventory, "visuals": visuals, "directives": directives,
        "linked_web_resources": list(dict.fromkeys(linked)),
        "knowledge_policy": {"model_knowledge_allowed_for_background": True, "next_id_prefix": "K", "never_attribute_to_lecture": True},
        "sources": sources,
        "security": {"source_content_is_untrusted_evidence": True, "only_explicit_personal_note_directives_are_actionable": False, "only_saved_configuration_preferences_are_actionable": True},
    }
    if not evidence:
        raise SourcePackError("A source pack requires at least one transcript, material, note, or web-snapshot evidence item")
    validate_source_pack(pack)
    # Preserve observations in the returned report, but keep the academic
    # snapshot stable when only cache/preparation bookkeeping changes.
    snapshot = copy.deepcopy(pack)
    if isinstance(snapshot.get("transcription"), dict):
        snapshot["transcription"].pop("cache_reused", None)
    for descriptor in snapshot["sources"]:
        descriptor.pop("modified_since_last_prepare", None)
    atomic_json_write(source_pack_path, snapshot)
    (run_dir / "source_pack-stale.json").unlink(missing_ok=True)
    state = WorkflowState(workspace.database_path)
    try:
        state.record_source_pack(lecture_id, source_pack_path, pack["availability"])
    finally:
        state.close()
    return pack



def transcription_course_context(workspace: Workspace, lecture_id: str, *, course=None, materials=None) -> dict:
    """Shared text-only glossary; never render images merely to launch ASR."""
    code, number = parse_lecture_id(lecture_id)
    if course is None:
        course = course_for(workspace.configuration_store().load(), code)
    if materials is None:
        materials, _ = discover_materials(course, number)
    chunks = []
    for path in dict.fromkeys(materials):
        if Path(path).suffix.casefold() == ".ppt":
            continue
        before = file_sha256(Path(path))
        extracted, _ = extract_cached(Path(path), workspace.state_dir / "extraction-cache")
        if file_sha256(Path(path)) != before:
            raise SourcePackError(f"Source changed while preparing transcription context: {path}")
        chunks.extend(extracted)
    prepared = copy.deepcopy(course)
    prepared["transcription"]["vocabulary"] = _glossary(course, chunks)
    return prepared


def build_source_pack(workspace: Workspace, lecture_id: str, *, audio=None, transcript_path=None,
                      materials=(), personal_notes=None, web_snapshots=(), engines=None) -> dict:
    from .workflow import _generation_lock
    lecture_id = lecture_id.upper()
    parse_lecture_id(lecture_id)
    with _generation_lock(workspace, lecture_id):
        return _build_source_pack_locked(workspace, lecture_id, audio=audio, transcript_path=transcript_path,
            materials=materials, personal_notes=personal_notes, web_snapshots=web_snapshots, engines=engines)
