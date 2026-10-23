"""Local Capture dashboard and persistent per-lesson controls; no AI calls."""
from __future__ import annotations
import copy
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from . import capture

FIELDS = {"startsAt", "endsAt", "timezone", "title", "recordingEnabled"}


def apply_local_changes(events: list[dict], overrides: dict, config: dict) -> list[dict]:
    effective = copy.deepcopy(events)
    courses = set(config["courseCodes"])
    for event in effective:
        change = overrides.get("events", {}).get(event["id"], {})
        if not change:
            continue
        # Do not resurrect a provider cancellation or disappeared occurrence.
        original_eligibility = event["eligibility"]
        raw = {**event, **{key: value for key, value in change.items() if key in FIELDS - {"recordingEnabled"}}}
        raw["eligibility"] = "SCHEDULED"
        normalized = capture.normalize_event(raw, courses, event["sourceID"])
        for key in ("startsAt", "endsAt", "timezone", "title"):
            event[key] = normalized[key]
        if original_eligibility in {"CANCELLED", "NEEDS_REVIEW"}:
            continue
        if change.get("recordingEnabled") is False:
            event["eligibility"] = "CANCELLED"
            event["reason"] = "Recording excluded locally"
            event["reasonCode"] = "LOCAL_EXCLUSION"
    return capture.validate_windows(effective, config)


def _assert_editable(store, event_id: str) -> None:
    state = capture.capture_status(store)
    runtime = state["runtime"]
    current = runtime.get("currentSession") or {}
    exporting = any(s.get("event", {}).get("id") == event_id and s.get("status") == "EXPORTING" for s in runtime.get("sessions", []))
    if state["running"] is not False and ((runtime.get("recording") and current.get("event", {}).get("id") == event_id) or exporting):
        raise capture.CaptureError("Stop and save this lesson before editing it")


def _base_event(store, event_id: str) -> dict:
    return next((e for e in store.source_events() if e["id"] == event_id), None) or _missing()


def _missing():
    raise capture.CaptureError("Lesson not found")


def _save_changes(store, changes: dict) -> None:
    events, config = store.source_events(), store.config()
    from .capture_associations import project_events
    apply_local_changes(project_events(store, events), changes, config)  # Validate the same effective associations that will be saved.
    path = store.directory / "overrides.json"
    store.save(path, changes)
    store.save_schedule(events, config, overrides=changes)
    # The native recorder watches the configuration directory. Publish its
    # notification only after the effective schedule is ready to read.
    store.save_config(config)


def edit_lesson(store, event_id: str, patch: dict) -> dict:
    if not isinstance(patch, dict) or not patch or set(patch) - FIELDS:
        raise capture.CaptureError("Edit title, times, timezone or recordingEnabled")
    if "recordingEnabled" in patch and type(patch["recordingEnabled"]) is not bool:
        raise capture.CaptureError("recordingEnabled must be true or false")
    if "title" in patch and (not isinstance(patch["title"], str) or not patch["title"].strip() or len(patch["title"]) > 300):
        raise capture.CaptureError("Lesson title must contain between 1 and 300 characters")
    with store.lock():
        _base_event(store, event_id)
        _assert_editable(store, event_id)
        changes = capture.read_json(store.directory / "overrides.json", {"schemaVersion": 1, "events": {}})
        changes["events"][event_id] = {**changes["events"].get(event_id, {}), **patch}
        _save_changes(store, changes)
    return {"status": "UPDATED", "eventID": event_id, "localChanges": True}


def reset_lesson(store, event_id: str) -> dict:
    with store.lock():
        _base_event(store, event_id)
        _assert_editable(store, event_id)
        changes = capture.read_json(store.directory / "overrides.json", {"schemaVersion": 1, "events": {}})
        changes["events"].pop(event_id, None)
        _save_changes(store, changes)
    return {"status": "RESET", "eventID": event_id}


