"""Private calendar ingestion and control for the native menu-bar recorder.

No provider calls, transcription, or document publication occur here. The native
app owns runtime state; this module owns versioned configuration and schedules.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .utils import atomic_json_write, now_compact_utc
from .workspace import Workspace
from . import capture_links

LABEL = "com.gabrizanni.mynotes-capture"
MAX_FEED_BYTES = 10 * 1024 * 1024


class CaptureError(ValueError):
    """A capture operation could not be safely completed."""


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def timestamp(value: str | datetime, timezone: str | None = None) -> datetime:
    if not isinstance(value, (str, datetime)):
        raise CaptureError("Event times must be date-time strings")
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.tzinfo is None:
        if not timezone:
            raise CaptureError("An explicit event timezone is required")
        zone = ZoneInfo(timezone)
        first, second = result.replace(tzinfo=zone, fold=0), result.replace(tzinfo=zone, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise CaptureError("Ambiguous or nonexistent local time; provide an explicit UTC offset")
        result = first
    return result.astimezone(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def runtime_home() -> Path:
    return Path(os.environ.get("MYNOTES_CAPTURE_HOME", str(Path.home() / "Library/Application Support/MyNotes/Capture"))).expanduser()


class CaptureStore:
    def __init__(self, workspace: Workspace):
        if not workspace.is_initialized():
            raise CaptureError("Initialize the MyNotes workspace before configuring Capture")
        self.workspace = workspace
        self.config_path = workspace.config_dir / "capture.json"
        self.directory = workspace.state_dir / "capture"
        self.schedule_path = self.directory / "schedule.json"
        self.runtime_path = self.directory / "runtime.json"

    @contextmanager
    def lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "control.lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield

    def config(self) -> dict:
        key = hashlib.sha256(str(self.workspace.root).encode()).hexdigest()[:24]
        return read_json(self.config_path, {
            "schemaVersion": 1, "revision": 0, "enabled": False, "paused": False,
            "refreshHours": 6, "recordBeforeSeconds": 0, "recordAfterSeconds": 120,
            "outputDirectory": str(self.workspace.root / "recordings/capture"),
            "stageDirectory": str(runtime_home() / "workspaces" / key / "staging"),
            "pythonExecutable": sys.executable, "courseCodes": [], "sources": [],
        })

    def schedule(self) -> dict:
        return read_json(self.schedule_path, {"schemaVersion": 1, "revision": 0, "events": []})

    def source_events(self) -> list[dict]:
        return read_json(self.directory / "source-events.json", self.schedule())["events"]

    def save(self, path: Path, payload: dict) -> None:
        old = read_json(path)
        if old is not None:
            atomic_json_write(self.directory / "history" / f"{now_compact_utc()}-{path.name}", old)
        payload["revision"] = (old or {}).get("revision", 0) + 1
        atomic_json_write(path, payload)

    def save_config(self, config: dict) -> None:
        config["courseCodes"] = sorted(c["code"] for c in self.workspace.configuration_store().load()["courses"])
        config["pythonExecutable"] = sys.executable
        self.save(self.config_path, config)

    def save_schedule(self, events: list[dict], config: dict, *, overrides: dict | None = None) -> None:
        from .capture_dashboard import apply_local_changes
        changes = read_json(self.directory / "overrides.json", {"events": {}}) if overrides is None else overrides
        from .capture_associations import project_events
        effective = apply_local_changes(project_events(self, events), changes, config)
        self.save(self.directory / "source-events.json", {"schemaVersion": 1, "events": events})
        self.save(self.schedule_path, {"schemaVersion": 1, "events": effective})


def normalize_event(raw: dict, courses: set[str], source_id: str = "static") -> dict:
    if not isinstance(raw, dict) or not {"startsAt", "endsAt"} <= raw.keys():
        raise CaptureError("Each event needs courseCode, lessonNumber, startsAt and endsAt")
    standalone = raw.get("recordingID") is not None
    if standalone:
        if not re.fullmatch(r"[a-f0-9]{32}", str(raw["recordingID"])):
            raise CaptureError("An independent recording needs a valid recordingID")
        course, number = raw.get("courseCode"), raw.get("lessonNumber")
        if course is not None and (course not in courses or not re.fullmatch(r"[A-Z0-9]+", str(course))):
            raise CaptureError("Unknown course")
        if number is not None and (type(number) is not int or not 1 <= number <= 999):
            raise CaptureError("Invalid lesson number")
        title = raw.get("title")
        if not isinstance(title, str) or not title.strip() or len(title) > 8192 or any(ord(c) < 32 for c in title):
            raise CaptureError("Recording title must contain 1–8192 printable characters")
    else:
        course, number = str(raw.get("courseCode", "")).upper(), raw.get("lessonNumber")
        if course not in courses or not re.fullmatch(r"[A-Z0-9]+", course):
            raise CaptureError("Event must identify one configured course")
        if type(number) is not int or not 1 <= number <= 999:
            raise CaptureError("lessonNumber must be an integer between 1 and 999")
    timezone = raw.get("timezone")
    if timezone:
        try:
            ZoneInfo(timezone)
        except KeyError as error:
            raise CaptureError("Unknown timezone") from error
    start, end = timestamp(raw["startsAt"], timezone), timestamp(raw["endsAt"], timezone)
    if end <= start or end - start > timedelta(hours=12):
        raise CaptureError("Recording windows must last between zero and twelve hours")
    identity = raw.get("id") or (f"recording:{raw['recordingID']}" if standalone else f"{source_id}:{course}-L{number:02d}")
    if not isinstance(identity, str) or not identity or len(identity) > 2048:
        raise CaptureError("Invalid event identifier")
    eligibility = raw.get("eligibility", "SCHEDULED")
    if eligibility not in {"SCHEDULED", "CANCELLED", "NEEDS_REVIEW"}:
        raise CaptureError("Invalid event eligibility")
    result = {"id": identity, "sourceID": source_id, "courseCode": course, "lessonNumber": number,
            "startsAt": iso(start), "endsAt": iso(end), "timezone": timezone or "UTC",
            "title": str(raw.get("title", course)), "eligibility": eligibility,
            "reason": str(raw.get("reason", ""))}
    if standalone:
        result["recordingID"] = raw["recordingID"]
        result["title"] = raw["title"].strip()
    for key in ("associationState", "candidateCourses", "calendarUID", "originalOccurrence"):
        if key in raw:
            result[key] = copy.deepcopy(raw[key])
    if raw.get("transcriptionLanguage") is not None:
        language = raw["transcriptionLanguage"]
        if not isinstance(language, str) or not re.fullmatch(r"auto|[a-z]{2,3}(?:-[A-Za-z]{2,4})?", language):
            raise CaptureError("Invalid transcription language")
        result["transcriptionLanguage"] = language
    return result


def validate_windows(events: list[dict], config: dict) -> list[dict]:
    result = copy.deepcopy(events)
    seen = set()
    for event in result:
        if event["id"] in seen:
            raise CaptureError("Duplicate event identity")
        seen.add(event["id"])
        if event["eligibility"] == "CONFLICT":
            event["eligibility"], event["reason"] = "SCHEDULED", ""
        event["recordAt"] = iso(timestamp(event["startsAt"]) - timedelta(seconds=config["recordBeforeSeconds"]))
        event["stopAt"] = iso(timestamp(event["endsAt"]) + timedelta(seconds=config["recordAfterSeconds"]))
    ordered = sorted(result, key=lambda e: (e["startsAt"], e["id"]))
    eligible = [e for e in ordered if e["eligibility"] == "SCHEDULED"]
    conflicts = set()
    for i, first in enumerate(eligible):
        for second in eligible[i + 1:]:
            if second["startsAt"] < first["endsAt"] or (first["courseCode"] is not None and first["lessonNumber"] is not None and (first["courseCode"], first["lessonNumber"]) == (second["courseCode"], second["lessonNumber"])):
                conflicts.update((first["id"], second["id"]))
    for event in eligible:
        if event["id"] in conflicts:
            event["eligibility"], event["reason"] = "CONFLICT", "Overlapping lectures or duplicate course/lesson"
    playable = [e for e in eligible if e["eligibility"] == "SCHEDULED"]
    for first, second in zip(playable, playable[1:]):
        # Keep real lesson time intact. Margins alone must not create a conflict.
        if first["stopAt"] > second["startsAt"]:
            first["stopAt"] = second["startsAt"]
        if second["recordAt"] < first["endsAt"]:
            second["recordAt"] = first["endsAt"]
        if first["stopAt"] > second["recordAt"]:
            first["stopAt"] = second["recordAt"]
    return ordered


def configure(store: CaptureStore, patch: dict) -> dict:
    allowed = {"refreshHours", "recordBeforeSeconds", "recordAfterSeconds", "outputDirectory"}
    if not isinstance(patch, dict) or set(patch) - allowed:
        raise CaptureError("Supported configuration fields: " + ", ".join(sorted(allowed)))
    with store.lock():
        config = store.config()
        state = capture_status(store)
        active = state["running"] is not False and (state["runtime"].get("recording", False) or state["runtime"].get("exporting", False))
        if active and "outputDirectory" in patch:
            raise CaptureError("Stop the recording before changing its output directory")
        for key in ("recordBeforeSeconds", "recordAfterSeconds"):
            if key in patch and (type(patch[key]) is not int or not 0 <= patch[key] <= 1800):
                raise CaptureError("Recording margins must be integers from 0 to 1800 seconds")
        if "refreshHours" in patch and (type(patch["refreshHours"]) not in {int, float} or not 0.25 <= patch["refreshHours"] <= 168):
            raise CaptureError("refreshHours must be between 0.25 and 168")
        if "outputDirectory" in patch:
            patch = {**patch, "outputDirectory": str(Path(patch["outputDirectory"]).expanduser().resolve())}
        config.update(patch)
        if "refreshHours" in patch:
            sync_path = store.directory / "sync.json"
            sync_state = read_json(sync_path, {"sources": {}})
            for meta in sync_state["sources"].values():
                if meta.get("lastSuccess") and not meta.get("error"):
                    meta["nextAttempt"] = iso(timestamp(meta["lastSuccess"]) + timedelta(hours=config["refreshHours"]))
            if sync_path.exists():
                atomic_json_write(sync_path, sync_state)
        # Staging is intentionally not configurable into a synced materials folder.
        Path(config["outputDirectory"]).mkdir(parents=True, exist_ok=True)
        Path(config["stageDirectory"]).mkdir(parents=True, exist_ok=True)
        settings = store.workspace.configuration_store().load()
        if config["outputDirectory"] not in settings["paths"]["recording_dirs"]:
            settings["paths"]["recording_dirs"].append(config["outputDirectory"])
            store.workspace.configuration_store().save(settings)
        config["courseCodes"] = sorted(c["code"] for c in settings["courses"])
        store.save_schedule(store.source_events(), config)
        store.save_config(config)
        return {"status": "CONFIGURED", "revision": config["revision"], "outputDirectory": config["outputDirectory"]}


def import_schedule(store: CaptureStore, payload: Any) -> dict:
    raw_events = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(raw_events, list):
        raise CaptureError("Supply a JSON list or an object containing events")
    with store.lock():
        config = store.config()
        courses = {c["code"] for c in store.workspace.configuration_store().load()["courses"]}
        incoming = [normalize_event(e, courses) for e in raw_events]
        ids = {e["id"] for e in incoming}
        old = store.source_events()
        prior = {e["id"]: e for e in old}
        for event in incoming:
            previous = prior.get(event["id"])
            if previous and (event["courseCode"], event["lessonNumber"]) != (previous["courseCode"], previous["lessonNumber"]):
                raise CaptureError("An existing event cannot be renumbered")
        retained = [e for e in old if e["sourceID"] != "static"]
        retained += [{**e, "eligibility": "CANCELLED", "reason": "Removed from static timetable"}
                     for e in old if e["sourceID"] == "static" and e["id"] not in ids]
        store.save_schedule(retained + incoming, config)
        store.save_config(config)
        return {"status": "IMPORTED", "events": len(incoming), "revision": store.schedule()["revision"]}


def prepare_source(store: CaptureStore, source: dict) -> dict:
    if not isinstance(source, dict) or not {"url", "timezone", "periodStart", "periodEnd"} <= source.keys():
        raise CaptureError("Subscription needs url, timezone, periodStart and periodEnd")
    source = copy.deepcopy(source)
    source_id = source.get("sourceID") or uuid.uuid4().hex
    url = capture_links.resolve(source.get("url", ""))
    parsed = urllib.parse.urlsplit(url)
    timezone = source.get("timezone")
    if not timezone:
        raise CaptureError("Provide the calendar timezone")
    try:
        ZoneInfo(timezone)
    except KeyError as error:
        raise CaptureError("Unknown calendar timezone") from error
    start, end = timestamp(source["periodStart"], timezone), timestamp(source["periodEnd"], timezone)
    if not start < end or end - start > timedelta(days=550):
        raise CaptureError("Set a teaching period of at most 550 days")
    mode = source.get("importMode", "mapped")
    if mode not in {"mapped", "all_timed"}:
        raise CaptureError("Unknown calendar import mode")
    if mode == "all_timed":
        try:
            require_calendar_recordings(store)
        except CaptureError as error:
            raise capture_links.CalendarLinkError("RECORDER_INCOMPATIBLE") from error
    mappings = source.get("mappings", [])
    courses = {c["code"] for c in store.workspace.configuration_store().load()["courses"]}
    if not isinstance(mappings, list) or (mode == "mapped" and not mappings):
        raise CaptureError("Provide title-to-course mappings")
    for mapping in mappings:
        if not isinstance(mapping, dict) or not isinstance(mapping.get("match"), str) or not mapping["match"] or mapping.get("courseCode") not in courses:
            raise CaptureError("Every mapping needs a title match and a configured courseCode")
        if (mode == "mapped" or "firstLesson" in mapping) and (type(mapping.get("firstLesson")) is not int or not 1 <= mapping["firstLesson"] <= 999):
            raise CaptureError("Every mapping needs an explicit firstLesson number")
    label = source.get("label", parsed.hostname)
    if not isinstance(label, str) or not label.strip() or len(label) > 200:
        raise CaptureError("Calendar label must contain between 1 and 200 characters")
    source = {"sourceID": str(source_id), "label": label.strip(), "url": url, "timezone": timezone,
              "periodStart": iso(start), "periodEnd": iso(end), "mappings": mappings, "importMode": mode}
    return source


def check_source_replacement(store: CaptureStore, current: dict | None, source: dict, metadata: dict) -> None:
    if not current or current["url"] == source["url"]:
        return
    try:
        same_url = capture_links.resolve(current["url"]) == source["url"]
    except capture_links.CalendarLinkError:
        same_url = False
    same_google = capture_links.google_identity(current["url"])
    if same_url or (same_google and same_google == capture_links.google_identity(source["url"])):
        return
    populated = any(e["sourceID"] == source["sourceID"] for e in store.source_events())
    if not populated and not metadata.get("lastSuccess"):
        return
    raise capture_links.CalendarLinkError("SOURCE_CHANGED")


def subscribe(store: CaptureStore, source: dict) -> dict:
    try:
        source = prepare_source(store, source)
    except capture_links.CalendarLinkError as error:
        raise CaptureError(str(error)) from error
    source_id = source["sourceID"]
    with store.lock():
        config = store.config()
        current = next((s for s in config["sources"] if s["sourceID"] == source_id), None)
        path = store.directory / "sync.json"
        metadata = read_json(path, {"sources": {}})
        check_source_replacement(store, current, source, metadata["sources"].get(source_id, {}))
        sources = [s for s in config["sources"] if s["sourceID"] != source_id] + [source]
        if len(sources) > 10:
            raise CaptureError("At most ten calendar sources are supported")
        if current and current != source:
            metadata["sources"].pop(source_id, None)
            atomic_json_write(path, metadata)
        config["sources"] = sources
        store.save_config(config)
        return {"status": "SUBSCRIBED", "sourceID": source_id}


def recognition_key(details: list[dict]) -> str:
    return hashlib.sha256(json.dumps([
        {key: c.get(key) for key in ("code", "name", "aliases")}
        for c in details], sort_keys=True).encode()).hexdigest()


def project_calendar(data: bytes, source: dict, old: list[dict], details: list[dict]) -> tuple[list[dict], dict]:
    capture_links.validate_body(data, source["url"])
    summary = {}
    try:
        events = calendar_events(data, source, old, {c["code"] for c in details}, course_details=details, report=summary)
    except (ValueError, KeyError, TypeError) as error:
        raise capture_links.CalendarLinkError("INVALID_CALENDAR") from error
    if not summary:
        summary = {"imported": len(events), "recognizedCourses": len({e["courseCode"] for e in events if e.get("courseCode")}),
                   "pending": 0, "excluded": 0, "issues": []}
    return events, summary


def connect(store: CaptureStore, raw: dict) -> dict:
    """Verify once before publishing; reproject against current state under lock."""
    source = None
    try:
        try:
            source = prepare_source(store, raw)
        except CaptureError as error:
            raise capture_links.CalendarLinkError("INVALID_SOURCE") from error
        # No subscription/schedule writes and no calendar lock during network I/O.
        data, headers = download(source, {})
        if data is None:
            raise capture_links.CalendarLinkError("INVALID_CALENDAR")
        project_calendar(data, source, [], store.workspace.configuration_store().load()["courses"])
        with store.lock():
            config = store.config()
            current = next((s for s in config["sources"] if s["sourceID"] == source["sourceID"]), None)
            metadata = read_json(store.directory / "sync.json", {"sources": {}})
            check_source_replacement(store, current, source, metadata["sources"].get(source["sourceID"], {}))
            sources = [s for s in config["sources"] if s["sourceID"] != source["sourceID"]] + [source]
            if len(sources) > 10:
                raise capture_links.CalendarLinkError("INVALID_SOURCE")
            if source["importMode"] == "all_timed":
                try:
                    require_calendar_recordings(store)
                except CaptureError as error:
                    raise capture_links.CalendarLinkError("RECORDER_INCOMPATIBLE") from error
            details = store.workspace.configuration_store().load()["courses"]
            old = store.source_events()
            imported, summary = project_calendar(data, source, old, details)
            events = validate_windows([e for e in old if e["sourceID"] != source["sourceID"]] + imported, config)
            now = datetime.now(UTC)
            metadata["sources"][source["sourceID"]] = success_metadata(source, headers, details, summary, now, config)
            config["sources"] = sources
            config["courseCodes"] = sorted(c["code"] for c in details)
            store.save_schedule(events, config)
            atomic_json_write(store.directory / "sync.json", metadata)
            store.save_config(config)  # Notify only after the effective schedule is ready.
        count = summary.get("imported", len(imported))
        message = f"Calendario collegato: {count} eventi nel periodo selezionato."
        if count == 0:
            message += " Il calendario è valido, ma non contiene lezioni nel periodo scelto."
        return {"status": "CONNECTED", "sourceID": source["sourceID"], "summary": summary, "message": message}
    except Exception as error:
        return {"status": "FAILED", **capture_links.diagnostic(error, source["url"] if source else "")}


def success_metadata(source, headers, details, summary, now, config):
    return {**{k: headers[k] for k in ("etag", "lastModified") if k in headers}, "feedURLHash": hashlib.sha256(source["url"].encode()).hexdigest(),
            "recognitionKey": recognition_key(details), "summary": summary,
            "lastSuccess": iso(now), "lastAttempt": iso(now), "error": None,
            "nextAttempt": iso(now + timedelta(hours=config["refreshHours"]))}


def download(source: dict, metadata: dict) -> tuple[bytes | None, dict]:
    headers = {"User-Agent": "MyNotes-Capture/1", "Accept": "text/calendar"}
    for key, header in (("etag", "If-None-Match"), ("lastModified", "If-Modified-Since")):
        if metadata.get(key):
            headers[header] = metadata[key]
    class CalendarRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, redirect_headers, newurl):
            if urllib.parse.urlsplit(newurl).scheme != "https":
                raise capture_links.CalendarLinkError("INSECURE_REDIRECT")
            return super().redirect_request(req, fp, code, msg, redirect_headers, newurl)
    opener = urllib.request.build_opener(CalendarRedirect())
    try:
        with opener.open(urllib.request.Request(source["url"], headers=headers), timeout=20) as response:
            if urllib.parse.urlsplit(response.url).scheme != "https":
                raise capture_links.CalendarLinkError("INSECURE_REDIRECT")
            data = response.read(MAX_FEED_BYTES + 1)
            if len(data) > MAX_FEED_BYTES:
                raise capture_links.CalendarLinkError("FEED_TOO_LARGE")
            return data, {"etag": response.headers.get("ETag"), "lastModified": response.headers.get("Last-Modified")}
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return None, metadata
        raise


def calendar_events(data: bytes, source: dict, old: list[dict], courses: set[str], *, course_details=None, report=None) -> list[dict]:
    if source.get("importMode", "mapped") == "all_timed":
        from .capture_calendar import all_timed_events
        return all_timed_events(data, source, old, courses, course_details=course_details, report=report)
    import icalendar
    import recurring_ical_events

    calendar = icalendar.Calendar.from_ical(data)
    if calendar.name != "VCALENDAR":
        raise CaptureError("Response is not a calendar")
    # Reject malformed components instead of silently removing scheduled lectures.
    if any(component.errors for component in calendar.walk()):
        raise CaptureError("Calendar contains malformed properties")
    start, end = timestamp(source["periodStart"]), timestamp(source["periodEnd"])
    expanded = recurring_ical_events.of(calendar).between(start, end)
    recurring_uids = {str(e.get("UID", "")) for e in calendar.walk("VEVENT") if "RRULE" in e or "RDATE" in e}
    previous = {e["id"]: e for e in old}
    max_numbers = {course: max((e["lessonNumber"] for e in old if e["courseCode"] == course and type(e["lessonNumber"]) is int), default=0) for course in courses}
    events = []
    cancelled_uids = {str(e.get("UID", "")) for e in calendar.walk("VEVENT")
                      if str(e.get("STATUS", "")).upper() == "CANCELLED" and "RECURRENCE-ID" not in e}
    cancelled_instances = {(str(e.get("UID", "")), iso(timestamp(e["RECURRENCE-ID"].dt, source["timezone"])))
                           for e in calendar.walk("VEVENT") if str(e.get("STATUS", "")).upper() == "CANCELLED" and "RECURRENCE-ID" in e}
    for component in sorted(expanded, key=lambda c: timestamp(c.decoded("DTSTART"), source["timezone"]) if isinstance(c.decoded("DTSTART"), datetime) else datetime.min.replace(tzinfo=UTC)):
        title = str(component.get("SUMMARY", ""))
        matched = [m for m in source["mappings"] if m["match"].casefold() in title.casefold()]
        if not matched:
            continue
        dtstart = component.decoded("DTSTART")
        dtend = component.decoded("DTEND", None)
        if not isinstance(dtstart, datetime) or not isinstance(dtend, datetime):
            raise CaptureError("Selected lectures need start/end times; all-day events are unsupported")
        uid = str(component.get("UID", ""))
        if not uid:
            raise CaptureError("Selected events need stable UID values")
        original = component.decoded("RECURRENCE-ID", dtstart)
        occurrence = iso(timestamp(original, source["timezone"])) if str(component.get("UID", "")) in recurring_uids else "single"
        identity = source["sourceID"] + ":" + uid + ":" + occurrence
        prior = previous.get(identity)
        mapping = matched[0]
        course = prior["courseCode"] if prior else mapping["courseCode"]
        official = re.search(r"\b(?:L|Lesson\s*|Lecture\s*|Lezione\s*)(\d{1,3})\b", title, re.I)
        number = prior["lessonNumber"] if prior else (int(official[1]) if official else max(max_numbers[course] + 1, mapping["firstLesson"]))
        max_numbers[course] = max(max_numbers[course], number)
        eligibility, reason = "SCHEDULED", ""
        if len(matched) != 1:
            eligibility, reason = "NEEDS_REVIEW", "Title matches multiple mappings"
        if str(component.get("STATUS", "")).upper() == "CANCELLED" or uid in cancelled_uids or (uid, iso(timestamp(original, source["timezone"]))) in cancelled_instances:
            eligibility, reason = "CANCELLED", "Calendar cancellation"
        raw = {"id": identity, "courseCode": course, "lessonNumber": number, "startsAt": dtstart,
               "endsAt": dtend, "timezone": source["timezone"], "title": title, "eligibility": eligibility, "reason": reason}
        event = normalize_event(raw, courses, source["sourceID"])
        events.append(event)
    ids = {e["id"] for e in events}
    for event in old:
        if event["sourceID"] != source["sourceID"] or event["id"] in ids:
            continue
        source_uid = event.get("calendarUID")
        is_cancelled = source_uid in cancelled_uids or (source_uid, event.get("originalOccurrence")) in cancelled_instances
        retained = {**event, "eligibility": "CANCELLED" if is_cancelled else "NEEDS_REVIEW",
                    "reason": "Calendar cancellation" if is_cancelled else "Event disappeared from calendar; verify before recording"}
        events.append(retained)
    # Store source identity separately, so UID values containing colons stay valid.
    components_by_id = {}
    for component in expanded:
        if "UID" not in component or "DTSTART" not in component:
            continue
        original = component.decoded("RECURRENCE-ID", component.decoded("DTSTART"))
        if not isinstance(original, datetime):
            continue
        occurrence = iso(timestamp(original, source["timezone"])) if str(component.get("UID", "")) in recurring_uids else "single"
        components_by_id[source["sourceID"] + ":" + str(component["UID"]) + ":" + occurrence] = (str(component["UID"]), iso(timestamp(original, source["timezone"])))
    for event in events:
        if event["id"] in components_by_id:
            event["calendarUID"], event["originalOccurrence"] = components_by_id[event["id"]]
    return events


def sync(store: CaptureStore, *, if_due: bool = False, now: datetime | None = None, source_id: str | None = None) -> dict:
    now = now or datetime.now(UTC)
    with store.lock():
        config = store.config()
        events = store.source_events()
        if source_id is not None and not any(s["sourceID"] == source_id for s in config["sources"]):
            raise CaptureError("Calendar source not found")
        path = store.directory / "sync.json"
        metadata = read_json(path, {"sources": {}})
        courses = {c["code"] for c in store.workspace.configuration_store().load()["courses"]}
        reports = []
        for source in config["sources"]:
            if source_id is not None and source["sourceID"] != source_id:
                continue
            calendar_id = source["sourceID"]
            meta = metadata["sources"].get(calendar_id, {})
            if if_due and (meta.get("retryable") is False or (meta.get("nextAttempt") and timestamp(meta["nextAttempt"]) > now)):
                continue
            resolved = None
            try:
                resolved = {**source, "url": capture_links.resolve(source["url"])}
                if source.get("importMode") == "all_timed":
                    try:
                        require_calendar_recordings(store)
                    except CaptureError as error:
                        raise capture_links.CalendarLinkError("RECORDER_INCOMPATIBLE") from error
                course_details = store.workspace.configuration_store().load()["courses"]
                key = recognition_key(course_details)
                url_hash = hashlib.sha256(resolved["url"].encode()).hexdigest()
                changed_url = (source["url"] != resolved["url"] or
                               (meta.get("feedURLHash") is not None and meta["feedURLHash"] != url_hash))
                validators = {} if changed_url or (source.get("importMode") == "all_timed" and meta.get("recognitionKey") != key) else meta
                data, headers = download(resolved, validators)
                summary = meta.get("summary")
                if data is None and not meta.get("lastSuccess"):
                    raise capture_links.CalendarLinkError("INVALID_CALENDAR")
                if data is not None:
                    imported, summary = project_calendar(data, resolved, events, course_details)
                    candidate = [e for e in events if e["sourceID"] != calendar_id] + imported
                    events = validate_windows(candidate, config)
                meta = success_metadata(resolved, headers, course_details, summary, now, config)
                source["url"] = resolved["url"]
                reports.append({"sourceID": calendar_id, "status": "UNCHANGED" if data is None else "UPDATED", "summary": summary})
            except Exception as error:
                detail = capture_links.diagnostic(error, resolved["url"] if resolved else "")
                meta = {**meta, **detail, "lastAttempt": iso(now), "error": detail["code"],
                        "nextAttempt": iso(now + timedelta(minutes=15)) if detail["retryable"] else None}
                reports.append({"sourceID": calendar_id, "status": "FAILED", "error": detail["code"], **detail})
            metadata["sources"][calendar_id] = meta
        courses_changed = sorted(courses) != config["courseCodes"]
        config["courseCodes"] = sorted(courses)
        if reports:
            store.save_schedule(events, config)
            atomic_json_write(path, metadata)
        if reports or courses_changed:
            # Wake the native watcher after publishing all calendar changes.
            store.save_config(config)
        failed = [r for r in reports if r["status"] == "FAILED"]
        output = {"status": "SYNC_FAILED" if failed else "SYNCED", "sources": reports}
        if failed:
            output["message"] = failed[0]["message"]
        return output


def capture_status(store: CaptureStore) -> dict:
    runtime = read_json(store.runtime_path, {})
    running: bool | None = False
    process_check = "CHECKED"
    if runtime.get("pid") and runtime.get("updatedAt"):
        try:
            os.kill(int(runtime["pid"]), 0)
            running = datetime.now(UTC) - timestamp(runtime["updatedAt"]) < timedelta(seconds=90)
        except PermissionError:
            # Provider sandboxes can deny process probes even when the app runs.
            running = None
            process_check = "UNAVAILABLE"
        except (OSError, ValueError):
            pass
    config = store.config()
    return {"workspace": str(store.workspace.root), "enabled": config["enabled"], "paused": config["paused"],
            "running": running, "processCheck": process_check,
            "recording": bool(runtime.get("recording")) if running else (False if running is False else None), "runtime": runtime,
            "sync": read_json(store.directory / "sync.json", {"sources": {}}),
            "pendingRequests": [p.stem for p in sorted((store.directory / "requests").glob("*.json"))]}


def request(store: CaptureStore, action: str, payload: dict | None = None, *, wait_seconds: float = 10) -> dict:
    request_id = uuid.uuid4().hex
    now = datetime.now(UTC)
    atomic_json_write(store.directory / "requests" / f"{request_id}.json", {
        "schemaVersion": 1, "id": request_id, "action": action, "createdAt": iso(now),
        "expiresAt": iso(now + timedelta(seconds=60)), "payload": payload or {},
    })
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        answer = read_json(store.directory / "responses" / f"{request_id}.json")
        if answer:
            return answer
        time.sleep(0.1)
    return {"status": "PENDING", "requestID": request_id, "message": "No app acknowledgement yet; the action is not confirmed"}


def app_path() -> Path:
    configured = os.environ.get("MYNOTES_CAPTURE_APP")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates += [Path(sys.executable).parent.parent.parent.parent.parent / "current/MyNotes Capture.app",
                   Path(sys.executable).parent.parent.parent / "MyNotes Capture.app",
                   Path(__file__).resolve().parents[2] / "capture/output/MyNotes Capture.app"]
    for candidate in candidates:
        if (candidate / "Contents/MacOS/MyNotesCapture").is_file():
            return candidate.absolute()
    raise CaptureError("MyNotes Capture.app is not installed; build or install the release package")


def set_enabled(store: CaptureStore, enabled: bool) -> dict:
    import plistlib
    if sys.platform != "darwin":
        raise CaptureError("The recording app requires macOS")
    active_path = runtime_home() / "active.json"
    active = read_json(active_path)
    if active and active["workspace"] != str(store.workspace.root):
        raise CaptureError("Disable Capture in its active workspace before switching workspaces")
    state = capture_status(store)
    if not enabled and state["running"] is not False and (state["runtime"].get("recording", False) or state["runtime"].get("exporting", False)):
        raise CaptureError("Stop and save the active recording before disabling Capture")
    plist_path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    if enabled:
        app = app_path()
        if active and state["running"] is not False:
            return {"status": "ENABLED", "alreadyRunning": state["running"]}
        config = store.config()
        if not store.config_path.exists() or not store.schedule_path.exists():
            raise CaptureError("Configure Capture and import or synchronize a calendar first")
        log_dir = runtime_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        for name in ("capture.out.log", "capture.err.log"):
            log = log_dir / name
            if log.exists() and log.stat().st_size > MAX_FEED_BYTES:
                os.replace(log, log.with_suffix(".log.1"))
        plist = {"Label": LABEL, "ProgramArguments": [str(app / "Contents/MacOS/MyNotesCapture"), "--workspace", str(store.workspace.root)],
                 "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 30,
                 "StandardOutPath": str(log_dir / "capture.out.log"), "StandardErrorPath": str(log_dir / "capture.err.log")}
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = plist_path.with_suffix(".tmp")
        temporary.write_bytes(plistlib.dumps(plist))
        os.replace(temporary, plist_path)
        subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
        with store.lock():
            config["enabled"] = True
            store.save_config(config)
        completed = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], capture_output=True, text=True)
        if completed.returncode:
            with store.lock():
                config["enabled"] = False
                store.save_config(config)
            raise CaptureError("LaunchAgent bootstrap failed: " + completed.stderr.strip())
        atomic_json_write(active_path, {"workspace": str(store.workspace.root)})
        return {"status": "ENABLED", "audioReadinessVerified": False}
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    with store.lock():
        config = store.config()
        config["enabled"] = False
        store.save_config(config)
    plist_path.unlink(missing_ok=True)
    active_path.unlink(missing_ok=True)
    return {"status": "DISABLED"}


def add_parser(subparsers) -> None:
    capture = subparsers.add_parser("capture", help="configure and control MyNotes Capture")
    commands = capture.add_subparsers(dest="capture_command", required=True)
    for name in ("configure", "import", "subscribe", "connect", "source", "schedule", "status", "dashboard", "add-lesson", "edit-lesson", "reset-lesson", "unsubscribe", "sync", "pending", "assign", "set-import-mode", "enable", "disable", "pause", "resume", "start", "stop", "test", "recover", "confirm-test"):
        command = commands.add_parser(name)
        command.add_argument("--workspace")
        command.add_argument("--json", action="store_true")
        if name in {"configure", "import", "subscribe", "connect", "add-lesson", "edit-lesson", "assign"}:
            command.add_argument("--file", required=name != "configure", help="private JSON input")
        if name == "confirm-test":
            command.add_argument("--audio", required=True)
            command.add_argument("--audible-confirmed", action="store_true", required=True)
        if name == "sync":
            command.add_argument("--if-due", action="store_true")
            command.add_argument("--source")
        if name == "set-import-mode":
            command.add_argument("--source", required=True)
            command.add_argument("--mode", choices=("mapped", "all_timed"), required=True)
        if name in {"edit-lesson", "reset-lesson"}:
            command.add_argument("--event", required=True)
        if name in {"unsubscribe", "source"}:
            command.add_argument("--source", required=True)
        if name == "start":
            command.add_argument("--course")
            command.add_argument("--lesson", type=int)
            command.add_argument("--unassigned", action="store_true")
            command.add_argument("--title")
            command.add_argument("--language")
            command.add_argument("--duration-minutes", type=int, required=True)
        if name == "recover":
            command.add_argument("--session", help="explicit attempt ID when multiple attempts exist")
        if name in {"start", "stop", "test", "recover"}:
            command.add_argument("--wait-seconds", type=float, default=10)


def run_cli(arguments) -> int:
    store = CaptureStore(Workspace.from_value(arguments.workspace))
    action = arguments.capture_command
    if action in {"dashboard", "add-lesson", "edit-lesson", "reset-lesson", "unsubscribe"}:
        from . import capture_dashboard
        if action == "dashboard": output = capture_dashboard.dashboard(store)
        elif action == "add-lesson": output = capture_dashboard.add_lesson(store, read_json(Path(arguments.file)))
        elif action == "edit-lesson": output = capture_dashboard.edit_lesson(store, arguments.event, read_json(Path(arguments.file)))
        elif action == "reset-lesson": output = capture_dashboard.reset_lesson(store, arguments.event)
        else: output = capture_dashboard.unsubscribe(store, arguments.source)
    elif action in {"pending", "assign"}:
        from . import capture_associations
        output = capture_associations.pending(store) if action == "pending" else capture_associations.assign(store, read_json(Path(arguments.file)))
    elif action == "confirm-test":
        from .onboarding import confirm_capture_audio
        output = {"status": "VERIFIED", **confirm_capture_audio(store.workspace, Path(arguments.audio), audible=arguments.audible_confirmed)}
    elif action == "configure":
        output = configure(store, read_json(Path(arguments.file)) if arguments.file else {})
    elif action == "import":
        output = import_schedule(store, read_json(Path(arguments.file)))
    elif action == "connect":
        output = connect(store, read_json(Path(arguments.file)))
    elif action == "source":
        source = next((s for s in store.config()["sources"] if s["sourceID"] == arguments.source), None)
        if source is None:
            raise CaptureError("Calendar source not found")
        output = {"status": "SOURCE", "source": source}
    elif action == "subscribe":
        output = subscribe(store, read_json(Path(arguments.file)))
    elif action == "set-import-mode":
        source = next((s for s in store.config()["sources"] if s["sourceID"] == arguments.source), None)
        if source is None:
            raise CaptureError("Calendar source not found")
        output = subscribe(store, {**source, "importMode": arguments.mode})
    elif action == "schedule":
        output = store.schedule()
    elif action == "status":
        output = capture_status(store)
    elif action == "sync":
        output = sync(store, if_due=arguments.if_due, source_id=arguments.source)
    elif action in {"enable", "disable"}:
        output = set_enabled(store, action == "enable")
    elif action in {"pause", "resume"}:
        with store.lock():
            config = store.config()
            config["paused"] = action == "pause"
            store.save_config(config)
        output = {"status": "PAUSED" if action == "pause" else "RESUMED", "activeRecordingUnaffected": True}
    else:
        payload = {}
        if action == "start":
            courses = {c["code"] for c in store.workspace.configuration_store().load()["courses"]}
            now = datetime.now(UTC)
            raw = {"courseCode": arguments.course, "lessonNumber": arguments.lesson,
                   "startsAt": now, "endsAt": now + timedelta(minutes=arguments.duration_minutes)}
            if arguments.title is not None:
                raw["title"] = arguments.title
            if arguments.language is not None:
                raw["transcriptionLanguage"] = arguments.language
            if arguments.unassigned:
                require_standalone(store)
                raw["recordingID"] = uuid.uuid4().hex
            event = normalize_event(raw, courses, "manual")
            event["recordAt"], event["stopAt"] = event["startsAt"], event["endsAt"]
            payload = {"event": event}
        elif action == "recover" and arguments.session:
            payload = {"sessionID": arguments.session}
        if not 0 <= arguments.wait_seconds <= 30:
            raise CaptureError("wait-seconds must be between zero and thirty")
        output = request(store, action, payload, wait_seconds=arguments.wait_seconds)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 2 if output.get("status") in {"FAILED", "REJECTED", "SYNC_FAILED", "INTERRUPTED"} else 0


def standalone_supported(store) -> bool:
    if ("standalone-recordings-v1" in read_json(store.runtime_path, {}).get("capabilities", [])
            and capture_status(store)["running"] is True):
        return True
    import plistlib
    info = Path(sys.executable).parent.parent.parent / "MyNotes Capture.app/Contents/Info.plist"
    try:
        return "standalone-recordings-v1" in plistlib.loads(info.read_bytes()).get("MyNotesCaptureCapabilities", [])
    except (OSError, ValueError):
        return False


def require_standalone(store):
    if not standalone_supported(store):
        raise CaptureError("Aggiorna il registratore Capture per usare registrazioni senza corso. I calendari esistenti sono conservati.")


def calendar_recordings_supported(store) -> bool:
    runtime = read_json(store.runtime_path, {})
    if capture_status(store)["running"] is not False:
        return "calendar-recordings-v1" in runtime.get("capabilities", [])
    import plistlib
    info = Path(sys.executable).parent.parent.parent / "MyNotes Capture.app/Contents/Info.plist"
    try:
        return "calendar-recordings-v1" in plistlib.loads(info.read_bytes()).get("MyNotesCaptureCapabilities", [])
    except (OSError, ValueError):
        return False


def require_calendar_recordings(store):
    if not calendar_recordings_supported(store):
        raise CaptureError("Aggiorna il registratore Capture prima di collegare un calendario completo. I calendari esistenti sono conservati.")
