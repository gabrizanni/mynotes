"""Read-only lecture attempt accounting; counters are reports, never invoices."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any

from .utils import atomic_json_write, now_compact_utc
from .workspace import Workspace

ATTEMPT_FILE = re.compile(r"^.+-[0-9a-f]{12}\.json$")
# These exact provider counters are additive across independent calls. They
# remain separate: input/cache/total may overlap and are never added together.
TOKEN_COUNTERS = {
    "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
    "cache_read_input_tokens", "cache_creation_input_tokens", "prompt_tokens",
    "completion_tokens", "promptTokenCount", "candidatesTokenCount",
    "totalTokenCount", "cachedContentTokenCount", "thinking_tokens", "cache_read_tokens",
}


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    def invalid(value):
        raise ValueError(f"Invalid JSON constant {value}")
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError("Record is not an object")
    return value


def record_reuse(workspace: Workspace, lecture_id: str, *, kind: str, phase: str | None = None,
                 checkpoint: Path | None = None, attempt_path: str | None = None) -> None:
    """Record a local reuse decision, not another provider invocation."""
    payload = {"event_id": uuid.uuid4().hex, "lecture_id": lecture_id, "kind": kind,
               "phase": phase, "checkpoint": str(checkpoint) if checkpoint else None,
               "attempt_path": attempt_path, "recorded_at": now_compact_utc(),
               "provider_invoked": False}
    path = workspace.runs_dir / lecture_id / "usage-reuse" / f"{payload['event_id']}.json"
    atomic_json_write(path, {"schema_version": 1, "payload": payload, "sha256": _fingerprint(payload)})


def lecture_usage(workspace: Workspace, lecture_id: str) -> dict[str, Any]:
    """Inspect immutable raw attempts and checkpoints without opening SQLite."""
    lecture_id = lecture_id.upper()
    if not re.fullmatch(r"[A-Z0-9]+-L[0-9]{2}", lecture_id):
        raise ValueError("Use a lecture id such as COURSE-L01")
    run = workspace.runs_dir / lecture_id
    warnings: list[dict[str, str]] = []
    attempts: list[dict[str, Any]] = []
    providers: dict[str, Any] = {}
    cumulative = {}
    records: dict[str, tuple[dict[str, Any], Path, str]] = {}
    conflicts: set[str] = set()
    def warn(path: Path, reason: str):
        warnings.append({"path": str(path), "reason": reason})
    def local(path: Path):
        if path.is_symlink() or not path.resolve().is_relative_to(run.resolve()):
            raise ValueError("Record is outside the lecture directory")
    for path in sorted((run / "provider-attempts").glob("*.json")):
        if not ATTEMPT_FILE.fullmatch(path.name):
            continue  # schemas and final responses are not invocation records
        try:
            local(path); data = _read(path)
            provider = data["requested"]["provider"]
            if data["lecture_id"] != lecture_id or not isinstance(provider, str) or not provider or not isinstance(data["phase"], str) or not data["phase"] or path.stem.rsplit("-", 1)[0] != data["phase"]:
                raise ValueError("Attempt identity is invalid")
            if not isinstance(data["stdout"], str) or not isinstance(data["stderr"], str) or "returncode" not in data or "error" not in data:
                raise ValueError("Attempt transport fields are missing")
            if data["error"] is not None and not isinstance(data["error"], str):
                raise ValueError("Attempt error field is invalid")
            if data["returncode"] is not None and type(data["returncode"]) is not int:
                raise ValueError("Attempt return code is invalid")
            digest = _fingerprint(data)
            identifier = data.get("attempt_id") or "legacy:" + digest
            if not isinstance(identifier, str):
                raise ValueError("Attempt id is invalid")
            if identifier in records:
                if records[identifier][2] != digest:
                    conflicts.add(identifier); warn(path, "Conflicting duplicate attempt id; neither version is totaled")
                else:
                    warn(path, "Duplicate immutable attempt record; counted once")
                continue
            records[identifier] = (data, path, digest)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
            warn(path, f"Unreadable/invalid attempt; usage unknown ({type(error).__name__})")
    for identifier, (data, path, _) in records.items():
        if identifier in conflicts:
            continue
        provider = data["requested"]["provider"]
        raw = _attempt_usage(data)
        if raw is not None and not isinstance(raw, dict):
            warn(path, "Invalid usage object; counters unknown"); raw = None
        started = data.get("invocation_started")
        if type(started) is not bool:
            started = True if data["returncode"] is not None else None
        outcome = "local_rejected" if started is False else "failed" if data["error"] or data["returncode"] != 0 else "returned"
        attempts.append({"attempt_id": identifier, "path": str(path), "provider": provider,
                         "phase": data["phase"], "outcome": outcome,
                         "duration_seconds": data.get("duration_seconds"),
                         "invocation_started": started, "reported_usage": raw})
        group = providers.setdefault(provider, {"attempts": 0, "returned": 0, "failed": 0, "local_rejected": 0,
                                               "reported_totals": {}, "counter_reporting_attempts": {}, "unknown_usage_attempts": 0})
        group["attempts"] += 1; group[outcome] += 1
        if not raw:
            group["unknown_usage_attempts"] += 1
            if started is not False:
                warn(path, "No reported usage counters; unknown, not zero")
            continue
        session, counters = _session_snapshot(data, raw)
        attempts[-1]["conversation_id"] = session
        if "session_cumulative" in raw and session is None:
            warn(path, "Cumulative counters have no unique conversation; excluded from totals")
            continue
        if session:
            bucket = cumulative.setdefault((provider, session), {})
        if set(counters) - TOKEN_COUNTERS:
            warn(path, "Non-additive or unfamiliar counters are retained per attempt, not totaled")
        for name, value in counters.items():
            if name not in TOKEN_COUNTERS:
                # Keep future/nested/cost counters verbatim per attempt rather
                # than assuming units or inventing an additive cost metric.
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or (isinstance(value, float) and not math.isfinite(value)) or value < 0:
                warn(path, f"Invalid {name} counter; excluded from subtotal")
                continue
            if session:
                bucket[name] = max(bucket.get(name, 0), value)
                continue
            try:
                subtotal = group["reported_totals"].get(name, 0) + value
                if isinstance(subtotal, float) and not math.isfinite(subtotal):
                    raise OverflowError
            except OverflowError:
                warn(path, f"{name} subtotal overflow; latest value retained only in raw usage")
                continue
            group["reported_totals"][name] = subtotal
            group["counter_reporting_attempts"][name] = group["counter_reporting_attempts"].get(name, 0) + 1
    for (provider, session), counters in cumulative.items():
        group = providers[provider]
        group.setdefault("conversations", []).append({"conversation_id": session, "reported_cumulative": counters})
        for name, value in counters.items():
            subtotal = group["reported_totals"].get(name, 0) + value
            if not math.isfinite(subtotal):
                warn(run, f"Non-finite conversation subtotal {name}")
                continue
            group["reported_totals"][name] = subtotal
            group["counter_reporting_attempts"][name] = group["counter_reporting_attempts"].get(name, 0) + 1
    for provider, group in providers.items():
        relevant = [a for a in attempts if a["provider"] == provider and a["outcome"] != "local_rejected"]
        expected = sum(not a.get("conversation_id") for a in relevant) + len({a["conversation_id"] for a in relevant if a.get("conversation_id")})
        group["expected_reporting_units"] = expected
        group["counter_reporting_units"] = dict(group["counter_reporting_attempts"])
        if any(count < expected for count in group["counter_reporting_attempts"].values()):
            warn(run, f"{provider}: some counter subtotals cover only part of the attempts; inspect counter_reporting_attempts")
    phases = []
    for path in sorted((run / "generation-checkpoints").glob("*/*.json")):
        if path.stem.startswith("returned-"):
            continue  # Preserved transport has not passed local validation.
        try:
            local(path); data = _read(path); payload = data["payload"]
            if data.get("schema_version") != 1 or data["sha256"] != _fingerprint(payload) or not isinstance(payload.get("previous_hash"), str):
                raise ValueError("Checkpoint fingerprint mismatch")
            response = payload["response"]
            if not isinstance(response, dict) or not isinstance(response.get("provider"), str) or not isinstance(response.get("notes"), dict) or not isinstance(response.get("usage"), dict):
                raise ValueError("Invalid saved phase response")
            phases.append({"phase": path.stem, "input_hash": path.parent.name,
                           "provider": response["provider"], "reported_usage": response["usage"],
                           "attempt_path": response.get("attempt_path"), "path": str(path)})
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
            warn(path, f"Saved phase is invalid; excluded ({type(error).__name__})")
    reuse = {"phase_events": 0, "candidate_events": 0, "historical_counts_known": False, "events": [],
             "scope": "Recorded local reuse events only; older historical counts unknown. No provider invocation is added."}
    seen = set()
    for path in sorted((run / "usage-reuse").glob("*.json")):
        try:
            local(path); data = _read(path); payload = data["payload"]
            if data.get("schema_version") != 1 or data["sha256"] != _fingerprint(payload) or payload["lecture_id"] != lecture_id or payload["provider_invoked"] is not False or payload["kind"] not in {"phase", "candidate"}:
                raise ValueError("Invalid reuse event")
            if payload["event_id"] in seen:
                warn(path, "Duplicate reuse event; counted once"); continue
            seen.add(payload["event_id"]); reuse[payload["kind"] + "_events"] += 1
            reuse["events"].append(payload)
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
            warn(path, f"Reuse event is invalid; excluded ({type(error).__name__})")
    return {"lecture_id": lecture_id, "workspace": str(workspace.root), "attempts": attempts, "providers": providers,
            "successful_saved_phases": phases, "reuse": reuse, "partial": bool(warnings), "warnings": warnings,
            "scope": "Reported counters across unique local attempts, including failures. Cumulative writing uses the largest reported snapshot per conversation. Metrics are separate and may overlap; no derived token total, cost estimate or billing claim. Saved phases/reuse are not added to attempt totals."}


def _attempt_usage(data):
    from .providers import reported_usage
    usage = data.get("reported_usage")
    if usage is None:
        usage = reported_usage(data["requested"]["provider"], data.get("stdout", ""), data.get("envelope"))
    if usage is None and (data.get("envelope") or {}).get("transport") == "agy_parts_v1":
        # Older local-rejection records have no summary envelope usage, but
        # retain the original per-turn result events. Never edit those records.
        for line in reversed(data.get("stdout", "").splitlines()):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            result = event.get("result") if isinstance(event, dict) else None
            if isinstance(result, dict) and isinstance(result.get("usage"), dict) and result["usage"]:
                return {"session_cumulative": result["usage"]}
    return usage


def _session_snapshot(data, usage):
    """Only explicitly cumulative writing transports share a counter bucket."""
    if not isinstance(usage, dict):
        return None, usage
    nested = usage.get("session_cumulative")
    if not isinstance(nested, dict):
        return None, usage
    identifiers = set()
    for line in data.get("stdout", "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        envelope = event.get("result", {}) if event.get("event") == "result" else event
        if isinstance(envelope, dict) and envelope.get("conversation_id"):
            identifiers.add(envelope["conversation_id"])
    return next(iter(identifiers)) if len(identifiers) == 1 else None, nested


def batch_usage(workspace: Workspace, batch_id: str) -> dict[str, Any]:
    """Count shared immutable attempts once across all versions of one batch."""
    if not re.fullmatch(r"[A-Z0-9]+-[0-9a-f]{12}", batch_id):
        raise ValueError("Use a batch ID returned by generate-batch")
    root = workspace.runs_dir / "_batches" / batch_id
    warnings, records, conflicts = [], {}, set()
    for path in sorted(root.rglob("provider-attempts/*.json")):
        if not ATTEMPT_FILE.fullmatch(path.name):
            continue
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Attempt is outside the batch")
            data = _read(path)
            if (not isinstance(data.get("attempt_id"), str) or not data["attempt_id"]
                    or data.get("phase") != path.stem.rsplit("-", 1)[0]
                    or not isinstance(data.get("requested"), dict)
                    or data["requested"].get("provider") not in {"codex", "agy", "claude"}
                    or not isinstance(data.get("lecture_id"), str)
                    or not data["lecture_id"].startswith(batch_id.split("-", 1)[0] + "-L")):
                raise ValueError("Invalid batch attempt identity")
            digest = _fingerprint(data)
            identifier = data["attempt_id"]
            if identifier in records:
                if records[identifier][2] != digest:
                    conflicts.add(identifier)
                    warnings.append({"path": str(path), "reason": "Conflicting shared attempt id; excluded from totals"})
                continue
            records[identifier] = (data, path, digest)
        except (OSError, ValueError, KeyError, TypeError) as error:
            warnings.append({"path": str(path), "reason": f"Invalid shared attempt; consumption unknown ({type(error).__name__})"})
    from .providers import reported_usage
    attempts, providers, cumulative = [], {}, {}
    for identifier, (data, path, _) in records.items():
        if identifier in conflicts:
            continue
        usage = _attempt_usage(data)
        if not isinstance(usage, dict):
            usage = None
        started = data.get("invocation_started")
        outcome = "local_rejected" if started is False else "failed" if data.get("error") or data.get("returncode") != 0 else "returned"
        attempt = {"attempt_id": identifier, "path": str(path), "provider": data["requested"]["provider"],
                   "phase": data["phase"], "outcome": outcome, "reported_usage": usage,
                   "invocation_started": started, "duration_seconds": data.get("duration_seconds")}
        attempts.append(attempt)
        totals = providers.setdefault(attempt["provider"], {"attempts": 0, "returned": 0, "failed": 0,
            "local_rejected": 0, "reported_totals": {}, "counter_reporting_attempts": {}, "unknown_usage_attempts": 0})
        totals["attempts"] += 1; totals[outcome] += 1
        if not usage:
            totals["unknown_usage_attempts"] += 1
            if started is not False:
                warnings.append({"path": str(path), "reason": "Shared attempt usage is unknown, not zero"})
            continue
        session, counters = _session_snapshot(data, usage)
        attempt["conversation_id"] = session
        if "session_cumulative" in usage and session is None:
            warnings.append({"path": str(path), "reason": "Cumulative counters have no unique conversation; excluded from totals"})
            continue
        if session:
            bucket = cumulative.setdefault((attempt["provider"], session), {})
        for name, value in counters.items():
            if name not in TOKEN_COUNTERS:
                warnings.append({"path": str(path), "reason": "Unfamiliar/non-additive counters retained without totals"})
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or not math.isfinite(value):
                warnings.append({"path": str(path), "reason": f"Invalid shared counter {name}"})
                continue
            if session:
                # The maximum is a reported cumulative snapshot, not a sum of
                # resumed processes. Preserve every raw snapshot above.
                bucket[name] = max(bucket.get(name, 0), value)
                continue
            combined = totals["reported_totals"].get(name, 0) + value
            if not math.isfinite(combined):
                warnings.append({"path": str(path), "reason": f"Non-finite shared counter total {name}"})
                continue
            totals["reported_totals"][name] = combined
            totals["counter_reporting_attempts"][name] = totals["counter_reporting_attempts"].get(name, 0) + 1
    for (provider, session), counters in cumulative.items():
        totals = providers[provider]
        totals.setdefault("conversations", []).append({"conversation_id": session, "reported_cumulative": counters})
        for name, value in counters.items():
            combined = totals["reported_totals"].get(name, 0) + value
            if not math.isfinite(combined):
                warnings.append({"path": str(root), "reason": f"Non-finite conversation subtotal {name}"})
                continue
            totals["reported_totals"][name] = combined
            totals["counter_reporting_attempts"][name] = totals["counter_reporting_attempts"].get(name, 0) + 1
    for provider, totals in providers.items():
        provider_attempts = [a for a in attempts if a["provider"] == provider and a["outcome"] != "local_rejected"]
        units = sum(not a.get("conversation_id") for a in provider_attempts) + len({a["conversation_id"] for a in provider_attempts if a.get("conversation_id")})
        totals["expected_reporting_units"] = units
        totals["counter_reporting_units"] = dict(totals["counter_reporting_attempts"])
        if any(count < units for count in totals["counter_reporting_attempts"].values()):
            warnings.append({"path": str(root), "reason": f"{provider}: some counters cover only part of shared attempts"})
    return {"batch_id": batch_id, "attempts": attempts, "providers": providers, "partial": bool(warnings), "warnings": warnings,
            "scope": "All unique immutable shared attempts across this batch's versions, including failures. Cumulative writing counters use the largest reported snapshot per conversation, never their sum; other counters are counted per invocation. Raw historical attempts remain separate. Counter units stay separate; no inferred billed cost."}
