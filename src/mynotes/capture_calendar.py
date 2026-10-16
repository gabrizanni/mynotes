"""Local classification and complete-calendar ingestion; no model calls."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import datetime

from .capture import CaptureError, iso, normalize_event, timestamp


def title_key(value):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold(), re.UNICODE))


def contains(title, phrase):
    haystack, needle = title_key(title), title_key(phrase)
    return bool(needle) and f" {needle} " in f" {haystack} "


def classify(title, source, courses, details):
    explicit = {m["courseCode"] for m in source.get("mappings", []) if contains(title, m["match"])}
    candidates = explicit or {c["code"] for c in (details or [{"code": c, "name": c} for c in courses])
                              if any(contains(title, x) for x in [c["code"], c["name"], *c.get("aliases", [])])}
    ordered = sorted(candidates)
    return (ordered[0] if len(ordered) == 1 else None), ordered


def all_timed_events(data, source, old, courses, *, course_details=None, report=None):
    import icalendar
    import recurring_ical_events

    calendar = icalendar.Calendar.from_ical(data)
    if calendar.name != "VCALENDAR" or calendar.errors:
        raise CaptureError("Response is not a valid calendar")
    summary = {"imported": 0, "recognizedCourses": 0, "pending": 0, "excluded": 0, "issues": []}
    valid = []
    cancelled_uids, cancelled_instances = set(), set()
    recurring_uids = {str(c.get("UID", "")) for c in calendar.subcomponents if "RRULE" in c or "RDATE" in c or "RECURRENCE-ID" in c}
    for component in calendar.subcomponents:
        if component.name != "VEVENT":
            valid.append(component)
            continue
        uid, title = str(component.get("UID", "")), str(component.get("SUMMARY", ""))
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            if "RECURRENCE-ID" in component:
                try:
                    cancelled_instances.add((uid, iso(timestamp(component.decoded("RECURRENCE-ID"), source["timezone"]))))
                except (ValueError, TypeError):
                    pass
            else:
                cancelled_uids.add(uid)
        start = component.decoded("DTSTART", None)
        if component.errors or not uid or not isinstance(start, datetime):
            summary["excluded"] += 1
            summary["issues"].append({"uid": uid, "title": title, "reason": "Evento di giornata intera" if start is not None and not isinstance(start, datetime) else "UID o inizio non valido"})
            continue
        valid.append(component)
    calendar.subcomponents = valid
    start, end = timestamp(source["periodStart"]), timestamp(source["periodEnd"])
    expanded = recurring_ical_events.of(calendar).between(start, end)
    previous = {e["id"]: e for e in old if e["sourceID"] == source["sourceID"]}
    events, recognized = [], set()
    for component in expanded:
        uid, title = str(component.get("UID", "")), str(component.get("SUMMARY", "")).strip()
        dtstart, dtend = component.decoded("DTSTART", None), component.decoded("DTEND", None)
        original = component.decoded("RECURRENCE-ID", dtstart)
        identity = None
        try:
            occurrence = iso(timestamp(original, source["timezone"])) if uid in recurring_uids else "single"
            identity = source["sourceID"] + ":" + uid + ":" + occurrence
            # An absent DTEND must not acquire a fabricated duration from the recurrence library.
            if not isinstance(dtend, datetime) or dtend <= dtstart:
                raise CaptureError("Orario di fine mancante o non valido")
            prior = previous.get(identity)
            course, candidates = classify(title, source, courses, course_details)
            detail = next((c for c in (course_details or []) if c['code'] == course), None)
            number = official_number(title, detail)
            if prior and prior.get("courseCode") is not None:
                course, number = prior["courseCode"], prior["lessonNumber"]
            raw = {"id": identity, "sourceID": source["sourceID"], "courseCode": course, "lessonNumber": number,
                   "title": title or "Lezione senza titolo", "startsAt": dtstart, "endsAt": dtend, "timezone": source["timezone"],
                   "associationState": "resolved" if course else "ambiguous" if candidates else "pending", "candidateCourses": candidates,
                   "calendarUID": uid, "originalOccurrence": iso(timestamp(original, source["timezone"]))}
            # Legacy events keep their original recording destination on opt-in conversion.
            if not prior or prior.get("recordingID"):
                raw["recordingID"] = (prior or {}).get("recordingID") or hashlib.sha256(json.dumps([source["sourceID"], uid, occurrence], ensure_ascii=False).encode()).hexdigest()[:32]
            if str(component.get("STATUS", "")).upper() == "CANCELLED" or uid in cancelled_uids or (uid, raw["originalOccurrence"]) in cancelled_instances:
                raw.update(eligibility="CANCELLED", reason="Calendar cancellation")
            event = normalize_event(raw, courses, source["sourceID"])
            events.append(event)
            summary["imported"] += 1
            if course:
                recognized.add(course)
            else:
                summary["pending"] += 1
        except (ValueError, TypeError, KeyError) as error:
            summary["excluded"] += 1
            summary["issues"].append({"uid": uid, "title": title, "reason": "Orario o dati dell’evento non validi"})
            if identity in previous:
                events.append({**previous[identity], "eligibility": "NEEDS_REVIEW", "reason": "Calendar event has invalid times"})
    current = {e["id"] for e in events}
    for identity, event in previous.items():
        if identity in current:
            continue
        cancelled = event.get("calendarUID") in cancelled_uids or (event.get("calendarUID"), event.get("originalOccurrence")) in cancelled_instances
        events.append({**event, "eligibility": "CANCELLED" if cancelled else "NEEDS_REVIEW",
                       "reason": "Calendar cancellation" if cancelled else "Event disappeared from calendar; verify before recording"})
    summary["recognizedCourses"] = len(recognized)
    if report is not None:
        report.update(summary)
    return events


def official_number(title, course=None):
    explicit = re.search(r"\b(?:L|Lesson\s*|Lecture\s*|Lezione\s*|Session\s*)(\d{1,3})\b", title, re.I)
    if explicit:
        number = int(explicit[1])
        return number if 1 <= number <= 999 else None
    suffix = re.fullmatch(r"(.+?)\s+(\d{1,3})", title.strip())
    if suffix and course and any(title_key(suffix[1]) == title_key(x) for x in [course['code'], course['name'], *course.get('aliases', [])]):
        number = int(suffix[2])
        return number if 1 <= number <= 999 else None
    return None
