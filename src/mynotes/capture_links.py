"""Calendar URL resolution and URL-free diagnostics shared by CLI and UI."""
from __future__ import annotations

import socket
import ssl
import urllib.error
from urllib.parse import parse_qs, quote, unquote, urlsplit


ERRORS = {
    "INVALID_URL": ("Inserisci un link HTTPS o webcal/iCal senza credenziali HTTP.", False, "correct_link"),
    "GOOGLE_MULTIPLE_CALENDARS": ("Il link contiene più calendari. Collega un calendario alla volta con il suo link iCal.", False, "use_ical"),
    "GOOGLE_LINK_UNSUPPORTED": ("Questo link Google non identifica un singolo calendario. Copia il link iCal dalle impostazioni del calendario.", False, "use_ical"),
    "WEBPAGE_NOT_CALENDAR": ("Questo indirizzo apre una pagina web. Incolla il link iCal del calendario.", False, "use_ical"),
    "GOOGLE_FEED_UNAVAILABLE": ("Il calendario Google non è accessibile tramite il feed pubblico. Copia l’indirizzo iCal privato dalle impostazioni del calendario, senza cambiarne la condivisione.", False, "use_private_ical"),
    "ACCESS_DENIED": ("Il calendario richiede un’autorizzazione. Usa un link di sottoscrizione iCal accessibile a Capture.", False, "use_ical"),
    "FEED_UNAVAILABLE": ("Il calendario non è disponibile a questo indirizzo. Controlla il link iCal.", False, "correct_link"),
    "NETWORK_ERROR": ("Impossibile raggiungere il calendario. Controlla la connessione e riprova.", True, "retry"),
    "TIMEOUT": ("Il calendario non ha risposto in tempo. Riprova tra poco.", True, "retry"),
    "TLS_ERROR": ("Non è stato possibile verificare la connessione sicura al calendario. Controlla rete, data del Mac e certificati.", False, "check_connection"),
    "INVALID_CALENDAR": ("La risposta non contiene un calendario iCal valido. Controlla il link di sottoscrizione.", False, "use_ical"),
    "FEED_TOO_LARGE": ("Il calendario supera il limite di dieci MB. Usa un feed più piccolo.", False, "correct_link"),
    "INSECURE_REDIRECT": ("Il calendario rimanda a una connessione non HTTPS. Usa un link iCal sicuro.", False, "correct_link"),
    "RECORDER_INCOMPATIBLE": ("Aggiorna il registratore Capture per importare lezioni senza corso o numero.", False, "update_capture"),
    "SOURCE_CHANGED": ("Il collegamento identifica un calendario diverso. Collegalo come nuovo calendario.", False, "new_calendar"),
    "INVALID_SOURCE": ("Controlla nome, fuso, periodo e associazioni del calendario.", False, "correct_fields"),
    "INTERNAL_ERROR": ("Non è stato possibile completare il collegamento. Riprova e controlla lo stato di Capture.", True, "retry"),
}


class CalendarLinkError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(ERRORS[code][0])


def resolve(url: str) -> str:
    if not isinstance(url, str):
        raise CalendarLinkError("INVALID_URL")
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    try:
        parsed = urlsplit(url)
        _ = parsed.port  # Reject malformed ports while preserving valid HTTPS feeds.
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or any(ord(c) < 32 for c in url)):
            raise ValueError()
    except ValueError:
        raise CalendarLinkError("INVALID_URL") from None
    if parsed.hostname == "calendar.google.com":
        if parsed.path.rstrip("/") == "/calendar/embed":
            sources = parse_qs(parsed.query, keep_blank_values=True).get("src", [])
            if len(sources) > 1:
                raise CalendarLinkError("GOOGLE_MULTIPLE_CALENDARS")
            if len(sources) != 1 or not sources[0].strip() or any(ord(c) < 32 or c in "/\\" for c in sources[0]):
                raise CalendarLinkError("GOOGLE_LINK_UNSUPPORTED")
            return "https://calendar.google.com/calendar/ical/" + quote(sources[0], safe="") + "/public/basic.ics"
        if not parsed.path.startswith("/calendar/ical/"):
            raise CalendarLinkError("GOOGLE_LINK_UNSUPPORTED")
    elif parsed.hostname == "accounts.google.com":
        raise CalendarLinkError("GOOGLE_LINK_UNSUPPORTED")
    return url


def google_identity(url: str) -> str | None:
    try:
        parsed = urlsplit(resolve(url))
        if parsed.hostname == "calendar.google.com" and parsed.path.startswith("/calendar/ical/"):
            return unquote(parsed.path.split("/")[3]) or None
    except (ValueError, IndexError):
        pass
    return None


def is_public_google(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.hostname == "calendar.google.com" and parsed.path.startswith("/calendar/ical/") and "/public/" in parsed.path


def validate_body(data: bytes, url: str) -> None:
    start = data.lstrip(b"\xef\xbb\xbf \r\n\t")[:4096].lower()
    if start.startswith((b"<!doctype html", b"<html")) or b"<html" in start:
        raise CalendarLinkError("GOOGLE_FEED_UNAVAILABLE" if is_public_google(url) else "WEBPAGE_NOT_CALENDAR")
    if not start.startswith(b"begin:vcalendar"):
        raise CalendarLinkError("INVALID_CALENDAR")


def diagnostic(error: Exception, url: str = "") -> dict:
    """Never interpolate provider exceptions: they may contain private URLs."""
    code = "INTERNAL_ERROR"
    if isinstance(error, CalendarLinkError):
        code = error.code
    elif isinstance(error, urllib.error.HTTPError):
        if error.code in {401, 403, 404, 410}:
            code = "GOOGLE_FEED_UNAVAILABLE" if is_public_google(url) else "ACCESS_DENIED" if error.code in {401, 403} else "FEED_UNAVAILABLE"
        elif error.code in {408, 429} or error.code >= 500:
            code = "NETWORK_ERROR"
        else:
            code = "FEED_UNAVAILABLE"
    else:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        if isinstance(reason, ssl.SSLError):
            code = "TLS_ERROR"
        elif isinstance(reason, (TimeoutError, socket.timeout)):
            code = "TIMEOUT"
        elif isinstance(error, (urllib.error.URLError, ConnectionError)):
            code = "NETWORK_ERROR"
        elif isinstance(error, (ValueError, KeyError, TypeError)):
            code = "INVALID_CALENDAR"
    message, retryable, action = ERRORS[code]
    return {"code": code, "message": message, "retryable": retryable, "suggestedAction": action}