def add_lesson(store, payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {"courseCode", "lessonNumber", "startsAt", "endsAt", "timezone", "title", "unassigned", "transcriptionLanguage"}:
        raise capture.CaptureError("Supply course, lesson number, title, start, end and timezone")
    if "title" in payload and (not isinstance(payload["title"], str) or not payload["title"].strip() or len(payload["title"]) > 300):
        raise capture.CaptureError("Lesson title must contain between 1 and 300 characters")
    with store.lock():
        config = store.config()
        courses = {c["code"] for c in store.workspace.configuration_store().load()["courses"]}
        config["courseCodes"] = sorted(courses)
        raw = {**payload, "id": "special:" + uuid.uuid4().hex}
        if payload.get("unassigned") is True:
            capture.require_standalone(store)
            raw["recordingID"] = uuid.uuid4().hex
        event = capture.normalize_event(raw, courses, "special")
        if capture.timestamp(event["startsAt"]) <= datetime.now(UTC):
            raise capture.CaptureError("Schedule a future lesson; use Record now for immediate recording")
        events = store.source_events()
        if event["courseCode"] is not None and any((e["courseCode"], e["lessonNumber"]) == (event["courseCode"], event["lessonNumber"]) for e in events):
            raise capture.CaptureError("This course and lesson number already exist")
        store.save_schedule(events + [event], config)
        store.save_config(config)
    return {"status": "ADDED", "eventID": event["id"]}


def unsubscribe(store, source_id: str) -> dict:
    with store.lock():
        config = store.config()
        if not any(s["sourceID"] == source_id for s in config["sources"]):
            raise capture.CaptureError("Calendar source not found")
        events = store.source_events()
        for event in events:
            if event["sourceID"] == source_id:
                _assert_editable(store, event["id"])
                event["eligibility"], event["reason"] = "CANCELLED", "Calendar disconnected"
                event["reasonCode"] = "CALENDAR_DISCONNECTED"
        config["sources"] = [s for s in config["sources"] if s["sourceID"] != source_id]
        store.save_schedule(events, config)
        store.save_config(config)
    return {"status": "UNSUBSCRIBED", "sourceID": source_id}


def dashboard(store) -> dict:
    with store.lock():
        config = store.config()
        # Read a coherent local snapshot; do not invoke sync or launch an app.
        state = capture.capture_status(store)
        changes = capture.read_json(store.directory / "overrides.json", {"events": {}})["events"]
        base = {e["id"]: e for e in store.source_events()}
        events = []
        for original in store.schedule()["events"]:
            event = copy.deepcopy(original)
            event["localChanges"] = bool(changes.get(event["id"]))
            event["recordingEnabled"] = changes.get(event["id"], {}).get("recordingEnabled", True)
            event["sourceEligibility"] = base.get(event["id"], event)["eligibility"]
            event["special"] = event["sourceID"] == "special"
            events.append(event)
        sync_state = state["sync"].get("sources", {})
        responses = []
        paths = sorted((store.directory / "responses").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:16]
        for path in paths:
            try:
                response = capture.read_json(path)
                if isinstance(response.get("requestID"), str) and isinstance(response.get("status"), str):
                    responses.append({key: response.get(key) for key in ("requestID", "status", "message")})
            except (OSError, ValueError, AttributeError):
                continue
        sources = [{"sourceID": s["sourceID"], "label": s.get("label", urlsplit(s["url"]).hostname or "Calendar"),
                    "host": urlsplit(s["url"]).hostname or "", "timezone": s["timezone"],
                    "lastSuccess": sync_state.get(s["sourceID"], {}).get("lastSuccess"),
                    "nextAttempt": sync_state.get(s["sourceID"], {}).get("nextAttempt"),
                    "error": sync_state.get(s["sourceID"], {}).get("error"),
                    **{key: sync_state.get(s["sourceID"], {}).get(key) for key in ("code", "message", "retryable", "suggestedAction")},
                    "mappings": s.get("mappings", []), "importMode": s.get("importMode", "mapped"),
                    "summary": sync_state.get(s["sourceID"], {}).get("summary")} for s in config["sources"]]
        for source in sources:
            if source["summary"] is not None:
                selected = [e for e in events if e['sourceID'] == source['sourceID']]
                source['summary'] = {**source['summary'],
                    'recognizedCourses': len({e['courseCode'] for e in selected if e.get('courseCode')}),
                    'pending': sum(e.get('courseCode') is None for e in selected)}
        courses = [{"code": c["code"], "name": c["name"], "contextPending": c.get("context_setup") == "pending"} for c in store.workspace.configuration_store().load()["courses"]]
        return {"schemaVersion": 1, "configured": store.config_path.exists(), "enabled": config["enabled"], "paused": config["paused"],
                "running": state["running"], "processCheck": state["processCheck"], "runtime": state["runtime"],
                "sources": sources, "events": events, "courses": courses, "refreshHours": config["refreshHours"], "responses": responses,
                "standaloneSupported": capture.standalone_supported(store), "calendarRecordingsSupported": capture.calendar_recordings_supported(store)}
