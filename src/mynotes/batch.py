"""Ordered, evidence-bounded generation across prepared lessons.

Planning is local and read-only. Generation produces reviewable candidates;
shared provider invocations are stored once, never billed once per lesson.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import zipfile
import time
from typing import Any

from .configuration import effective_style
from .provider_limits import CommandSizeError, validate_command_size
from .providers import (ProviderAdapter, ProviderError, ProviderQuotaError, ProviderCancelledError, ProviderAuthenticationError,
                        ProviderTransportError, ProviderSuspendedError, ProviderTimeoutError, ProviderRequest, ProviderResponse, RequestedModel, _agy_schema,
                        _prompt, _stage_agy_context, invoke_provider, notes_schema, provider_environment,
                        provider_override_flags, with_phase)
from .sourcepack import SourcePackError, validate_source_pack
from .utils import atomic_json_write, file_sha256
from .workflow import (GENERATION_CONTRACT_VERSION, QUALITY_PHASES, WorkflowError,
                       WorkflowState, _course, _generation_lock, _load_generation_phase,
                       _save_generation_phase, _validate_candidate_notes, parse_lecture_id,
                       resolve_model)
from .workspace import Workspace

BATCH_CONTRACT_VERSION = 1
NARRATIVE_KINDS = {"paragraph", "bullets", "table", "formula", "visual"}


class CoverageError(WorkflowError):
    def __init__(self, defects: list[dict]):
        self.defects = defects
        super().__init__("Batch omits the full explanation of an owned slide: " +
                         "; ".join(f"{d['lecture_id']} {d['evidence_id']} {Path(d['source_file']).name} page {d['page']}" for d in defects))


@dataclass(frozen=True)
class BatchBudget:
    """Only verified selected-model limits may enable multi-lesson requests.

    UTF-8 byte counts are conservative upper bounds, not reported token usage.
    Visual token reservations must be supplied when a request includes images.
    """
    verified_for_model: str | None = None
    context_tokens: int | None = None
    output_tokens: int | None = None
    output_reserve_per_lesson: int | None = None
    visual_tokens_per_image: int | None = None

    def __post_init__(self):
        for name in ("context_tokens", "output_tokens", "output_reserve_per_lesson", "visual_tokens_per_image"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise WorkflowError(f"{name} must be a positive integer or unset")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _save(path: Path, payload: dict[str, Any]) -> None:
    atomic_json_write(path, {"schema_version": 1, "sha256": _hash(payload), "payload": payload})


def _read(path: Path) -> dict[str, Any]:
    try:
        envelope = json.loads(path.read_text())
        if envelope["schema_version"] != 1 or envelope["sha256"] != _hash(envelope["payload"]):
            raise ValueError("fingerprint mismatch")
        return envelope["payload"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise WorkflowError(f"Batch checkpoint is damaged or edited: {path.name}; inspect it before retrying. No automatic provider call.") from error


def _style(configuration: dict, code: str, instructions: str, image_limit: int | None) -> dict:
    style = effective_style(configuration, code)
    if not isinstance(instructions, str):
        raise WorkflowError("Lesson instructions must be text")
    if instructions.strip():
        style["lesson_instructions"] = instructions.strip()
    if image_limit is not None:
        if type(image_limit) is not int or image_limit < 0:
            raise WorkflowError("image_limit must be a non-negative integer")
        style["image_limit"] = image_limit
    style["freeform_preferences"] = configuration["style"].get("freeform_preferences", "")
    if configuration.get("personal", {}).get("background"):
        style["learner_background"] = configuration["personal"]["background"]
    style["question_language"] = str(configuration.get("personal", {}).get("interface_language") or _course(configuration, code).get("languages", {}).get("notes", "en"))
    return style


def _packs(workspace: Workspace, lectures: list[str]) -> tuple[dict, dict, dict]:
    packs, snapshots, inventory = {}, {}, {}
    for lecture in lectures:
        path = workspace.runs_dir / lecture / "source_pack.json"
        try:
            content = path.read_bytes()
            pack = json.loads(content)
            validate_source_pack(pack)
        except (OSError, ValueError, TypeError, KeyError, SourcePackError) as error:
            raise WorkflowError(f"Prepare {lecture} before batch generation: {error}") from error
        if pack["lecture_id"] != lecture:
            raise WorkflowError("Prepared source pack does not match selected lesson")
        source_hashes = {}
        for source in pack["sources"]:
            source_path = Path(source["path"])
            if not source_path.is_file() or file_sha256(source_path) != source["sha256"]:
                raise WorkflowError(f"Sources changed for {lecture}; prepare again before generating")
            source_hashes[source["path"]] = source["sha256"]
        for visual in pack.get("visuals", []):
            if visual.get("evidence_id") is None and visual.get("visual_decision") != "exclude":
                raise WorkflowError(f"Image-only slide evidence is missing for {lecture}; prepare again")
        # Older packs get identity from verified source descriptors, never path names.
        for evidence in pack["evidence"]:
            if evidence.get("kind") != "slide" or evidence.get("material_role") == "syllabus" or type(evidence.get("page")) is not int:
                continue
            digest = source_hashes.get(evidence.get("source_file"))
            if not digest:
                raise WorkflowError(f"Slide source identity is missing for {lecture}; prepare again")
            canonical = f"sha256:{digest}:page:{evidence['page']}"
            if evidence.get("canonical_slide_id") not in (None, canonical):
                raise WorkflowError("Prepared canonical slide identity conflicts with the source hash")
            evidence["canonical_slide_id"] = canonical
            record = inventory.setdefault(canonical, {"canonical_slide_id": canonical, "page": evidence["page"], "source_sha256": digest, "occurrences": {}})
            if lecture in record["occurrences"]:
                raise WorkflowError("Duplicate slide locator within one lesson source pack")
            record["occurrences"][lecture] = evidence["id"]
        assets = {v["image_path"]: file_sha256(Path(v["image_path"])) for v in pack.get("visuals", []) if v.get("image_path") and Path(v["image_path"]).is_file()}
        snapshots[lecture] = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(), "sources": source_hashes, "assets": assets}
        packs[lecture] = pack
    return packs, snapshots, inventory


def _published_history(workspace: Workspace, code: str, selected: list[str], inventory: dict) -> tuple[dict, list, dict, list]:
    """Reuse only source-bound, manifest-bound coverage in a verified published Word."""
    choices, unresolved, snapshots, continuity = {}, [], {}, []
    if not workspace.database_path.is_file():
        return choices, unresolved, snapshots, continuity
    try:
        with sqlite3.connect(workspace.database_path.as_uri() + "?mode=ro", uri=True) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"candidate_runs", "lesson_runs"}.issubset(tables):
                return choices, unresolved, snapshots, continuity
            rows = connection.execute("SELECT l.lecture_id,c.candidate_path,c.candidate_hash,l.output_path FROM lesson_runs l LEFT JOIN candidate_runs c ON c.lecture_id=l.lecture_id AND c.status='PUBLISHED' WHERE l.status='PUBLISHED'").fetchall()
            latest = {row[0]: row[1] for row in connection.execute("SELECT output_path,output_hash FROM lesson_runs WHERE status='PUBLISHED' ORDER BY updated_at")}
    except sqlite3.Error as error:
        raise WorkflowError("Cannot verify prior publication state") from error
    for lecture, candidate_name, digest, output_name in rows:
        if lecture in selected or not lecture.startswith(code + "-L") or parse_lecture_id(lecture)[1] >= min(parse_lecture_id(value)[1] for value in selected):
            continue
        if candidate_name is None:
            prior_pack_path = workspace.runs_dir / lecture / "source_pack.json"
            if prior_pack_path.is_file():
                try:
                    _, _, old_inventory = _packs(workspace, [lecture])
                    overlap = set(old_inventory) & set(inventory)
                except WorkflowError:
                    overlap = set(inventory)
                if overlap:
                    unresolved.append({"lecture_id": lecture, "reason": "prior published notes overlap these slides but have no verified canonical ownership", "candidates": sorted(overlap)})
            else:
                linked_history = any(Path(str(item.get("source_file", ""))).resolve() == Path(output_name).resolve() for selected_lecture in selected for item in json.loads((workspace.runs_dir / selected_lecture / "source_pack.json").read_text())["evidence"] if str(item.get("id", "")).startswith("H"))
                if linked_history:
                    unresolved.append({"lecture_id": lecture, "reason": "imported prior published notes have no verifiable slide identity", "candidates": ["review and map existing slide coverage"]})
            continue
        path, output = Path(candidate_name), Path(output_name)
        try:
            if not path.is_file() or file_sha256(path) != digest:
                raise ValueError("published candidate changed")
            payload = json.loads(path.read_text())
            prior_packs, prior_snapshots, prior_inventory = _packs(workspace, [lecture])
            overlap = set(prior_inventory) & set(inventory)
            if not overlap:
                continue
            if payload.get("published_to_word") is not True or not output.is_file() or file_sha256(output) != latest.get(output_name):
                raise ValueError("published Word changed")
            with zipfile.ZipFile(output) as archive:
                xml = archive.read("word/document.xml")
            if f"MYNOTES START {lecture}".encode() not in xml or f"MYNOTES END {lecture}".encode() not in xml:
                raise ValueError("published lesson boundaries missing")
            if payload.get("source_pack_hash") != prior_snapshots[lecture]["sha256"]:
                raise ValueError("published source pack changed")
            manifest = workspace.runs_dir / "_batches" / payload["batch_id"] / payload["batch_fingerprint"] / "manifest.json"
            bound = _read(manifest)
            completion = _read(manifest.parent / f"group-{payload['batch_group']:03d}" / "complete.json")
            before_publish = deepcopy(payload)
            before_publish["published_to_word"] = False
            before_publish.pop("published_path", None)
            before_publish.pop("published_hash", None)
            original_digest = hashlib.sha256((json.dumps(before_publish, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()).hexdigest()
            if not any(entry["lecture_id"] == lecture and entry["sha256"] == original_digest for entry in completion["candidates"]):
                raise ValueError("published notes are not bound to their completed batch")
            owned = payload.get("slide_ownership", {})
            if any(bound["ownership"].get(key) != choice or choice.get("owner") != lecture for key, choice in owned.items()):
                raise ValueError("published ownership differs from its batch manifest")
            _validate_candidate_notes(payload["notes"], lecture, prior_packs[lecture])
            for canonical in overlap & set(owned):
                if canonical in choices and choices[canonical]["owner"] != lecture:
                    raise ValueError("multiple prior full owners")
                # Prior publication is full coverage regardless of its original
                # material classification. Its placement is already published.
                choices[canonical] = {"owner": lecture, "status": "taught"}
                inventory[canonical]["occurrences"][lecture] = prior_inventory[canonical]["occurrences"][lecture]
            snapshots[lecture] = {**prior_snapshots[lecture], "candidate": {str(path): digest}, "published_word": {str(output): latest[output_name]},
                                  "manifest": {str(manifest): file_sha256(manifest), str(manifest.parent / f"group-{payload['batch_group']:03d}" / "complete.json"): file_sha256(manifest.parent / f"group-{payload['batch_group']:03d}" / "complete.json")}}
            continuity.append(_continuity(lecture, payload["notes"]))
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, WorkflowError) as error:
            # If history is present but unverifiable, never silently regenerate
            # its slides into a different lesson or infer coverage from prose.
            unresolved.append({"lecture_id": lecture, "reason": "prior published coverage needs verification", "detail": str(error), "candidates": ["restore/review prior publication", "provide explicit ownership and placement after review"]})
    return choices, unresolved, snapshots, continuity


def _structural_review(choice: dict, slide: dict, pack: dict) -> bool:
    """Only a source-plan review can exempt a title/divider from full prose.

    A model's excluded coverage decision is never such a review. Bind the
    trusted selection to the actual inspected image, including image-only pages;
    do not infer decorative content from short/empty extracted text.
    """
    review = choice.get("structural_review")
    if review is None:
        return False
    text = str(slide.get("content", ""))
    if (len(text.split()) > 40 or re.search(r'\b(?:import\s+\w|from\s+\w.*\bimport\b|def\s+\w|SELECT\s+|In\s*\[\d+\]|Out\s*\[\d+\])|\d\s*[%=]', text, re.I)):
        raise WorkflowError(f"Structural review rejected: {slide['id']} {Path(slide.get('source_file','')).name} page {slide.get('page')} contains substantive text, code or data")
    if (not isinstance(review, dict) or set(review) != {"kind", "reason", "image_sha256"}
            or review.get("kind") not in {"title", "section_divider"}
            or not isinstance(review.get("reason"), str) or not review["reason"].strip()
            or choice.get("status") != "material"):
        raise WorkflowError("Structural slide review requires kind, reason, inspected image hash and material status")
    for visual in pack.get("visuals", []):
        if (visual.get("evidence_id") == slide["id"] and visual.get("source_file") == slide.get("source_file")
                and visual.get("page") == slide.get("page") and visual.get("image_path")):
            path = Path(visual["image_path"])
            if path.is_file() and file_sha256(path) == review["image_sha256"]:
                return True
    raise WorkflowError("Structural slide review no longer matches its inspected source image")


def _owners(inventory: dict, packs: dict, selection: dict | None) -> tuple[dict, list]:
    selection = selection or {}
    if not isinstance(selection, dict) or set(selection) - set(inventory):
        raise WorkflowError("Selection must map existing canonical slide IDs to ownership choices")
    owners, unresolved = {}, []
    for canonical, item in inventory.items():
        candidates = sorted(item["occurrences"], key=lambda value: parse_lecture_id(value)[1])
        choice = selection.get(canonical)
        if choice is None and len(candidates) == 1:
            # Association with one lesson is reliable; it is not proof it was taught.
            choice = {"owner": candidates[0], "status": "material"}
        if choice is None:
            # Exact substantial transcript matches can establish ownership;
            # heuristic visual alignment is deliberately not authoritative.
            matches = []
            for lecture in candidates:
                if lecture not in packs:
                    continue
                local = item["occurrences"][lecture]
                slide = next(row for row in packs[lecture]["evidence"] if row["id"] == local)
                text = " ".join(str(slide.get("content", "")).split()).casefold()
                transcripts = " ".join(str(row.get("text", row.get("content", ""))) for row in packs[lecture]["evidence"] if row["id"].startswith("T")).casefold()
                if len(text.split()) >= 8 and text in " ".join(transcripts.split()):
                    matches.append(lecture)
            if len(matches) == 1:
                choice = {"owner": matches[0], "status": "taught"}
        if choice is None:
            unresolved.append({"canonical_slide_id": canonical, "reason": "shared slide has no reliable actual-lecture owner", "candidates": candidates})
            continue
        if not isinstance(choice, dict) or set(choice) - {"owner", "status", "anchor", "structural_review"} or choice.get("owner") not in candidates or choice.get("status") not in {"taught", "supplement", "material"}:
            raise WorkflowError("A slide choice needs an available owner and status taught, supplement or material")
        owner = choice["owner"]
        if owner in packs:
            local = item["occurrences"][owner]
            slide = next(row for row in packs[owner]["evidence"] if row["id"] == local)
            _structural_review(choice, slide, packs[owner])
            if not str(slide.get("content", "")).strip() and not any(v.get("evidence_id") == local and v.get("image_path") and Path(v["image_path"]).is_file() for v in packs[owner].get("visuals", [])):
                unresolved.append({"canonical_slide_id": canonical, "reason": "textless slide needs a readable source image before generation", "candidates": ["provide a verified source image", "provide an accessible text source"]})
        owners[canonical] = deepcopy(choice)
    for canonical, choice in owners.items():
        if choice["status"] != "supplement":
            if choice.get("anchor"):
                raise WorkflowError("Only supplementary slide choices may specify anchors")
            continue
        lecture = choice["owner"]
        item = inventory[canonical]
        neighbours = [(other["page"], key) for key, other in inventory.items() if key != canonical and other["source_sha256"] == item["source_sha256"] and key in owners and owners[key]["owner"] == lecture and owners[key]["status"] != "supplement"]
        preceding = max((entry for entry in neighbours if entry[0] < item["page"]), default=None)
        following = min((entry for entry in neighbours if entry[0] > item["page"]), default=None)
        suggested = {}
        if preceding:
            suggested["after"] = inventory[preceding[1]]["occurrences"][lecture]
        if following:
            suggested["before"] = inventory[following[1]]["occurrences"][lecture]
        anchor = choice.get("anchor") or suggested
        valid_ids = {row["id"] for row in packs[lecture]["evidence"]}
        if not isinstance(anchor, dict) or not anchor or set(anchor) - {"before", "after"} or any(value not in valid_ids or value == item["occurrences"][lecture] for value in anchor.values()):
            unresolved.append({"canonical_slide_id": canonical, "reason": "supplementary material needs a reliable inline placement", "candidates": suggested})
        else:
            choice["anchor"] = anchor
    return owners, unresolved


def batch_notes_schema() -> dict:
    note = deepcopy(notes_schema(compact=True))
    props = note["properties"]["blocks"]["items"]["properties"]
    props["coverage_role"] = {"type": "string", "enum": ["full", "supplement", "reference", "new_insight"]}
    props["prior_lecture_id"] = {"type": "string"}
    note["properties"]["blocks"]["items"]["required"].append("coverage_role")
    return {"type": "object", "additionalProperties": False, "properties": {"lessons": {"type": "array", "items": {"type": "object", "additionalProperties": False, "properties": {"lecture_id": {"type": "string"}, "notes": note}, "required": ["lecture_id", "notes"]}}}, "required": ["lessons"]}


def _request(plan: dict, lectures: list[str], run_dir: Path, continuity: list[dict]) -> tuple[ProviderRequest, dict]:
    shared, unique, counters, maps, visuals = [], {}, {}, {}, {}
    for lecture in lectures:
        pack = plan["packs"][lecture]
        maps[lecture] = {}
        for item in pack["evidence"]:
            canonical = item.get("canonical_slide_id")
            key = canonical or _hash({"lecture": lecture, "evidence": item})
            if key not in unique:
                prefix = str(item["id"])[0]
                counters[prefix] = counters.get(prefix, 0) + 1
                unique[key] = f"{prefix}{counters[prefix]:03d}"
                shared.append({**item, "id": unique[key]})
            maps[lecture][item["id"]] = unique[key]
        for visual in pack.get("visuals", []):
            identifier = maps[lecture].get(visual.get("evidence_id"))
            if identifier and visual.get("visual_decision") != "exclude":
                candidate = {**visual, "evidence_id": identifier}
                if visual.get("image_path"):
                    original = Path(visual["image_path"])
                    if not original.is_file():
                        raise WorkflowError("Source slide image is missing; prepare again")
                    digest = file_sha256(original)
                    candidate["original_image_path"] = str(original)
                    candidate["image_sha256"] = digest
                    candidate["image_path"] = str(run_dir / "visuals" / (digest + original.suffix))
                alignment = deepcopy(candidate.get("alignment", {}))
                if alignment.get("transcript_id") in maps[lecture]:
                    alignment["transcript_id"] = maps[lecture][alignment["transcript_id"]]
                candidate["alignment"] = alignment
                # Prefer the owner's image path so permitted provenance is deterministic.
                canonical = next((row.get("canonical_slide_id") for row in pack["evidence"] if row["id"] == visual.get("evidence_id")), None)
                if identifier not in visuals or (canonical in plan["ownership"] and plan["ownership"][canonical]["owner"] == lecture):
                    visuals[identifier] = candidate
    ownership = []
    for canonical, choice in plan["ownership"].items():
        occurrences = plan["inventory"][canonical]["occurrences"]
        local = next((maps[lecture][occurrences[lecture]] for lecture in lectures if lecture in occurrences), None)
        if local:
            owner = choice["owner"]
            anchor = {direction: maps[owner][value] for direction, value in choice.get("anchor", {}).items()} if owner in maps else {}
            ownership.append({"canonical_slide_id": canonical, "evidence_id": local, **choice, "anchor": anchor})
    pack = {"lecture_id": lectures[0], "course": {"code": plan["course_code"]}, "evidence": shared, "visuals": list(visuals.values()),
            "directives": [{"lecture_id": lecture, "imported_markers": plan["packs"][lecture].get("directives", [])}
                           for lecture in lectures if plan["packs"][lecture].get("directives")]}
    context = {"lessons": lectures, "lesson_evidence": {lecture: list(maps[lecture].values()) for lecture in lectures}, "ownership": ownership, "continuity": [{key: value for key, value in entry.items() if key != "narrative_fingerprints"} for entry in continuity]}
    request = ProviderRequest(lectures[0], plan["course"], {}, plan["requested"], source_pack=pack, run_dir=run_dir,
                              style=plan["style"], response_schema=batch_notes_schema(), batch_context=context)
    return request, maps


def _launch_check(request: ProviderRequest) -> dict:
    # Same argument payloads as adapters, with conservatively longer file names.
    context = _stage_agy_context(request, request.run_dir.resolve(), write=False) if request.requested.provider == "agy" else None
    prompt = _prompt(request, context=context)
    command = [request.requested.provider, *provider_override_flags(request.requested)]
    if request.requested.provider == "codex":
        command += ["exec", "--json", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--output-schema", str(request.run_dir / ("x" * 64 + ".schema.json")), "--output-last-message", str(request.run_dir / ("x" * 64 + ".final.json")), prompt]
    else:
        schema = _agy_schema(request.response_schema) if request.requested.provider == "agy" else request.response_schema
        command += ["--print", prompt, "--output-format", "json", "--mode" if request.requested.provider == "agy" else "--permission-mode", "plan", "--no-session-persistence", "--json-schema", json.dumps(schema, ensure_ascii=False), "--add-dir", str(request.run_dir)]
    return validate_command_size(command, provider_environment())


def _fits(request: ProviderRequest, count: int, budget: BatchBudget) -> bool:
    _launch_check(request)
    if budget.verified_for_model != request.requested.model:
        return count == 1
    # Conservative text estimate, not a measured tokenizer count or proof of
    # hidden CLI context. Enforce known bounds even if image costs are unknown.
    upper_bound = len(_prompt(request).encode()) + len(json.dumps(request.response_schema).encode())
    reserve = count * budget.output_reserve_per_lesson if budget.output_reserve_per_lesson is not None else None
    if reserve is not None and budget.output_tokens is not None and reserve > budget.output_tokens:
        return False
    if budget.context_tokens is not None and upper_bound + (reserve or 0) > budget.context_tokens:
        return False
    verified = all(value is not None for value in (budget.context_tokens, budget.output_tokens, budget.output_reserve_per_lesson))
    images = sum(bool(item.get("image_path")) for item in request.source_pack.get("visuals", []))
    if not verified or (images and budget.visual_tokens_per_image is None):
        return count == 1
    upper_bound += images * (budget.visual_tokens_per_image or 0)
    reserve = count * budget.output_reserve_per_lesson
    return reserve <= budget.output_tokens and upper_bound + reserve <= budget.context_tokens


def plan_batch(workspace: Workspace, course_code: str, lesson_numbers: list[int], *, provider: str | None = None,
               quality: str = "standard", selection: dict | None = None, budgets: BatchBudget | dict | None = None,
               instructions: str = "", image_limit: int | None = None, speaker_focus: str | None = None) -> dict:
    """Inspect existing prepared packs; write nothing and invoke no provider."""
    code = course_code.upper()
    if not lesson_numbers or any(type(number) is not int or number < 1 or number > 99 for number in lesson_numbers):
        raise WorkflowError("Select lesson numbers from 1 to 99")
    lectures = [f"{code}-L{number:02d}" for number in sorted(set(lesson_numbers))]
    for lecture in lectures:
        parse_lecture_id(lecture)
    configuration = workspace.configuration_store().load()
    course = deepcopy(_course(configuration, code))
    # Onboarding progress is administrative, never an input to lesson writing.
    course.pop('context_setup', None)
    if speaker_focus is not None:
        if speaker_focus not in {"lecture", "professor"}:raise WorkflowError("Invalid speaker_focus")
        course['speaker_focus']=speaker_focus
    requested = resolve_model(configuration, provider, quality)
    style = _style(configuration, code, instructions, image_limit)
    batch_id = code + "-" + _hash(lectures)[:12]
    budget = budgets if isinstance(budgets, BatchBudget) else BatchBudget(**(budgets or {}))
    try:
        packs, snapshots, inventory = _packs(workspace, lectures)
    except WorkflowError:
        pending = []
        for lecture in lectures:
            try:
                _packs(workspace, [lecture])
            except WorkflowError as error:
                pending.append({"lecture_id": lecture, "reason": str(error)})
        return {"status": "NEEDS_PREPARATION", "course_code": code, "lectures": lectures, "batch_id": batch_id,
                "requested_model": asdict(requested), "style": style, "selection": selection, "budgets": asdict(budget),
                "request_options": {"instructions": instructions, "image_limit": image_limit},
                "lessons_needing_preparation": pending, "published_to_word": False}
    prior_choices, prior_unresolved, prior_snapshots, prior_continuity = _published_history(workspace, code, lectures, inventory)
    explicit = selection or {}
    if not isinstance(explicit, dict):
        raise WorkflowError("Selection must be an object mapping canonical slide IDs")
    if any(key in explicit and explicit[key].get("owner") != choice["owner"] for key, choice in prior_choices.items()):
        raise WorkflowError("Selection conflicts with verified prior published ownership; revise that publication explicitly first")
    ownership, unresolved = _owners(inventory, packs, {**prior_choices, **explicit})
    unresolved.extend(prior_unresolved)
    payload = {"course_code": code, "lectures": lectures, "sources": snapshots, "ownership": ownership,
               "requested_model": asdict(requested), "style": style, "course": course, "budgets": asdict(budget),
               "prior_publications": prior_snapshots, "prior_continuity": prior_continuity,
               "selection": selection, "request_options": {"instructions": instructions, "image_limit": image_limit, **({"speaker_focus":speaker_focus} if speaker_focus else {})},
               "contract": BATCH_CONTRACT_VERSION, "generation_contract": GENERATION_CONTRACT_VERSION,
               "prompt_contract": file_sha256(Path(__file__).with_name("providers.py"))}
    fingerprint = _hash(payload)
    plan = {**payload, "batch_id": batch_id, "fingerprint": fingerprint, "status": "NEEDS_SELECTION" if unresolved else "PLANNED",
            "unresolved": unresolved, "groups": [], "published_to_word": False,
            "budget_basis": "verified_context_output_and_cli" if budget.verified_for_model == requested.model and all(value is not None for value in (budget.context_tokens, budget.output_tokens, budget.output_reserve_per_lesson)) and (not any(v.get("image_path") for pack in packs.values() for v in pack.get("visuals", [])) or budget.visual_tokens_per_image is not None) else "cli_only_model_limits_unknown"}
    if unresolved:
        return plan
    runtime = {**plan, "packs": packs, "inventory": inventory, "requested": requested}
    remaining = list(lectures)
    prior_count = 0
    while remaining:
        chosen = None
        for count in range(len(remaining), 0, -1):
            group = remaining[:count]
            request, _ = _request(runtime, group, workspace.runs_dir / "_batches" / batch_id / fingerprint / f"group-{len(plan['groups']):03d}", prior_continuity + [{"lecture_id": lectures[index], "topics": ["x" * 100] * 30, "recap": "x" * 1600} for index in range(prior_count)])
            # Quality passes resend the prior draft. Reserve its complete
            # previous-output budget conservatively before choosing a group.
            if len(QUALITY_PHASES[requested.quality]) > 1 and budget.output_reserve_per_lesson:
                request = with_phase(request, "scoped_review", "thorough", {"reserved_prior_draft": "x" * (budget.output_reserve_per_lesson * count * 4)})
            try:
                fits = _fits(request, count, budget)
            except CommandSizeError:
                fits = False
            if fits:
                chosen = group
                break
        if chosen is None:
            raise WorkflowError(f"Prepared lesson {remaining[0]} cannot fit verified request limits; split its sources explicitly. No evidence was removed or provider invoked.")
        plan["groups"].append(chosen)
        remaining = remaining[len(chosen):]
        prior_count += len(chosen)
    return plan


def _remap_notes(notes: dict, mapping: dict) -> dict:
    reverse = {global_id: local for local, global_id in mapping.items()}
    def visit(value, field=None):
        if isinstance(value, dict):
            return {key: visit(item, key) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item, "evidence_id" if field == "evidence_ids" else None) for item in value]
        if field in {"evidence_id", "visual_id"}:
            return reverse.get(value, value)
        return value
    return visit(notes)


def _normalized(block: dict) -> str:
    if block["kind"] == "visual":
        return ""
    if block.get("text") in {"Supplementary slide material.", "Integrazione dalle slide."}:
        return ""
    content = {key: block[key] for key in ("text", "items", "rows", "expression") if key in block}
    if "text" in content:
        content["text"] = re.sub(r"^(?:Supplementary slide material: |Integrazione dalle slide: )", "", content["text"])
    text = json.dumps(content, ensure_ascii=False)
    return " ".join(re.sub(r"\[\[term:([^\]]+)\]\]|\*\*", lambda match: match.group(1) or "", text).casefold().split())


def _validate_group(response: ProviderResponse, plan: dict, lectures: list[str], maps: dict, *, final_phase: bool, continuity: list[dict]) -> dict:
    from .evidence_refs import normalize_references, EvidenceReferenceError
    valid_ids = {i for mapping in maps.values() for i in mapping.values()}
    try:
        normalized, _ = normalize_references(response.notes, valid_ids)
    except EvidenceReferenceError as error:
        raise WorkflowError(str(error)) from error
    from .generation_diagnostics import group_defects
    defects=group_defects(response,plan,lectures,maps)
    if defects:
        error=WorkflowError("A study summary must remain a brief reference; local content validation requires review: " + "; ".join(f"{d['lecture_id']} {d['path']} {d['code']}" for d in defects))
        error.defects=defects
        raise error
    entries = normalized.get("lessons")
    if not isinstance(entries, list) or [row.get("lecture_id") for row in entries if isinstance(row, dict)] != lectures or len(entries) != len(lectures):
        raise WorkflowError("Batch response must contain each selected lesson exactly once in numeric order")
    notes_by_lesson, duplicate_texts = {}, {}
    for prior in continuity:
        for text in prior.get("narrative_fingerprints", []):
            duplicate_texts[text] = prior["lecture_id"]
    for row in entries:
        lecture = row["lecture_id"]
        if not isinstance(row.get("notes"), dict):
            raise WorkflowError("Batch lesson requires structured notes")
        notes = _remap_notes(row["notes"], maps[lecture])
        pack = plan["packs"][lecture]
        # Ownership is authoritative application metadata. A writer need not
        # spend tokens excluding untouched slides belonging to other lessons.
        # Never synthesize an exclusion for an owned or actually cited page.
        cited = {identifier for block in notes.get('blocks', []) for identifier in block.get('evidence_ids', [])}
        coverage = {item.get('evidence_id') for item in notes.get('evidence_coverage', [])}
        visuals = {item.get('evidence_id') for item in notes.get('visual_decisions', [])}
        available_visuals = {item.get('evidence_id') for item in pack.get('visuals', []) if item.get('visual_decision') != 'exclude'}
        for item in pack['evidence']:
            identifier, canonical = item['id'], item.get('canonical_slide_id')
            owner = plan['ownership'].get(canonical, {}).get('owner')
            if not owner or owner == lecture or identifier in cited:
                continue
            reason = f'Assigned by the reviewed batch ownership ledger to {owner}; not used in this lesson.'
            if identifier not in coverage:
                notes['evidence_coverage'].append({'evidence_id': identifier, 'disposition': 'excluded', 'reason': reason})
            if identifier in available_visuals and identifier not in visuals:
                notes['visual_decisions'].append({'evidence_id': identifier, 'decision': 'exclude', 'reason': reason, 'inspected': False})
        notes = _validate_candidate_notes(notes, lecture, pack, source_root=Path(plan["sources"][lecture]["path"]).parent,
                                          style=plan["style"], final_phase=final_phase)
        if plan["style"].get("image_limit") is not None and sum(v.get("decision") == "include" for v in notes["visual_decisions"]) > plan["style"]["image_limit"]:
            raise WorkflowError("Batch lesson exceeds its image limit")
        local_slides = {item["id"]: item["canonical_slide_id"] for item in pack["evidence"] if item.get("canonical_slide_id")}
        local_evidence = {item["id"]: item for item in pack["evidence"]}
        decisions = {entry["evidence_id"]: entry for entry in notes["visual_decisions"]}
        primary, placements, supplement_labels = set(), {}, {}
        for index, block in enumerate(notes["blocks"]):
            if block["kind"] not in NARRATIVE_KINDS:
                # Study summaries may reference earlier slides briefly, but
                # cannot act as a hidden second full explanation or anticipate
                # content owned by a future lesson. They never satisfy primary
                # slide coverage.
                cited_slides = {local_slides[identifier] for identifier in block["evidence_ids"] if identifier in local_slides}
                for canonical in cited_slides:
                    owner = plan["ownership"][canonical]["owner"]
                    if owner == lecture:
                        continue
                    if parse_lecture_id(owner)[1] >= parse_lecture_id(lecture)[1]:
                        raise WorkflowError("Study summaries cannot anticipate a future lesson's slide")
                    value = block.get("answer", block.get("text", " ".join(block.get("items", []))))
                    if len(str(value).split()) > 50:
                        raise WorkflowError("A study summary of another lesson's slide must remain a brief reference")
                continue
            slides = {local_slides[identifier] for identifier in block["evidence_ids"] if identifier in local_slides}
            for identifier in block["evidence_ids"]:
                placements.setdefault(identifier, []).append(index)
            for canonical in slides:
                owner = plan["ownership"][canonical]
                role = block.get("coverage_role")
                if owner["owner"] == lecture:
                    expected = "supplement" if owner["status"] == "supplement" else "full"
                    if role != expected:
                        source = local_evidence[plan['inventory'][canonical]['occurrences'][lecture]]
                        raise WorkflowError(f"Slide owner requires coverage_role={expected}: {lecture} {source['id']} {Path(source['source_file']).name} page {source['page']}; received {role}")
                    slide_id = plan["inventory"][canonical]["occurrences"][lecture]
                    if not str(local_evidence[slide_id].get("content", "")).strip() and decisions.get(slide_id, {}).get("inspected") is not True:
                        raise WorkflowError("A textless slide explanation requires confirmed source image inspection")
                    primary.add(canonical)
                else:
                    if role not in {"reference", "new_insight"} or block.get("prior_lecture_id") != owner["owner"] or parse_lecture_id(owner["owner"])[1] >= parse_lecture_id(lecture)[1]:
                        raise WorkflowError("A non-owned slide cannot receive a duplicate full explanation or anticipate a future lesson")
                    if block["kind"] != "paragraph":
                        raise WorkflowError("A later slide reference/new insight cannot repeat its table, figure or formula")
                    if role == "reference" and len(str(block.get("text", "")).split()) > 50:
                        raise WorkflowError("A cross-lesson slide reference must remain brief")
                    if role == "new_insight" and not any(identifier.startswith("T")  for identifier in block["evidence_ids"]):
                        raise WorkflowError("New insight requires this lesson's transcript evidence")
            text = _normalized(block)
            digest = _hash(text) if text and len(text) >= 20 else None
            if digest and block.get("coverage_role") != "reference":
                if digest in duplicate_texts and duplicate_texts[digest] != lecture:
                    raise WorkflowError("Batch repeats an identical narrative explanation across lessons")
                duplicate_texts[digest] = lecture
        required = {key for key, choice in plan["ownership"].items() if choice["owner"] == lecture
                    and not _structural_review(choice, local_evidence[plan["inventory"][key]["occurrences"][lecture]], pack)}
        if required - primary:
            raise CoverageError([{"lecture_id":lecture, "evidence_id":plan["inventory"][key]["occurrences"][lecture],
                                  "source_file":local_evidence[plan["inventory"][key]["occurrences"][lecture]]["source_file"],
                                  "page":plan["inventory"][key]["page"], "canonical_slide_id":key,
                                  "reason":"No substantive owner explanation; headings/recaps do not establish coverage"}
                                 for key in sorted(required-primary)])
        for canonical in required:
            choice = plan["ownership"][canonical]
            if choice["status"] != "supplement":
                continue
            local_id = plan["inventory"][canonical]["occurrences"][lecture]
            positions = placements.get(local_id, [])
            for direction, anchor in choice["anchor"].items():
                anchor_positions = placements.get(anchor, [])
                if not anchor_positions or (direction == "before" and max(positions) >= min(anchor_positions)) or (direction == "after" and min(positions) <= max(anchor_positions)):
                    raise WorkflowError("Supplementary slide must appear inline at its selected topic anchors")
            for index in positions:
                block = notes["blocks"][index]
                if block["kind"] == "paragraph" and not block.get("supplement_labelled"):
                    prefix = "Integrazione dalle slide: " if plan["course"].get("languages", {}).get("notes") == "it" else "Supplementary slide material: "
                    if not block["text"].startswith(prefix):
                        block["text"] = prefix + block["text"]
                    block["supplement_labelled"] = True
            first = min(positions)
            if notes["blocks"][first]["kind"] != "paragraph":
                supplement_labels.setdefault(first, []).append(local_id)
        # Anchors were validated above; placing a label immediately before its
        # first substantive block preserves their relative ordering.
        for index in sorted(supplement_labels, reverse=True):
            text = "Integrazione dalle slide." if plan["course"].get("languages", {}).get("notes") == "it" else "Supplementary slide material."
            notes["blocks"].insert(index, {"kind": "paragraph", "text": text,
                                          "evidence_ids": supplement_labels[index], "coverage_role": "supplement",
                                          "supplement_labelled": True})
        notes_by_lesson[lecture] = notes
    return notes_by_lesson


def _continuity(lecture: str, notes: dict) -> dict:
    # Mechanical synopsis; never a separate provider call or independent evidence.
    return {"lecture_id": lecture, "topics": [block["text"][:100] for block in notes["blocks"] if block["kind"] == "heading"][:30],
            "recap": next((block["text"][:1600] for block in reversed(notes["blocks"]) if block["kind"] == "recap"), ""),
            "narrative_fingerprints": [_hash(_normalized(block)) for block in notes["blocks"] if block["kind"] in NARRATIVE_KINDS and len(_normalized(block)) >= 20 and block.get("coverage_role") != "reference"]}


def _unchanged(plan: dict) -> None:
    for lecture, snapshot in {**plan["sources"], **plan.get("prior_publications", {})}.items():
        if file_sha256(Path(snapshot["path"])) != snapshot["sha256"] or any(not Path(path).is_file() or file_sha256(Path(path)) != digest for path, digest in {**snapshot["sources"], **snapshot["assets"], **snapshot.get("candidate", {}), **snapshot.get("published_word", {}), **snapshot.get("manifest", {})}.items()):
            raise WorkflowError(f"Sources changed during batch generation for {lecture}; prior candidates were preserved")


def _stage_visuals(request: ProviderRequest) -> None:
    for visual in request.source_pack.get("visuals", []):
        if not visual.get("original_image_path"):
            continue
        original = Path(visual["original_image_path"])
        target = Path(visual["image_path"])
        if file_sha256(original) != visual["image_sha256"]:
            raise WorkflowError("Source image changed before provider invocation")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and file_sha256(target) != visual["image_sha256"]:
            raise WorkflowError("Staged batch visual is corrupted; inspect it before retrying")
        if not target.exists():
            shutil.copyfile(original, target)


def load_batch_manifest(workspace: Workspace, batch_id: str) -> dict:
    if not re.fullmatch(r"[A-Z0-9]+-[0-9a-f]{12}", batch_id):
        raise WorkflowError("Invalid batch ID")
    manifest = _read(workspace.runs_dir / "_batches" / batch_id / "manifest.json")
    if manifest.get("batch_id") != batch_id:
        raise WorkflowError("Saved manifest does not match the requested batch ID")
    return manifest


def generate_batch(workspace: Workspace, course_code: str, lesson_numbers: list[int], *, provider: str | None = None,
                   quality: str = "standard", selection: dict | None = None, budgets: BatchBudget | dict | None = None,
                   instructions: str = "", image_limit: int | None = None, plan_only: bool = False,
                   provider_adapter: ProviderAdapter | None = None, batch_id: str | None = None,
                   build_word: bool = False, repair_missing: bool = False, pause_after: str | None = None, speaker_focus: str | None = None) -> dict:
    """Plan or resume ordered candidates. Requires already prepared source packs."""
    kwargs = dict(provider=provider, quality=quality, selection=selection, budgets=budgets, instructions=instructions, image_limit=image_limit, speaker_focus=speaker_focus)
    plan = plan_batch(workspace, course_code, lesson_numbers, **kwargs)
    if batch_id is not None and batch_id != plan["batch_id"]:
        raise WorkflowError("Resume batch ID does not match the selected course and lessons")
    if plan_only:
        return plan
    if plan["status"] != "PLANNED":
        _save(workspace.runs_dir / "_batches" / plan["batch_id"] / "manifest.json", plan)
        return plan
    with ExitStack() as locks:
        for lecture in plan["lectures"]:
            locks.enter_context(_generation_lock(workspace, lecture))
        plan = plan_batch(workspace, course_code, lesson_numbers, **kwargs)
        if plan["status"] != "PLANNED":
            _save(workspace.runs_dir / "_batches" / plan["batch_id"] / "manifest.json", plan)
            return plan
        packs, snapshots, inventory = _packs(workspace, plan["lectures"])
        if snapshots != plan["sources"]:
            raise WorkflowError("Sources changed while preparing the batch request; no provider was invoked")
        _published_history(workspace, plan["course_code"], plan["lectures"], inventory)
        runtime = {**plan, "packs": packs, "sources": snapshots, "inventory": inventory, "requested": RequestedModel(**plan["requested_model"])}
        root = workspace.runs_dir / "_batches" / plan["batch_id"]
        version_dir = root / plan["fingerprint"]
        manifest = version_dir / "manifest.json"
        prior_word_candidates = _read(root / "manifest.json").get("word_candidates",{}) if (root/"manifest.json").exists() else {}
        if manifest.exists():
            saved = _read(manifest)
            if saved["fingerprint"] != plan["fingerprint"]:
                raise WorkflowError("Batch manifest fingerprint mismatch")
            prior_word_candidates = {**prior_word_candidates, **saved.get('word_candidates', {})}
        else:
            _save(manifest, plan)
            _save(root / "manifest.json", plan)
        state = WorkflowState(workspace.database_path)
        continuity, results, shared_attempts = deepcopy(plan.get("prior_continuity", [])), [], []
        from .generation_diagnostics import speaker_warning
        warnings=[{"lecture_id":l,**warning} for l,p in packs.items()
                  if (warning:=speaker_warning(p,plan["course"].get("speaker_focus","lecture"))) is not None]
        word_candidates = {}; activity = {}; network_retries = 0
        generation_started = time.monotonic()
        budget_path=None;spent=0.;group_started=generation_started
        from .publication import PublicationError
        try:
            for group_index, lectures in enumerate(plan["groups"]):
                from .batch_recovery import compatible_response, historical_active_seconds, intent, repair_coverage
                group_started = time.monotonic()
                spent = historical_active_seconds(root,runtime,lectures,continuity)
                budget_path = root / "active-budgets" / (_hash(intent(runtime,lectures,continuity)) + ".json")
                if budget_path.exists():spent=max(spent,json.loads(budget_path.read_text())["used_seconds"])
                def budget_remaining(reserve=120):
                    return 1200 - reserve - spent - (time.monotonic()-group_started)
                def save_progress(status="RUNNING",problem=None):
                    progress={**plan,"status":status,"completed_groups":len({e['group'] for e in shared_attempts}),"shared_attempts":shared_attempts,
                              "candidates":results,"word_candidates":word_candidates,"activity":activity,
                              "network_retries":network_retries,"problem":problem,"warnings":warnings}
                    _save(manifest,progress);_save(root/"manifest.json",progress)
                activity={"pid":os.getpid(),"lessons":lectures,"status":"VALIDATING_CACHE"}
                save_progress()
                group_fingerprint = _hash({"lectures": lectures, "sources": {lecture: plan["sources"][lecture] for lecture in lectures},
                                           "ownership": {key: choice for key, choice in plan["ownership"].items() if any(lecture in inventory[key]["occurrences"] for lecture in lectures)},
                                           "requested": plan["requested_model"], "style": plan["style"], "course": plan["course"],
                                           "budgets": plan["budgets"], "contract": plan["contract"], "generation_contract": plan["generation_contract"],
                                           "prompt_contract": plan["prompt_contract"], "continuity": continuity})
                group_dir = root / "generation-checkpoints" / group_fingerprint
                activity['checkpoint']=str(group_dir)
                version_group = version_dir / f"group-{group_index:03d}"
                request, maps = _request(runtime, lectures, group_dir, continuity)
                previous = None
                validated = None
                phases = []
                repair_lineage = None
                origin = None
                phase_metrics = []
                completed_path = group_dir / "complete.json"
                group_reused = completed_path.exists()
                if (version_group / "complete.json").exists():
                    _read(version_group / "complete.json")
                if completed_path.exists():
                    completed = _read(completed_path)
                    repair_lineage=completed.get("repair_lineage")
                    for saved in completed["candidates"]:
                        path = workspace.runs_dir / saved["lecture_id"] / "lecture_notes.candidate.json"
                        if not path.is_file() or file_sha256(path) != saved["sha256"]:
                            raise WorkflowError("Saved batch candidate was edited or removed; inspect it before retrying. No automatic paid regeneration.")
                for phase, context_mode in QUALITY_PHASES[runtime["requested"].quality]:
                    active = with_phase(request, phase, context_mode, previous)
                    checkpoint = group_dir / f"{phase}-{_hash(continuity)[:12]}.json"
                    phase_started = time.monotonic()
                    phase_wall = time.time()
                    validation_started = None
                    response = _load_generation_phase(checkpoint, previous=previous)
                    returned = checkpoint.with_name("returned-" + checkpoint.name)
                    if response is None:
                        response = _load_generation_phase(returned, previous=previous)
                    if response is None and phase == "writing":
                        response,origin=compatible_response(root,runtime,lectures,continuity)
                        if response is not None:
                            _save_generation_phase(returned,previous,response)
                            atomic_json_write(group_dir/"reused-from.json",origin)
                    reused = response is not None
                    if response is None:
                        if batch_id is not None and any(
                                (workspace.runs_dir / lesson / 'lecture_notes.candidate.json').is_file()
                                and json.loads((workspace.runs_dir / lesson / 'lecture_notes.candidate.json').read_text()).get('batch_id') == plan['batch_id']
                                for lesson in lectures):
                            raise WorkflowError('Resume would rewrite an existing batch candidate because its generation inputs changed or its checkpoint is unavailable. Saved notes preserved; review the changed inputs before explicitly starting a new generation.')
                        if completed_path.exists():
                            raise WorkflowError("Completed batch phase is missing; no automatic paid regeneration")
                        budget = budgets if isinstance(budgets, BatchBudget) else BatchBudget(**(budgets or {}))
                        if not _fits(active, len(lectures), budget):
                            raise WorkflowError("Validated continuity/prior draft exceeds the selected group budget; replan smaller groups before invoking a provider")
                        _unchanged(runtime)
                        _stage_visuals(active)
                        if budget_remaining() <= 0:raise WorkflowError("Lesson active time budget exhausted; saved content preserved")
                        from dataclasses import replace
                        active=replace(active,timeout_seconds=budget_remaining())
                        activity={**activity,"status":"WRITING"};save_progress()
                        response = invoke_provider(active, provider_adapter)
                        # A local validation failure must not discard a paid,
                        # successful transport or trigger another paid write.
                        _save_generation_phase(returned, previous, response)
                    try:
                        from .evidence_refs import normalize_references
                        _, transformations=normalize_references(response.notes,{r['id'] for r in active.source_pack['evidence']})
                        if transformations:
                            atomic_json_write(group_dir/"reference-normalization.json",{"attempt":response.attempt_path,"transformations":transformations})
                        if phase == 'writing' and repair_lineage is None:
                            from .batch_recovery import certify_response
                            response=certify_response(response,active,root)
                        validation_started=time.monotonic()
                        activity={**activity,"status":"LOCAL_VALIDATION"};save_progress()
                        validated = _validate_group(response, runtime, lectures, maps, final_phase=phase == QUALITY_PHASES[runtime["requested"].quality][-1][0], continuity=continuity)
                    except CoverageError as error:
                        cached_repair = (group_dir/'coverage-repair/repair.json').is_file()
                        if not repair_missing or phase != "writing" or (budget_remaining() <= 0 and not cached_repair):raise
                        if budget_remaining(reserve=0) <= 0:
                            raise WorkflowError('Lesson active budget exhausted; saved correction retained for local review')
                        certified_base=group_dir/'certified-base.json'
                        if certified_base.exists():
                            bound=_load_generation_phase(certified_base,previous=None)
                            if bound is None or bound.notes!=response.notes:raise WorkflowError('Certified repair base changed; no paid retry')
                        else:_save_generation_phase(certified_base,None,response)
                        activity={**activity,"status":"TARGETED_CORRECTION"};save_progress(problem={"kind":"COVERAGE","defects":error.defects})
                        response,repair_lineage=repair_coverage(active,response,error.defects,maps,provider_adapter,budget_remaining())
                        validated=_validate_group(response,runtime,lectures,maps,final_phase=True,continuity=continuity)
                        repair_lineage["base_response"]=str(certified_base)
                        repair_lineage["validated_notes_sha256"]={l:_hash(n) for l,n in validated.items()}
                        atomic_json_write(group_dir/"coverage-repair/lineage.json",repair_lineage)
                    if not checkpoint.exists():
                        _unchanged(runtime)
                        _save_generation_phase(checkpoint, previous, response)
                    phase_metrics.append({"phase":phase,"started_at_epoch":phase_wall,"ended_at_epoch":time.time(),
                        "elapsed_seconds":time.monotonic()-phase_started,"reused":reused,
                        "local_validation_seconds":time.monotonic()-validation_started if validation_started is not None else None,
                        "attempt_path":response.attempt_path})
                    atomic_json_write(group_dir/"phase-metrics.json",{"lessons":lectures,"phases":phase_metrics,
                        "note":"Provider records distinguish reading, writing, suspension and reported usage; local wall time is not AI time."})
                    phases.append({"phase": phase, "attempt_path": response.attempt_path, "reported_usage": response.usage, "reused": reused, "elapsed_seconds": time.monotonic() - phase_started})
                    previous = response.notes
                _unchanged(runtime)
                assert validated is not None
                group_candidates = []
                for lecture in lectures:
                    notes = validated[lecture]
                    path = workspace.runs_dir / lecture / "lecture_notes.candidate.json"
                    payload = {"notes": notes, "source_pack": snapshots[lecture]["path"], "source_pack_hash": snapshots[lecture]["sha256"],
                               "requested_model": asdict(runtime["requested"]), "actual_model": {"provider": response.provider, "model": response.model, "effort": response.effort},
                               "quality": runtime["requested"].quality, "phases": [phase for phase, _ in QUALITY_PHASES[runtime["requested"].quality]],
                               "batch_id": plan["batch_id"], "batch_fingerprint": plan["fingerprint"], "batch_group": group_index,
                               "request_options": {"instructions": instructions, "image_limit": image_limit}, "effective_style": runtime["style"],
                               "slide_ownership": {key: choice for key, choice in plan["ownership"].items() if choice["owner"] == lecture},
                               "shared_attempt_paths": [entry["attempt_path"] for entry in phases if entry["attempt_path"]],
                               "usage": {}, "usage_scope": "Shared batch attempts are counted once in the batch manifest, never attributed in full to each lesson.", "published_to_word": False}
                    if repair_lineage:
                        payload["repair_lineage"]={"path":str(group_dir/"coverage-repair/lineage.json"),"sha256":file_sha256(group_dir/"coverage-repair/lineage.json")}
                        if repair_lineage.get("repair_attempt"):payload["shared_attempt_paths"].append(repair_lineage["repair_attempt"])
                    preserve_candidate = (origin is not None and path.is_file() and
                                          json.loads(path.read_text()).get('notes') == notes and
                                          json.loads(path.read_text()).get('source_pack_hash') == payload['source_pack_hash'] and
                                          json.loads(path.read_text()).get('requested_model') == payload['requested_model'])
                    if not completed_path.exists() and not preserve_candidate:
                        # Preserve any prior candidate as an immutable version before replacing.
                        if path.exists():
                            old = path.read_bytes()
                            backup = version_dir / "previous-candidates" / f"{lecture}-{hashlib.sha256(old).hexdigest()}.json"
                            backup.parent.mkdir(parents=True, exist_ok=True)
                            if not backup.exists():
                                backup.write_bytes(old)
                        atomic_json_write(path, payload)
                        state.record_candidate(lecture, _hash({"batch": plan["fingerprint"], "lecture": lecture}), response, runtime["requested"].quality, path)
                    group_candidates.append({"lecture_id": lecture, "sha256": file_sha256(path)})
                    continuity.append(_continuity(lecture, notes))
                    results.append({"lecture_id": lecture, "candidate": str(path), "reused": group_reused or preserve_candidate})
                completed_payload = {"candidates": group_candidates, "phases": phases,"repair_lineage":repair_lineage}
                if not completed_path.exists():
                    _save(completed_path, completed_payload)
                if not (version_group / "complete.json").exists():
                    _save(version_group / "complete.json", completed_payload)
                shared_attempts.extend({**entry, "group": group_index, "lessons": lectures} for entry in phases)
                if repair_lineage:
                    shared_attempts.append({"phase":"coverage_repair","group":group_index,"lessons":lectures,"attempt_path":repair_lineage.get("repair_attempt"),"reported_usage":repair_lineage.get("repair_usage",{}),"reused":group_reused})
                for path in group_dir.glob("writing-parts/*/state.json"):
                    network_retries+=json.loads(path.read_text()).get("network_retries",0)
                if build_word:
                    from .publication import build_word_candidate, word_renderer_hash
                    for lecture in lectures:
                        cached_word=prior_word_candidates.get(lecture)
                        if (cached_word and cached_word.get('renderer_hash') == word_renderer_hash()
                                and cached_word.get('source_candidate_hash')==file_sha256(workspace.runs_dir/lecture/'lecture_notes.candidate.json')
                                and Path(cached_word['path']).is_file() and file_sha256(Path(cached_word['path']))==cached_word['sha256']):
                            word_candidates[lecture]=cached_word;continue
                        activity={**activity,"status":"BUILDING_WORD"};save_progress()
                        if budget_remaining(reserve=0)<=0:raise WorkflowError("Word deferred: lesson active budget exhausted")
                        from .execution import prevent_idle_sleep
                        with prevent_idle_sleep():
                            word_started=time.monotonic()
                            built=build_word_candidate(workspace,lecture,timeout_seconds=budget_remaining(reserve=0))
                            phase_metrics.append({"phase":"BUILDING_WORD","lecture_id":lecture,"elapsed_seconds":time.monotonic()-word_started})
                            atomic_json_write(group_dir/"phase-metrics.json",{"lessons":lectures,"phases":phase_metrics})
                        path=Path(built['candidate']);word_candidates[lecture]={"path":str(path),"sha256":file_sha256(path),
                            "source_candidate_hash":file_sha256(workspace.runs_dir/lecture/'lecture_notes.candidate.json'),
                            "renderer_hash":word_renderer_hash()}
                atomic_json_write(budget_path,{"lessons":lectures,"used_seconds":spent+time.monotonic()-group_started})
                save_progress()
                if pause_after and pause_after in lectures:
                    activity={};save_progress(status='PAUSED_REVIEW')
                    return {**_read(manifest),'manifest':str(manifest)}
            ready = {**plan, "status": "CANDIDATE_READY", "completed_groups": len(plan["groups"]),
                     "candidates": results, "manifest": str(manifest), "shared_attempts": shared_attempts,
                     "elapsed_seconds": time.monotonic() - generation_started,"word_candidates":word_candidates,
                     "activity":{},"network_retries":network_retries,"warnings":warnings}
            _save(manifest, ready)
            _save(root / "manifest.json", ready)
            return ready
        except (ProviderError, WorkflowError, PublicationError, KeyboardInterrupt, KeyError, ValueError, OSError, TypeError) as error:
            failure_status=('PAUSED_QUOTA' if isinstance(error,ProviderQuotaError) else
                            'PAUSED_AUTH' if isinstance(error,ProviderAuthenticationError) else
                            'PAUSED_SLEEP' if isinstance(error,ProviderSuspendedError) else
                            'PAUSED_NETWORK' if isinstance(error,ProviderTransportError) else
                            'TIMED_OUT' if isinstance(error,ProviderTimeoutError) else
                            'INTERRUPTED' if isinstance(error,(ProviderCancelledError,KeyboardInterrupt)) else 'FAILED')
            failed = {**plan, "status": failure_status,
                      "error": str(error), "completed_groups": len({entry["group"] for entry in shared_attempts}),
                      "shared_attempts": shared_attempts, "candidates": results,"word_candidates":word_candidates,
                      "activity":{**activity,"status":"STOPPED"},"network_retries":network_retries,
                      "problem":{"kind":type(error).__name__,"message":str(error),"defects":getattr(error,"defects",[]),
                                 "phase":activity.get("status","LOCAL_VALIDATION"),
                                 "next_action":"build_word_without_generation" if isinstance(error,PublicationError) else "review_saved_content",
                                 "saved_content_preserved":True},"warnings":warnings}
            if budget_path is not None:atomic_json_write(budget_path,{"lessons":lectures,"used_seconds":spent+time.monotonic()-group_started})
            _save(manifest, failed)
            _save(root / "manifest.json", failed)
            if not isinstance(error,(ProviderError,WorkflowError,PublicationError,KeyboardInterrupt)):
                raise WorkflowError(f"Batch stopped during {activity.get('status','local processing')}: {error}") from error
            raise
        finally:
            state.close()
