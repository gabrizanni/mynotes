"""Read-only model discovery and shared chat/note selection defaults.

Catalog membership describes what the signed-in CLI reports, never a generic
subscription table or inferred entitlement. Discovery does not send a prompt or initialize a MyNotes workspace.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .configuration import ConfigurationError, SUPPORTED_PROVIDERS

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,199}")
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
AGY_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Gemini identifiers were verified with the installed Agy `models` command;
# Terra is from Codex's local catalog. Labels are the product's exact presets.
_PRESET_DATA = json.loads((Path(__file__).parent / "resources" / "model-presets.json").read_text())
PRESETS = tuple(_PRESET_DATA["presets"])
DEFAULT_PRESET = _PRESET_DATA["defaults"]


def valid_model(value: str) -> bool:
    return isinstance(value, str) and (value == "" or IDENTIFIER.fullmatch(value) is not None)


def preset_choice(provider: str, preset: str) -> dict[str, Any]:
    for item in PRESETS:
        if item["id"] == preset and item["provider"] == provider:
            return dict(item)
    raise ConfigurationError("Choose a preset belonging to the selected agent")


def resolve_provider_options(configuration: dict[str, Any], provider: str) -> tuple[str, str]:
    """Apply product defaults only where there is no saved model choice.

    An explicit empty model means the user selected the provider default. Old
    nonempty model choices keep provider-default effort when no override exists.
    """
    options = configuration.get("providers", {}).get("configured", {}).get(provider, {})
    if "model" in options:
        return str(options["model"]).strip(), str(options.get("effort", "")).strip()
    default = DEFAULT_PRESET.get(provider)
    if default:
        item = preset_choice(provider, default)
        return item["model"], str(options.get("effort", item["default_effort"])).strip()
    return "", str(options.get("effort", "")).strip()


def _safe_label(value: Any, fallback: str) -> str:
    from .model_discovery import safe_text
    return safe_text(value, fallback, 100)


def _efforts(value: Any) -> list[str]:
    from .model_discovery import efforts
    return efforts(value)


def _codex_catalog(home: Path) -> tuple[list[dict], str]:
    """Compatibility fallback for offline inspection, never a live refresh."""
    from .model_discovery import _row
    try:
        path = home / "models_cache.json"
        if path.stat().st_size > 8_000_000: raise ValueError()
        data = json.loads(path.read_text())
        if not isinstance(data.get("models"), list): raise ValueError()
    except (OSError, ValueError, AttributeError):
        return [], "Catalogo Codex non disponibile. Premi Aggiorna modelli."
    entries = {}
    for item in data["models"]:
        if not isinstance(item, dict) or item.get("visibility") != "list": continue
        row = _row(item.get("slug"), item.get("display_name"), item.get("supported_reasoning_levels"),
                   item.get("default_reasoning_level"), effort_scope="model", is_default=bool(item.get("is_default")), description="")
        if row: entries.setdefault(row["model"], row)
    return list(entries.values()), "Ultimo catalogo locale della CLI. Aggiorna per rileggere l’account."


def _cache_path(provider: str) -> Path:
    root = Path(os.environ.get("MYNOTES_HOME", Path.home() / "Library/Application Support/MyNotes"))
    return root / "model-catalogs" / (provider + ".json")


def _snapshot(provider: str) -> dict | None:
    try:
        path = _cache_path(provider)
        if path.stat().st_size > 4_000_000: return None
        data = json.loads(path.read_text())
        if data.get("schema_version") != 2 or data.get("provider") != provider: return None
        if not isinstance(data.get("entries"), list) or not all(isinstance(row, dict) and valid_model(row.get("model")) for row in data["entries"]): return None
        return data
    except (OSError, ValueError, AttributeError): return None


def catalog(provider: str, *, saved: str = "", saved_effort: str = "", refresh: bool = False,
            codex_home: Path | None = None, timeout: float = 20.0) -> dict[str, Any]:
    """Provider-scoped, extensible catalog. Metadata calls do not consume turns."""
    from . import model_discovery as discovery
    import signal
    import threading
    from .utils import atomic_json_write
    if provider not in SUPPORTED_PROVIDERS:
        raise ConfigurationError("Choose codex, agy or claude")
    snapshot = _snapshot(provider) if codex_home is None else None
    bootstrap = [{**preset, "id": preset["model"]} for preset in PRESETS if preset["provider"] == provider]
    result = snapshot or {"entries": bootstrap, "source": "bundled_presets", "account": {"status": "unknown", "label": "", "kind": "", "plan": ""}}
    status = "stale" if result["entries"] else "no_catalog"
    message = "Elenco salvato. I modelli si aggiornano soltanto quando premi Aggiorna modelli."
    cache_warning = ""
    if refresh:
        previous_handler = None
        def cancelled(_signal, _frame): raise discovery.DiscoveryCancelled()
        if threading.current_thread() is threading.main_thread():
            previous_handler = signal.signal(signal.SIGTERM, cancelled)
        try:
            previous_models = {entry["model"] for entry in result["entries"]} | set(result.get("removed_models", []))
            if saved and valid_model(saved): previous_models.add(saved)
            fresh = discovery.discover(provider, timeout)
            fresh["removed_models"] = sorted(previous_models - {entry["model"] for entry in fresh["entries"]})
            result = fresh
            result.update(schema_version=2, provider=provider, fetched_at=discovery.timestamp(), context_key=discovery.context_key(provider))
            status = "ready"; message = "Catalogo letto dalla CLI dell’account."
            try:
                path = _cache_path(provider); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                atomic_json_write(path, result); path.chmod(0o600)
            except OSError:
                cache_warning = "Il catalogo è stato letto, ma la copia locale non è salvabile."
        except discovery.DiscoveryError as error:
            status = "stale" if result["entries"] and error.state == "error" else error.state
            message = str(error)
        except (OSError, subprocess.SubprocessError, ValueError):
            status = "stale" if result["entries"] else "error"
            message = "Impossibile leggere la CLI. Controlla accesso e connessione, poi riprova."
        finally:
            if previous_handler is not None: signal.signal(signal.SIGTERM, previous_handler)
    elif codex_home is not None and provider == "codex":
        entries, message = _codex_catalog(codex_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
        result = {**result, "entries": entries, "source": "codex_local_cache"}
        status = "stale" if entries else "no_catalog"
    # A failed check is not evidence that previously listed models disappeared.
    # Only a successful explicit refresh changes membership.
    usable = True
    rows = []
    seen = set()
    for entry in result["entries"]:
        model = entry.get("model")
        if not valid_model(model) or not model or model in seen: continue
        seen.add(model)
        row = {**entry, "id": model, "provider": provider, "label": _safe_label(entry.get("label"), model),
               "supported_efforts": _efforts(entry.get("supported_efforts")), "source": result["source"],
               "availability": "listed" if status == "ready" else "cached", "selectable": usable}
        if row.get("default_effort") not in row["supported_efforts"]: row["default_effort"] = ""
        rows.append(row)
    # Presets are shortcuts over real rows. They are never catalog entries.
    presets = []
    for preset in PRESETS:
        if preset["provider"] != provider: continue
        matched = next((row for row in rows if row["model"] == preset["model"]), None)
        presets.append({**preset, "selectable": bool(matched and matched["selectable"] and preset["default_effort"] in matched["supported_efforts"]),
                        "availability": "listed" if matched else "unavailable", "source": "shortcut",
                        "supported_efforts": matched["supported_efforts"] if matched else []})
    rows.append({"id": "", "model": "", "provider": provider, "label": "Modello predefinito della CLI", "default_effort": "",
                 "supported_efforts": [], "availability": "provider_default", "selectable": usable, "source": "provider_default", "effort_scope": "agent"})
    if saved and valid_model(saved) and saved not in seen:
        removed = saved in result.get("removed_models", [])
        rows.append({"id": saved, "model": saved, "provider": provider, "label": saved, "default_effort": "",
                     "availability": "saved_unavailable" if removed else "assumed", "selectable": not removed,
                     "supported_efforts": [saved_effort] if saved_effort else [],
                     "source": "saved_preference", "effort_scope": "model"})
    return {"schema_version": 2, "provider": provider, "choices": rows, "presets": presets,
            "default_preset": DEFAULT_PRESET.get(provider, ""), "status": status, "message": message, "cache_warning": cache_warning,
            "account": result.get("account", {}), "source": result["source"], "source_freshness": result.get("freshness", "cache"),
            "fetched_at": result.get("fetched_at", ""), "provider_updated_at": result.get("provider_updated_at", ""),
            "cli_version": result.get("cli_version", ""), "quota": result.get("quota", []), "access_verified": False}


def validate_effort(provider: str, model: str, effort: str) -> None:
    if not effort: return
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", effort): raise ConfigurationError("Unknown effort level")
    # Dynamic capabilities take precedence over legacy lists. No generation or
    # discovery is triggered by an atomic preference edit.
    current = catalog(provider)
    matching = next((row for row in current["choices"] if row["model"] == model and row["source"] != "saved_preference"), None)
    if matching and matching["supported_efforts"]:
        if effort not in matching["supported_efforts"]: raise ConfigurationError("This effort is not supported by the selected model")
        return
    if effort not in EFFORTS: raise ConfigurationError("Unknown effort level; refresh the provider catalog")
    if provider == "agy" and effort not in AGY_EFFORTS: raise ConfigurationError("This effort is not supported by Agy")
