"""Local classification and complete-calendar ingestion; no model calls."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta

from .capture import CaptureError, iso, normalize_event, timestamp


def title_key(value):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold(), re.UNICODE))


def contains(title, phrase):
    haystack, needle = title_key(title), title_key(phrase)
    return bool(needle) and f" {needle} " in f" {haystack} "


def classify(title, source, courses, details):
    explicit = {m["courseCode"] for m in source.get("mappings", []) if m["courseCode"] in courses and contains(title, m["match"])}
    candidates = explicit or {c["code"] for c in (details or [{"code": c, "name": c} for c in courses])
                              if any(contains(title, x) for x in [c["code"], c["name"], *c.get("aliases", [])])}
    ordered = sorted(candidates & courses)
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
    invalid_uids = {}
    invalid_instances = {}
    recurring_uids = {str(c.get("UID", "")) for c in calendar.subcomponents if "RRULE" in c or "RDATE" in c or "RECURRENCE-ID" in c}

    def issue(uid, title, code, reason, component=None):
        # A cancellation identifies what to stop through UID/RECURRENCE-ID;
        # RFC cancellation tombstones need not repeat start/end properties.
        if uid and component is not None and str(component.get("STATUS", "")).upper() == "CANCELLED":
            if "RECURRENCE-ID" not in component and uid in cancelled_uids:
                return
            try:
                occurrence = iso(timestamp(component.decoded("RECURRENCE-ID"), source["timezone"]))
                if (uid, occurrence) in cancelled_instances:
                    return
            except (ValueError, TypeError, KeyError):
                pass
        summary["excluded"] += 1
        summary["issues"].append({"uid": uid, "title": title, "code": code, "reason": reason})
        if uid:
            if component is not None and "RECURRENCE-ID" in component:
                try:
                    occurrence = iso(timestamp(component.decoded("RECURRENCE-ID"), source["timezone"]))
                    invalid_instances[(uid, occurrence)] = (code, reason)
                    return
                except (ValueError, TypeError, KeyError):
                    pass
            invalid_uids[uid] = (code, reason)

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
        dtstart = component.decoded("DTSTART", None)
        if dtstart is not None and not isinstance(dtstart, datetime):
            issue(uid, title, "ALL_DAY_EVENT", "Evento di giornata intera: nessun orario di registrazione", component)
            continue
        if not uid or dtstart is None:
            issue(uid, title, "INVALID_START" if uid else "INVALID_EVENT", "UID o inizio non valido", component)
            continue
        if component.errors:
            code = "INVALID_RECURRENCE" if any(key in component for key in ("RRULE", "RDATE", "RECURRENCE-ID")) else "INVALID_EVENT"
            issue(uid, title, code, "Proprietà dell’evento non valide", component)
            continue
        if "DTEND" not in component and "DURATION" not in component:
            issue(uid, title, "MISSING_END", "Orario di fine mancante", component)
            continue
        # Validate source values before recurrence expansion. The library may
        # otherwise repair a negative window into an apparently valid event.
        try:
            window_start = timestamp(dtstart, source["timezone"])
        except (ValueError, TypeError, KeyError):
            issue(uid, title, "INVALID_START", "Orario di inizio non valido", component)
            continue
        try:
            if "DTEND" in component:
                window_end = timestamp(component.decoded("DTEND"), source["timezone"])
            else:
                duration = component.decoded("DURATION")
                if not isinstance(duration, timedelta):
                    raise CaptureError("Invalid duration")
                window_end = window_start + duration
            if window_end <= window_start:
                raise CaptureError("Invalid end")
        except (ValueError, TypeError, KeyError):
            issue(uid, title, "INVALID_END", "Orario di fine non valido", component)
            continue
        if window_end - window_start > timedelta(hours=12):
            issue(uid, title, "INVALID_DURATION", "La registrazione supera il limite di dodici ore", component)
            continue
        valid.append(component)
    calendar.subcomponents = valid
    start, end = timestamp(source["periodStart"]), timestamp(source["periodEnd"])
    # Expand a UID (master plus overrides) independently: one broken recurrence
    # must not prevent unrelated valid lectures from reaching the recorder.
    expanded = []
    groups = {}
    for component in valid:
        if component.name == "VEVENT":
            groups.setdefault(str(component.get("UID", "")), []).append(component)
    for uid, components in groups.items():
        group = copy.copy(calendar)
        group.subcomponents = copy.deepcopy([c for c in valid if c.name != "VEVENT"] + components)
        try:
            expanded.extend(recurring_ical_events.of(group).between(start, end))
        except (ValueError, TypeError, KeyError, OverflowError):
            issue(uid, str(components[0].get("SUMMARY", "")), "INVALID_RECURRENCE", "Ricorrenza del calendario non valida")
    previous = {e["id"]: e for e in old if e["sourceID"] == source["sourceID"]}
    events, recognized = [], set()
    for component in expanded:
        uid, title = str(component.get("UID", "")), str(component.get("SUMMARY", "")).strip()
        dtstart, dtend = component.decoded("DTSTART", None), component.decoded("DTEND", None)
        original = component.decoded("RECURRENCE-ID", dtstart)
        identity, code, reason = None, "INVALID_EVENT", "Dati dell’evento non validi"
        try:
            occurrence = iso(timestamp(original, source["timezone"])) if uid in recurring_uids else "single"
            identity = source["sourceID"] + ":" + uid + ":" + occurrence
            is_cancelled = uid in cancelled_uids or (uid, iso(timestamp(original, source["timezone"]))) in cancelled_instances
            blocked = invalid_instances.get((uid, iso(timestamp(original, source["timezone"])))) or invalid_uids.get(uid)
            if blocked and not is_cancelled:
                # Removing an invalid override must not restore the master's
                # old occurrence as a new, apparently usable lecture.
                if identity in previous:
                    code, reason = blocked
                    events.append({**previous[identity], "eligibility": "NEEDS_REVIEW", "reason": reason, "reasonCode": code})
                continue
            code, reason = "INVALID_END", "Orario di fine non valido"
            if not isinstance(dtend, datetime):
                raise CaptureError(reason)
            window_start, window_end = timestamp(dtstart, source["timezone"]), timestamp(dtend, source["timezone"])
            if window_end <= window_start:
                raise CaptureError(reason)
            code, reason = "INVALID_DURATION", "La registrazione supera il limite di dodici ore"
            if window_end - window_start > timedelta(hours=12):
                raise CaptureError(reason)
            prior = previous.get(identity)
            course, candidates = classify(title, source, courses, course_details)
            detail = next((c for c in (course_details or []) if c['code'] == course), None)
            number = official_number(title, detail)
            if prior and prior.get("courseCode") is not None:
                # Independent identities are safe to reclassify. Explicit links
                # live in associations.json and are applied after ingestion.
                # Legacy course/lesson identities are recording destinations.
                if not prior.get("recordingID"):
                    course, number = prior["courseCode"], prior["lessonNumber"]
                elif prior["courseCode"] == course and prior.get("lessonNumber") is not None:
                    number = prior["lessonNumber"]
            raw = {"id": identity, "sourceID": source["sourceID"], "courseCode": course, "lessonNumber": number,
                   "title": title or "Lezione senza titolo", "startsAt": dtstart, "endsAt": dtend, "timezone": source["timezone"],
                   "associationState": "resolved" if course else "ambiguous" if candidates else "pending", "candidateCourses": candidates,
                   "calendarUID": uid, "originalOccurrence": iso(timestamp(original, source["timezone"]))}
            if not prior or prior.get("recordingID"):
                raw["recordingID"] = (prior or {}).get("recordingID") or hashlib.sha256(json.dumps([source["sourceID"], uid, occurrence], ensure_ascii=False).encode()).hexdigest()[:32]
            if str(component.get("STATUS", "")).upper() == "CANCELLED" or uid in cancelled_uids or (uid, raw["originalOccurrence"]) in cancelled_instances:
                raw.update(eligibility="CANCELLED", reason="Calendar cancellation", reasonCode="CALENDAR_CANCELLED")
            code, reason = ("UNKNOWN_COURSE", "Il corso associato non è più disponibile") if course is not None and course not in courses else ("INVALID_EVENT", "Dati dell’evento non validi")
            event = normalize_event(raw, courses, source["sourceID"])
            events.append(event)
            summary["imported"] += 1
            if course:
                recognized.add(course)
            else:
                summary["pending"] += 1
        except (ValueError, TypeError, KeyError):
            issue(uid, title, code, reason, component)
            if identity in previous:
                events.append({**previous[identity], "eligibility": "NEEDS_REVIEW", "reason": reason, "reasonCode": code})
    current = {e["id"] for e in events}
    for identity, event in previous.items():
        if identity in current:
            continue
        cancelled = event.get("calendarUID") in cancelled_uids or (event.get("calendarUID"), event.get("originalOccurrence")) in cancelled_instances
        if cancelled or event.get("eligibility") == "CANCELLED":
            events.append({**event, "eligibility": "CANCELLED", "reason": "Calendar cancellation", "reasonCode": "CALENDAR_CANCELLED"})
        elif (event.get("calendarUID"), event.get("originalOccurrence")) in invalid_instances or event.get("calendarUID") in invalid_uids:
            code, reason = invalid_instances.get((event.get("calendarUID"), event.get("originalOccurrence"))) or invalid_uids[event["calendarUID"]]
            events.append({**event, "eligibility": "NEEDS_REVIEW", "reason": reason, "reasonCode": code})
        else:
            events.append({**event, "eligibility": "NEEDS_REVIEW", "reason": "Event disappeared from calendar; verify before recording", "reasonCode": "EVENT_DISAPPEARED"})
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
