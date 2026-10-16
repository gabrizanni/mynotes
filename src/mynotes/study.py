"""Source-bound standalone study guides through the canonical Word renderer.

Study documents are candidates, never cumulative publication. Structural,
rendering, editorial and actual Word checks are recorded separately so that
building a DOCX cannot masquerade as a completed academic deliverable.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import uuid
import zipfile
from typing import Any

from docx import Document
from docx.oxml.ns import qn

from .configuration import effective_style
from .publication import (PublicationError, _course, _docx_structure, _heading,
                          _marker, _new_document, _render_and_validate,
                          append_note_blocks)
from .utils import atomic_json_write, file_sha256, now_compact_utc
from .workspace import Workspace


class StudyError(RuntimeError):
    """A study candidate lacks verified sources, content or Word integrity."""


SCHEMA_VERSION = 1
ROLES = {"content", "recap", "practice", "resources"}
KINDS = {"heading", "paragraph", "bullets", "table", "formula", "visual", "qa", "recap", "overview"}


def _load(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("expected a JSON object")
        return result
    except (OSError, ValueError) as error:
        raise StudyError(f"Cannot read study JSON {path}: {error}") from error


def parse_lessons(value: str | list[int]) -> list[int]:
    """Accept one inclusive range or a comma-separated list, ordered uniquely."""
    if isinstance(value, str):
        if re.fullmatch(r"\d+\s*-\s*\d+", value.strip()):
            first, last = map(int, value.split("-"))
            if last < first:
                raise StudyError("Lesson range is reversed")
            result = list(range(first, last + 1))
        elif re.fullmatch(r"\d+(?:\s*,\s*\d+)*", value.strip()):
            result = list(map(int, value.split(",")))
        else:
            raise StudyError("Lessons must be a range such as 6-10 or a comma-separated list")
    elif isinstance(value, list):
        result = value
    else:
        raise StudyError("Invalid lesson selection")
    if not result or any(type(item) is not int or not 1 <= item <= 9999 for item in result):
        raise StudyError("Lesson numbers must be positive integers")
    return sorted(set(result))


def _unique(items: list[dict], label: str) -> dict[str, dict]:
    result = {}
    if not isinstance(items, list):
        raise StudyError(f"{label} must be an array")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise StudyError(f"Every {label} item needs an id")
        if item["id"] in result:
            raise StudyError(f"Duplicate {label} id: {item['id']}")
        result[item["id"]] = item
    return result


def validate_manifest(manifest: dict, *, verify_files: bool = True) -> dict:
    if manifest.get("schema_version") != SCHEMA_VERSION or not manifest.get("course"):
        raise StudyError("Unsupported study manifest")
    lessons = parse_lessons(manifest.get("lessons", []))
    sources = _unique(manifest.get("sources", []), "source")
    evidence = _unique(manifest.get("evidence", []), "evidence")
    topics = _unique(manifest.get("topics", []), "topic")
    links = _unique(manifest.get("links", []), "link")
    if not sources or not evidence or not topics:
        raise StudyError("Study needs non-empty sources, evidence and explicit topics")
    for source in sources.values():
        path = Path(str(source.get("path", "")))
        digest = source.get("sha256", "")
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise StudyError(f"Source fingerprint is missing: {source['id']}")
        paths = [path, *(Path(item) for item in source.get("aliases", []))]
        if verify_files and any(not item.is_file() or file_sha256(item) != digest for item in paths):
            raise StudyError(f"Study source changed or disappeared: {source['id']}")
        if not set(source.get("lesson_ids", [])).issubset(set(lessons)):
            raise StudyError(f"Source lesson scope is outside the request: {source['id']}")
        # 'complete' describes reading coverage, not an inference from excerpts.
        if source.get("review_status") != "complete":
            raise StudyError(f"Full source review has not been recorded: {source['id']}")
    for item in evidence.values():
        if item.get("source_id") not in sources or not str(item.get("content", "")).strip():
            raise StudyError(f"Evidence needs a verified source and readable content: {item['id']}")
    for topic in topics.values():
        identifiers = topic.get("evidence_ids", [])
        if not identifiers or not set(identifiers).issubset(evidence):
            raise StudyError(f"Topic needs known evidence: {topic['id']}")
    for link in links.values():
        if not re.match(r"https?://[^\s]+$", str(link.get("url", ""))) or link.get("source_id") not in sources:
            raise StudyError(f"Linked resource needs a source and HTTP(S) URL: {link['id']}")
        if link.get("review_status") == "pending":
            raise StudyError(f"Review requested link or record it as unavailable: {link['id']}")
        if link.get("review_status") == "unavailable" and not str(link.get("reason", "")).strip():
            raise StudyError("Unavailable links need an explicit reason")
    return {"sources": sources, "evidence": evidence, "topics": topics, "links": links, "lessons": lessons}


def _nonempty(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise StudyError(f"{label} must be non-empty text")


def validate_study_candidate(candidate: dict, manifest: dict) -> dict:
    inventory = validate_manifest(manifest)
    if candidate.get("schema_version") != SCHEMA_VERSION or candidate.get("course") != manifest["course"]:
        raise StudyError("Study candidate and manifest course/schema disagree")
    if candidate.get("purpose") != "quiz":
        raise StudyError("Supported study purpose is quiz")
    _nonempty(candidate.get("title"), "Study title")
    sections = candidate.get("sections", [])
    section_ids = _unique(sections, "section")
    roles = [section.get("role") for section in sections]
    if not roles or any(role not in ROLES for role in roles):
        raise StudyError("Study sections need explicit content/recap/practice/resources roles")
    if inventory["links"] and (roles[-1] != "resources" or roles.count("resources") != 1):
        raise StudyError("Linked resources must be the single final section")
    if not inventory["links"] and (roles.count("resources") > 1 or ("resources" in roles and roles[-1] != "resources")):
        raise StudyError("Optional resources must be the final section")
    if roles.count("recap") != 1 or roles.count("practice") != 1 or "content" not in roles:
        raise StudyError("Study needs content, one recap and one practice section before resources")
    first_support = min(roles.index("recap"), roles.index("practice"))
    if any(role == "content" for role in roles[first_support:]):
        raise StudyError("Main content must precede recap and practice")
    expected_topics = set(inventory["topics"])
    covered = set()
    known_evidence = set(inventory["evidence"])
    all_blocks = []
    for section in sections:
        _nonempty(section.get("title"), "Section title")
        identifiers = section.get("evidence_ids", [])
        if not isinstance(identifiers, list) or not set(identifiers).issubset(known_evidence):
            raise StudyError(f"Unknown section evidence: {section['id']}")
        topic_ids = section.get("topic_ids", [])
        if not set(topic_ids).issubset(expected_topics):
            raise StudyError(f"Unknown topic in {section['id']}")
        if section["role"] == "content":
            covered.update(topic_ids)
            if not identifiers or not topic_ids:
                raise StudyError("Every content section needs supporting evidence and topics")
            for topic_id in topic_ids:
                if not set(inventory["topics"][topic_id]["evidence_ids"]).intersection(identifiers):
                    raise StudyError(f"Topic lacks evidence in its content section: {topic_id}")
        blocks = section.get("blocks", [])
        if not isinstance(blocks, list) or not blocks:
            raise StudyError(f"Empty study section: {section['id']}")
        for block in blocks:
            kind = block.get("kind")
            if kind not in KINDS:
                raise StudyError(f"Unknown Word block: {kind}")
            references = block.get("evidence_ids", identifiers)
            if not isinstance(references, list) or not set(references).issubset(known_evidence):
                raise StudyError("A study block refers to unknown evidence")
            if kind in {"paragraph", "bullets", "table", "formula", "visual"} and section["role"] in {"content", "resources"} and not references:
                raise StudyError("Substantive study blocks require supporting evidence")
            if kind in {"paragraph", "recap", "heading"}:
                _nonempty(block.get("text"), f"{kind}.text")
            if kind == "heading" and block.get("level") not in (2, 3, 4):
                raise StudyError("Word heading levels are 2, 3 or 4")
            if kind in {"bullets", "overview"}:
                if not isinstance(block.get("items"), list) or not block["items"]:
                    raise StudyError("List blocks need items")
                for item in block["items"]:
                    _nonempty(item, "List item")
            if kind == "qa":
                _nonempty(block.get("question"), "Question")
                _nonempty(block.get("answer"), "Answer")
            if kind == "formula":
                _nonempty(block.get("expression"), "Formula")
            if kind == "table":
                rows = block.get("rows")
                if not isinstance(rows, list) or not rows or not isinstance(rows[0], list) or not rows[0]:
                    raise StudyError("Tables need a non-empty rectangular matrix")
                width = len(rows[0])
                if any(not isinstance(row, list) or len(row) != width or any(not isinstance(cell, str) for cell in row) for row in rows):
                    raise StudyError("Tables must be rectangular arrays of text cells")
            if kind == "visual":
                _nonempty(block.get("visual_id"), "Visual id")
            all_blocks.append(block)
        if section["role"] == "content" and not any(block["kind"] == "paragraph" for block in blocks):
            raise StudyError("Study content must include explanatory prose, not only lists")
        if section["role"] == "resources":
            required_links = set(inventory["links"])
            if set(section.get("link_ids", [])) != required_links:
                raise StudyError("Final resources section must explain every requested link")
            text = "\n".join(str(block.get("text", "")) for block in blocks)
            if any(link["url"] not in text for link in inventory["links"].values()):
                raise StudyError("Final section must retain the exact resource URLs")
            if any(link.get("review_status") == "unavailable" and link["reason"] not in text for link in inventory["links"].values()):
                raise StudyError("Final section must disclose unavailable linked resources")
    if covered != expected_topics:
        raise StudyError(f"Study has omitted topics: {sorted(expected_topics - covered)}")
    # Coverage claims are auditable and explicit, not keyword matches or length heuristics.
    review = candidate.get("editorial_review", {})
    coverage = candidate.get("topic_coverage", [])
    if not isinstance(coverage, list):
        raise StudyError("Topic coverage must be an array")
    by_topic = {item.get("topic_id"): item for item in coverage if isinstance(item, dict)}
    if len(by_topic) != len(coverage) or set(by_topic) != expected_topics:
        raise StudyError("Explicit coverage is required for every topic")
    for topic_id, item in by_topic.items():
        section = section_ids.get(item.get("section_id"))
        if not section or section["role"] != "content" or topic_id not in section.get("topic_ids", []):
            raise StudyError(f"Invalid topic-to-section mapping: {topic_id}")
        facets = set(item.get("facets", []))
        required = set(inventory["topics"][topic_id].get("required_facets", ["definition", "intuition", "mechanism", "limits"]))
        if not required.issubset(facets):
            raise StudyError(f"Topic explanation lacks required facets: {topic_id}")
        if inventory["topics"][topic_id].get("quantitative"):
            if not {"formula", "worked_example"}.issubset(facets) or not any(b["kind"] == "formula" for b in section["blocks"]):
                raise StudyError(f"Quantitative topic needs a native formula and worked example: {topic_id}")
    decisions = candidate.get("visual_decisions", [])
    visuals = {item.get("evidence_id"): item for item in candidate.get("visuals", [])}
    included = {item.get("evidence_id") for item in decisions if item.get("decision") == "include"}
    placed = [b["visual_id"] for b in all_blocks if b["kind"] == "visual"]
    if set(placed) != included or len(placed) != len(set(placed)):
        raise StudyError("Included images must be placed exactly once")
    for item in decisions:
        if item.get("evidence_id") not in known_evidence:
            raise StudyError("Visual decision has unknown evidence")
        if item.get("decision") == "include":
            visual = visuals.get(item["evidence_id"], {})
            path = Path(str(visual.get("image_path", "")))
            if item.get("inspected") is not True or not path.is_file() or visual.get("sha256") != file_sha256(path):
                raise StudyError("Included image needs inspection and an immutable image fingerprint")
    return {"topics": len(covered), "sources": len(inventory["sources"]), "evidence": len(known_evidence),
            "sections": len(sections), "full_source_review_recorded": True,
            "editorial_review_recorded": review.get("complete") is True,
            "limitations": manifest.get("limitations", [])}


def _verify_snapshots(record: dict) -> None:
    for key in ("candidate_source", "manifest_source"):
        item = record[key]
        if not Path(item["path"]).is_file() or file_sha256(Path(item["path"])) != item["sha256"]:
            raise StudyError("Study input changed after building; rebuild the candidate")
    validate_study_candidate(_load(Path(record["candidate_source"]["path"])), _load(Path(record["manifest_source"]["path"])))
    if not Path(record["candidate"]).is_file() or file_sha256(Path(record["candidate"])) != record["candidate_hash"]:
        raise StudyError("Study Word candidate changed after validation")


def build_study_candidate(workspace: Workspace, course_code: str, candidate_path: Path,
                          manifest_path: Path, output_path: Path, *, render: bool = True) -> dict:
    candidate_path, manifest_path, output_path = map(lambda p: Path(p).expanduser().resolve(), (candidate_path, manifest_path, output_path))
    if output_path.suffix.lower() != ".docx":
        raise StudyError("Study deliverables use .docx; other formats require an explicit separate export")
    if output_path.exists():
        raise StudyError("Study output already exists; choose a new candidate path to preserve the original")
    candidate, manifest = _load(candidate_path), _load(manifest_path)
    course_code = course_code.upper()
    if candidate.get("course") != course_code:
        raise StudyError("Requested course does not match the study candidate")
    validation = validate_study_candidate(candidate, manifest)
    configuration = workspace.configuration_store().load()
    course = _course(configuration, course_code)
    profile = effective_style(configuration, course_code)
    language = str(course.get("languages", {}).get("notes", "en"))
    recap_language = str(profile.get("recap_language", "it"))
    if recap_language == "same_as_notes":
        recap_language = language
    if candidate.get("language", language) != language or candidate.get("recap_language", recap_language) != recap_language:
        raise StudyError("Candidate languages differ from saved course preferences")
    included = sum(item.get("decision") == "include" for item in candidate.get("visual_decisions", []))
    limit = profile.get("image_limit")
    if (profile.get("image_policy") == "none" and included) or (limit is not None and included > limit):
        raise StudyError("Study images exceed saved course preferences")
    input_hashes = {"candidate_source": {"path": str(candidate_path), "sha256": file_sha256(candidate_path)},
                    "manifest_source": {"path": str(manifest_path), "sha256": file_sha256(manifest_path)}}
    study_id = f"{course_code}-STUDY-{uuid.uuid4().hex[:10]}"
    # Lock the same lesson boundaries used by generate/prepare. Never import a cumulative.
    from .workflow import _generation_lock
    with ExitStack() as locks:
        for lesson in parse_lessons(manifest["lessons"]):
            locks.enter_context(_generation_lock(workspace, f"{course_code}-L{lesson:02d}"))
        document = _new_document(course, profile)
        _marker(document, f"MYNOTES START {study_id}")
        _heading(document, candidate["title"], 2)
        pack = {"visuals": candidate.get("visuals", [])}
        blocks = []
        for section in candidate["sections"]:
            blocks.append({"kind": "heading", "level": 3, "text": section["title"]})
            blocks.extend(section["blocks"])
        append_note_blocks(document, {"blocks": blocks, "visual_decisions": candidate.get("visual_decisions", [])}, pack,
                           language=language, recap_language=recap_language)
        _marker(document, f"MYNOTES END {study_id}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            document.save(temporary)
            tables = [b["rows"] for b in blocks if b["kind"] == "table"]
            structure = _docx_structure(temporary, study_id, included, any(b["kind"] == "formula" for b in blocks), tables)
            # Source races invalidate the build before publishing its path.
            for item in input_hashes.values():
                if file_sha256(Path(item["path"])) != item["sha256"]:
                    raise StudyError("Study inputs changed during Word build")
            validate_manifest(manifest)
            try:
                os.link(temporary, output_path)
            except FileExistsError as error:
                raise StudyError("Study destination appeared during build; preserved it") from error
        finally:
            temporary.unlink(missing_ok=True)
    record = {"schema_version": SCHEMA_VERSION, "study_id": study_id, "course": course_code,
              "status": "STUDY_STRUCTURE_VALIDATED", "candidate": str(output_path),
              "candidate_hash": file_sha256(output_path), "structure": structure,
              "coverage": validation, **input_hashes, "render": None,
              "visual_reviewed": False, "word_verified": False, "created_at": now_compact_utc()}
    atomic_json_write(output_path.with_suffix(".validation.json"), record)
    if render:
        return finalize_study_candidate(output_path)
    return record


def finalize_study_candidate(candidate_path: Path, *, render: bool = True,
                             visual_reviewed: bool = False, word_verified: bool = False,
                             editorial_reviewed: bool = False) -> dict:
    """Record checks actually performed; caller must inspect every page/Word before flags."""
    candidate_path = Path(candidate_path).resolve()
    sidecar = candidate_path.with_suffix(".validation.json")
    record = _load(sidecar)
    _verify_snapshots(record)
    candidate = _load(Path(record["candidate_source"]["path"]))
    if render:
        record["render"] = _render_and_validate(candidate_path, candidate_path.parent / f"{candidate_path.stem}-render")
        record["render"]["pdf_sha256"] = file_sha256(Path(record["render"]["pdf"]))
    if record.get("render"):
        pdf = Path(record["render"]["pdf"])
        if not pdf.is_file() or file_sha256(pdf) != record["render"].get("pdf_sha256"):
            raise StudyError("Rendered PDF changed after validation")
    if (visual_reviewed or word_verified) and not record.get("render"):
        raise StudyError("Visual and Word verification cannot precede rendering")
    record["visual_reviewed"] = record.get("visual_reviewed", False) or visual_reviewed
    record["word_verified"] = record.get("word_verified", False) or word_verified
    record["editorial_reviewed"] = record.get("editorial_reviewed", False) or editorial_reviewed
    editorial = record["editorial_reviewed"] or candidate.get("editorial_review", {}).get("complete") is True
    if record["render"] and editorial and record["visual_reviewed"] and record["word_verified"]:
        record["status"] = "STUDY_READY"
    elif record["render"]:
        record["status"] = "STUDY_RENDER_VALIDATED"
    record["validated_at"] = now_compact_utc()
    _verify_snapshots(record)
    atomic_json_write(sidecar, record)
    return record


def plan_study(workspace: Workspace, course_code: str, lessons: str | list[int], *, purpose: str = "quiz") -> dict:
    """Read-only inventory of prepared evidence; no audio, provider or Office invocation."""
    from .sourcepack import discover_materials
    code = course_code.upper()
    selected = parse_lessons(lessons)
    if purpose != "quiz":
        raise StudyError("Supported study purpose is quiz")
    configuration = workspace.configuration_store().load()
    course = _course(configuration, code)
    sources, evidence, topics, limitations, links = {}, [], [], [], []
    for lesson in selected:
        lecture = f"{code}-L{lesson:02d}"
        path = workspace.runs_dir / lecture / "source_pack.json"
        if (path.parent / "source_pack-stale.json").exists():
            raise StudyError(f"{lecture}: transcript changed; prepare the sources again")
        pack = _load(path) if path.is_file() else None
        materials, personal = discover_materials(course, lesson)
        descriptors = list(pack.get("sources", [])) if pack else []
        descriptors += [{"path": str(item), "kind": "material"} for item in materials + ([personal] if personal else [])]
        if not pack:
            limitations.append(f"{lecture}: prepare sources before provider generation; discovery is inventory only")
        path_ids = {}
        for descriptor in descriptors:
            source = Path(descriptor["path"]).resolve()
            if not source.is_file():
                limitations.append(f"Missing source: {source}")
                continue
            digest = file_sha256(source)
            if descriptor.get("sha256") and descriptor["sha256"] != digest:
                raise StudyError(f"Prepared source changed for {lecture}: {source.name}")
            # Exact byte copies are a single source identity; their scopes are merged.
            key = digest
            record = sources.setdefault(key, {"id": f"SRC-{len(sources)+1:03d}", "path": str(source), "sha256": digest,
                                               "kind": descriptor.get("kind", descriptor.get("role", "material")), "lesson_ids": [],
                                               "review_status": "pending", "aliases": []})
            if lesson not in record["lesson_ids"]:
                record["lesson_ids"].append(lesson)
            if str(source) not in record["aliases"]:
                record["aliases"].append(str(source))
            path_ids[str(source)] = record["id"]
        for item in (pack or {}).get("evidence", []):
            source_id = path_ids.get(str(Path(str(item.get("source_file", ""))).resolve()))
            if not source_id and item.get("kind") == "transcript":
                candidates = [descriptor for descriptor in descriptors if descriptor.get("role") in {"recording", "imported_transcript"}]
                if len(candidates) == 1:
                    source_id = path_ids.get(str(Path(candidates[0]["path"]).resolve()))
            content = item.get("content", item.get("text", ""))
            if not source_id or not str(content).strip():
                limitations.append(f"{lecture}/{item.get('id')}: unbound or unreadable evidence")
                continue
            identifier = f"{lecture}:{item['id']}"
            evidence.append({"id": identifier, "source_id": source_id, "content": content,
                             "location": {key: item[key] for key in ("page", "start", "end", "start_seconds", "end_seconds", "kind") if key in item}})
        for url in (pack or {}).get("linked_web_resources", []):
            if any(link["url"] == url for link in links):
                continue
            web_sources = [descriptor for descriptor in descriptors if descriptor.get("role") == "web_snapshot"]
            declaring_sources = web_sources or descriptors
            if declaring_sources:
                source_id = path_ids.get(str(Path(declaring_sources[0]["path"]).resolve()))
                if source_id:
                    links.append({"id": f"LINK-{len(links)+1:03d}", "url": url, "title": url, "source_id": source_id,
                                  "review_status": "pending"})
        topics.append({"id": lecture, "title": f"Lesson {lesson}", "evidence_ids": [item["id"] for item in evidence if item["id"].startswith(lecture + ":")]})
    return {"schema_version": SCHEMA_VERSION, "course": code, "lessons": selected, "purpose": purpose,
            "sources": list(sources.values()), "evidence": evidence, "topics": topics, "links": links,
            "limitations": limitations, "status": "STUDY_PLAN", "next_step": "Review all sources and define topic coverage before building"}


def study_schema() -> dict:
    """Portable JSON response schema; section evidence may use any supplied namespace."""
    from .providers import notes_schema
    block = deepcopy(notes_schema()["properties"]["blocks"])
    for field in ("visual_id",):
        block["items"]["properties"][field] = {"type": "string"}
    block["items"]["properties"]["evidence_ids"]["items"] = {"type": "string"}
    def strings():
        return {"type": "array", "items": {"type": "string"}}
    def obj(properties):
        return {"type": "object", "additionalProperties": False, "properties": properties, "required": list(properties)}
    section = obj({"id": {"type": "string"}, "title": {"type": "string"}, "role": {"type": "string", "enum": sorted(ROLES)},
                   "blocks": block, "evidence_ids": strings(), "topic_ids": strings(), "link_ids": strings()})
    coverage = obj({"topic_id": {"type": "string"}, "section_id": {"type": "string"}, "facets": strings()})
    return obj({"schema_version": {"type": "integer", "enum": [SCHEMA_VERSION]}, "course": {"type": "string"},
                "purpose": {"type": "string", "enum": ["quiz"]}, "title": {"type": "string"},
                "language": {"type": "string"}, "recap_language": {"type": "string"},
                "sections": {"type": "array", "items": section}, "topic_coverage": {"type": "array", "items": coverage},
                "editorial_review": obj({"complete": {"type": "boolean", "enum": [False]}}),
                "visuals": {"type": "array", "items": {"type": "string"}, "maxItems": 0},
                "visual_decisions": {"type": "array", "items": {"type": "string"}, "maxItems": 0}})


def generate_study_candidate(workspace: Workspace, course_code: str, manifest_path: Path,
                             output_path: Path, *, provider: str | None = None,
                             quality: str = "standard", provider_adapter=None, render: bool = True) -> dict:
    """Generate once from reviewed evidence; preserve an audit trail and avoid paid reuse.

    Source reading/selection is an explicit prerequisite. The metadata-only
    plan cannot be fed directly into this function as if it were a full review.
    The provider's returned review assertion is forcibly false.
    """
    from .providers import ProviderRequest, invoke_provider
    from .workflow import resolve_model, _generation_lock
    import hashlib
    manifest_path = Path(manifest_path).resolve()
    output_path = Path(output_path).resolve()
    if output_path.suffix.lower() != ".docx" or output_path.exists():
        raise StudyError("Choose a new .docx output candidate; existing documents are preserved")
    manifest = _load(manifest_path)
    validate_manifest(manifest)
    code = course_code.upper()
    if manifest["course"] != code:
        raise StudyError("Manifest does not match requested course")
    configuration = workspace.configuration_store().load()
    course = _course(configuration, code)
    style = effective_style(configuration, code)
    requested = resolve_model(configuration, provider, quality)
    fingerprint = hashlib.sha256(json.dumps({"manifest": manifest, "requested": requested.__dict__, "course": course, "style": style}, sort_keys=True).encode()).hexdigest()
    run_dir = workspace.runs_dir / "studies" / fingerprint
    candidate_path = run_dir / "study.candidate.json"
    # A single invocation shared by all selected lessons is logged only once.
    with ExitStack() as locks:
        for lesson in parse_lessons(manifest["lessons"]):
            locks.enter_context(_generation_lock(workspace, f"{code}-L{lesson:02d}"))
        if candidate_path.is_file():
            candidate = _load(candidate_path)
            validate_study_candidate(candidate, manifest)
            reused = True
        else:
            request = ProviderRequest(f"{code}-STUDY", course, {"study_manifest": json.dumps(manifest, ensure_ascii=False)}, requested,
                                      phase="study", context_mode="thorough", run_dir=run_dir, style=style, response_schema=study_schema())
            response = invoke_provider(request, provider_adapter)
            candidate = deepcopy(response.notes)
            # Writing a draft is never proof of a separately executed editorial review.
            candidate["editorial_review"] = {"complete": False}
            validate_study_candidate(candidate, manifest)
            atomic_json_write(candidate_path, candidate)
            atomic_json_write(run_dir / "generation.json", {"requested": requested.__dict__, "actual_model": {"provider": response.provider, "model": response.model, "effort": response.effort},
                                                            "usage": response.usage, "attempt_path": response.attempt_path, "shared_invocation": True})
            reused = False
    result = build_study_candidate(workspace, code, candidate_path, manifest_path, output_path, render=render)
    return {**result, "generation_reused": reused, "editorial_review_required": True}
