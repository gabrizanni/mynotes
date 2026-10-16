"""Bounded metadata-only transports for installed agent CLIs.

No user message, turn, login or inference request is sent by these adapters.
Authentication stays owned by the CLI; MyNotes never reads its tokens.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


class DiscoveryError(Exception):
    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


class DiscoveryCancelled(BaseException):
    pass


def timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def environment() -> dict[str, str]:
    from .providers import provider_environment
    return provider_environment()


def context_key(provider: str, env: dict | None = None) -> str:
    """Invalidate private snapshots on detectable auth/config changes, not tokens."""
    env = env or environment()
    home = Path.home()
    roots = {"codex": Path(env.get("CODEX_HOME", home / ".codex")),
             "agy": home / ".gemini", "claude": Path(env.get("CLAUDE_CONFIG_DIR", home / ".claude"))}
    names = {"codex": ("auth.json", "config.toml"),
             "agy": ("oauth_creds.json", "antigravity-cli/settings.json"),
             "claude": (".credentials.json", "settings.json")}
    parts = [provider, str(roots[provider]), shutil.which(provider, path=env["PATH"]) or "missing"]
    for name in names[provider]:
        path = roots[provider] / name
        try:
            stat = path.stat(); parts.append(f"{name}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(name + ":missing")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def safe_text(value, fallback: str = "", limit: int = 200) -> str:
    return value if isinstance(value, str) and len(value) <= limit and not any(ord(c) < 32 for c in value) else fallback


def efforts(values) -> list[str]:
    """Provider-declared effort names are extensible, not a closed enum."""
    result = []
    for item in values if isinstance(values, list) else []:
        value = next((item[k] for k in ("reasoningEffort", "effort", "value") if k in item), None) if isinstance(item, dict) else item
        if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) and value not in result:
            result.append(value)
    return result


class Channel:
    """A dedicated process group; bounded pipes, deadline and owned cleanup."""
    def __init__(self, command: list[str], env: dict, deadline: float):
        self.deadline = deadline
        self.process = subprocess.Popen(command, env=env, cwd=Path(os.getenv("TMPDIR", "/tmp")),
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        start_new_session=True, bufsize=0)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, "out")
        self.selector.register(self.process.stderr, selectors.EVENT_READ, "err")
        self.buffer = b""; self.errors = b""; self.received = 0

    def send(self, value: dict) -> None:
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        self.process.stdin.flush()

    def pump(self) -> bool:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DiscoveryError("error", "Tempo di aggiornamento scaduto. Riprova.")
        for key, _ in self.selector.select(min(remaining, 0.25)):
            chunk = os.read(key.fileobj.fileno(), 65536)
            if not chunk:
                self.selector.unregister(key.fileobj); continue
            self.received += len(chunk)
            if self.received > 2_000_000:
                raise DiscoveryError("incompatible", "Il catalogo della CLI supera i limiti supportati.")
            if key.data == "out": self.buffer += chunk
            else: self.errors = (self.errors + chunk)[-8192:]
        return bool(self.selector.get_map())

    def response(self, identifier, *, claude: bool = False) -> dict:
        while True:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try: message = json.loads(line)
                except ValueError: continue  # CLI banners are not JSON-RPC.
                if not isinstance(message, dict): continue
                if claude and message.get("type") == "control_response":
                    response = message.get("response", {})
                    if response.get("request_id") != identifier: continue
                    if response.get("subtype") == "error": self.failed()
                    result = response.get("response", {})
                    if not isinstance(result, dict): self.failed()
                    return result
                if not claude and message.get("id") == identifier:
                    if "error" in message: self.failed()
                    result = message.get("result", {})
                    if not isinstance(result, dict): self.failed()
                    return result
            if not self.pump(): self.failed()

    def failed(self):
        # Never expose raw CLI errors: they may contain account/URL information.
        text = self.errors.decode(errors="replace").lower()
        if any(s in text for s in ("login", "unauthorized", "not authenticated", "sign in")):
            raise DiscoveryError("login_required", "Accedi all’agente e aggiorna i modelli.")
        raise DiscoveryError("incompatible", "La CLI non ha restituito metadati validi. Controlla accesso e aggiornamenti.")

    def listing(self, include_stderr: bool = False) -> str:
        self.process.stdin.close()
        while self.pump(): pass
        self.process.wait(timeout=max(0.1, self.deadline - time.monotonic()))
        if self.process.returncode: self.failed()
        return (self.buffer + (self.errors if include_stderr else b"")).decode(errors="replace")

    def close(self):
        if self.process.poll() is None:
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: self.process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                self.process.wait()
        # Also reap any owned language-server children after their parent exits.
        try: os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        self.selector.close()
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            if pipe and not pipe.closed: pipe.close()


def run(command: list[str], env: dict, deadline: float, *, include_stderr: bool = False) -> str:
    channel = Channel(command, env, deadline)
    try: return channel.listing(include_stderr)
    finally: channel.close()


def _row(model, label, levels, default="", **extras) -> dict | None:
    from .models import valid_model
    if not valid_model(model) or not model: return None
    supported = efforts(levels)
    return {"model": model, "label": safe_text(label, model, 100),
            "supported_efforts": supported, "default_effort": default if default in supported else "", **extras}


def codex(executable: str, env: dict, deadline: float) -> dict:
    channel = Channel([executable, "app-server", "--stdio"], env, deadline)
    try:
        channel.send({"method": "initialize", "id": 1, "params": {"clientInfo": {"name": "mynotes", "version": "0.2"}}})
        initialized = channel.response(1)
        channel.send({"method": "initialized", "params": {}})
        channel.send({"method": "account/read", "id": 2, "params": {"refreshToken": False}})
        account_result = channel.response(2); account = account_result.get("account") or {}
        if not isinstance(account, dict): channel.failed()
        if not account and account_result.get("requiresOpenaiAuth"):
            raise DiscoveryError("login_required", "Accedi a Codex per leggere i modelli del tuo account.")
        entries = []; cursor = None; seen = set(); request_id = 3
        while True:
            params = {"limit": 100, "includeHidden": False}
            if cursor: params["cursor"] = cursor
            channel.send({"method": "model/list", "id": request_id, "params": params})
            response = channel.response(request_id)
            if not isinstance(response.get("data"), list): channel.failed()
            for item in response["data"]:
                if not isinstance(item, dict) or item.get("hidden"): continue
                row = _row(item.get("model"), item.get("displayName"), item.get("supportedReasoningEfforts"),
                           item.get("defaultReasoningEffort"), is_default=bool(item.get("isDefault")), effort_scope="model",
                           description=safe_text(item.get("description")))
                if row: entries.append(row)
            cursor = response.get("nextCursor")
            if not cursor: break
            if not isinstance(cursor, str) or cursor in seen: channel.failed()
            seen.add(cursor); request_id += 1
        kind = safe_text(account.get("type"), "provider")
        plan = safe_text(account.get("planType"))
        fingerprint = hashlib.sha256(json.dumps(account, sort_keys=True).encode()).hexdigest()
        home = Path(env.get("CODEX_HOME", Path.home() / ".codex"))
        try:
            cache = json.loads((home / "models_cache.json").read_text())
            updated = safe_text(cache.get("fetched_at"))
        except (OSError, ValueError, AttributeError): updated = ""
        return {"entries": entries, "source": "codex_app_server", "provider_updated_at": updated,
                "freshness": "cli_catalog", "cli_version": safe_text(initialized.get("userAgent")),
                "account": {"status": "connected" if account else "unknown", "kind": kind,
                            "plan": plan, "label": "ChatGPT" if kind == "chatgpt" else "API / altro provider", "fingerprint": fingerprint}}
    finally: channel.close()


def agy(executable: str, env: dict, deadline: float) -> dict:
    output = run([executable, "models"], env, deadline)
    help_text = run([executable, "--help"], env, deadline, include_stderr=True)
    effort_line = next((line for line in help_text.splitlines() if re.match(r"\s*--effort\s", line)), "")
    match = re.search(r"\(([a-z][a-z0-9_|-]+)\)", effort_line)
    levels = efforts(match.group(1).split("|")) if match else []
    entries = []
    for line in output.splitlines():
        model, separator, name = line.partition("\t")
        if not separator: continue
        row = _row(model, name, levels, effort_scope="agent", is_default=False, description="")
        if row: entries.append(row)
    quota = []
    try:
        usage = run([executable, "--print", "/usage"], env, min(deadline, time.monotonic() + 5))
        for line in usage.splitlines():
            cells = line.split("\t")
            if len(cells) == 4 and re.fullmatch(r"\d{1,3}%", cells[2]):
                quota.append({"group": safe_text(cells[0]), "window": safe_text(cells[1]), "remaining": cells[2], "resets_at": safe_text(cells[3])})
    except (DiscoveryError, OSError, subprocess.SubprocessError): pass
    return {"entries": entries, "source": "agy_models", "freshness": "cli_catalog", "provider_updated_at": "",
            "quota": quota, "account": {"status": "connected" if quota else "unknown", "kind": "subscription" if quota else "unknown", "label": "Account Agy", "plan": ""}}


def claude(executable: str, env: dict, deadline: float) -> dict:
    # This is the metadata handshake used by the official Agent SDK. The input
    # contains no `user` frame, and no prompt or inference can start.
    channel = Channel([executable, "--print", "--input-format", "stream-json", "--output-format", "stream-json",
                       "--verbose", "--no-session-persistence", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                       "--settings", '{"disableAllHooks":true}'], env, deadline)
    try:
        channel.send({"type": "control_request", "request_id": "catalog", "request": {"subtype": "initialize", "hooks": None}})
        value = channel.response("catalog", claude=True)
        if not isinstance(value.get("models"), list): channel.failed()
        entries = []
        for item in value["models"]:
            if not isinstance(item, dict) or item.get("disabled"): continue
            row = _row(item.get("value"), item.get("displayName"), item.get("supportedEffortLevels"),
                       item.get("defaultEffort"), is_default=item.get("value") == "default", effort_scope="model",
                       description=safe_text(item.get("description")))
            if row: entries.append(row)
        raw_account = value.get("account") or {}
        if not isinstance(raw_account, dict): channel.failed()
        return {"entries": entries, "source": "claude_initialize", "freshness": "cli_catalog", "provider_updated_at": "",
                "account": {"status": "connected" if raw_account else "unknown", "kind": safe_text(raw_account.get("type"), "unknown"),
                            "label": "Account Claude Code", "plan": safe_text(raw_account.get("subscriptionType"))}}
    finally: channel.close()


def discover(provider: str, timeout: float = 20, env: dict | None = None) -> dict:
    env = env or environment()
    executable = shutil.which(provider, path=env["PATH"])
    if not executable: raise DiscoveryError("not_installed", "Installa l’agente per leggere il suo catalogo.")
    result = {"codex": codex, "agy": agy, "claude": claude}[provider](executable, env, time.monotonic() + max(0.1, min(20, timeout)))
    unique = {}
    for row in result["entries"]: unique.setdefault(row["model"], row)
    result["entries"] = list(unique.values())
    if not result["entries"]: raise DiscoveryError("incompatible", "La CLI ha restituito un catalogo vuoto o non riconosciuto. Controlla accesso e aggiornamenti.")
    return result
