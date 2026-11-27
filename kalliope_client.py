"""Client Python non ufficiale per le API REST di KalliopePBX V4.

Solo libreria standard. Copre l'autenticazione (header X-authenticate monouso),
il registro chiamate (CDR), interni, code, gruppi di chiamata e controlli orari.

Variabili d'ambiente usate da `KalliopeClient.from_env()` e dalla riga di comando:
    KALLIOPE_URL       base dell'API, es. https://pbx.example.com
    KALLIOPE_DOMAIN    dominio del tenant («default» sui sistemi mono-tenant)
    KALLIOPE_USER      utente (lo stesso dell'accesso web)
    KALLIOPE_PASSWORD  password

Progetto indipendente, non affiliato a NetResults S.r.l.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Iterable

__version__ = "0.1.0"

# Tipi di destinazione usati negli oggetti «failover» (osservati su un'istanza 4.x;
# il 13 non compare nella documentazione ufficiale).
DESTINATION_TYPES = {
    4: "extension",
    5: "callgroup",
    7: "queue",
    13: "hangup",
    15: "ivr",
    17: "voicemail",
    18: "external_number",
}

# Tipi di account (dispositivi) di un interno.
ACCOUNT_TYPES = {2: "IPPhone", 9: "KPEDesktop", 10: "KPEMobile", 11: "WebCTI"}


class KalliopeError(Exception):
    """Risposta HTTP di errore del centralino."""

    def __init__(self, status: int, body: str, method: str, path: str):
        super().__init__(f"{method} {path} -> HTTP {status}: {body[:300]}")
        self.status, self.body, self.method, self.path = status, body, method, path


def digest_password(password: str, salt: str) -> str:
    """sha256 esadecimale di «password{salt}» (le graffe fanno parte della stringa)."""
    return hashlib.sha256(f"{password}{{{salt}}}".encode()).hexdigest()


def build_auth_header(username: str, domain: str, digest_pw: str,
                      nonce: str | None = None, created: str | None = None) -> str:
    """Costruisce il valore dell'header X-authenticate.

    Digest = base64( sha256_binario( nonce + digestPassword + username + domain + created ) )
    `nonce` e `created` si passano solo nei test: di norma vengono generati qui.
    """
    nonce = nonce or secrets.token_hex(16)
    created = created or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = hashlib.sha256((nonce + digest_pw + username + domain + created).encode()).digest()
    digest = base64.b64encode(raw).decode()
    return (f'RestApiUsernameToken Username="{username}", Domain="{domain}", '
            f'Digest="{digest}", Nonce="{nonce}", Created="{created}"')


class KalliopeClient:
    """Client minimale: una richiesta = un header nuovo, nessuna sessione da tenere viva."""

    def __init__(self, base_url: str, domain: str, username: str, password: str,
                 timeout: float = 60, lock_retries: int = 3, lock_wait: float = 5):
        self.base_url = base_url.rstrip("/")
        self.domain, self.username = domain, username
        self._password = password
        self.timeout, self.lock_retries, self.lock_wait = timeout, lock_retries, lock_wait
        self._digest_pw: str | None = None

    @classmethod
    def from_env(cls, **kw: Any) -> "KalliopeClient":
        e = os.environ
        return cls(e["KALLIOPE_URL"], e.get("KALLIOPE_DOMAIN", "default"),
                   e["KALLIOPE_USER"], e["KALLIOPE_PASSWORD"], **kw)

    # --- trasporto -----------------------------------------------------------------

    def salt(self) -> str:
        """Salt del tenant: chiamata anonima, serve `Accept: application/json`."""
        req = urllib.request.Request(f"{self.base_url}/rest/salt/{self.domain}",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)["salt"]

    def _header(self) -> str:
        if self._digest_pw is None:
            self._digest_pw = digest_password(self._password, self.salt())
        return build_auth_header(self.username, self.domain, self._digest_pw)

    def request(self, method: str, path: str, body: Any = None) -> Any:
        """Esegue la richiesta e restituisce il JSON decodificato.

        Le scritture rispondono 503 «Lock can not be acquired» mentre qualcuno tiene
        aperto il pannello di amministrazione: in quel caso si riprova qualche volta.
        """
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(self.lock_retries + 1):
            headers = {"X-authenticate": self._header(), "Accept": "application/json"}
            if data is not None:
                headers["Content-Type"] = "application/json"  # non merge-patch: risponde 400
            req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text = r.read().decode("utf-8", "replace")
                    return json.loads(text) if text.strip() else None
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                if e.code == 503 and "ock" in text and attempt < self.lock_retries:
                    time.sleep(self.lock_wait)
                    continue
                raise KalliopeError(e.code, text, method, path) from None
        raise AssertionError("non raggiungibile")

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    @staticmethod
    def items(result: Any) -> list:
        """Le liste v2 arrivano come {"items": [...]}, quelle vecchie come lista semplice."""
        if isinstance(result, list):
            return result
        return (result or {}).get("items") or []

    # --- documentazione dell'istanza -----------------------------------------------

    def swagger(self) -> dict:
        """Specifica completa degli endpoint dell'istanza (il CDR non vi compare)."""
        return self.get("/api/doc.json")

    # --- registro chiamate ----------------------------------------------------------

    def cdr(self, start: dt.date, end: dt.date | None = None, fmt: str = "detailed") -> list:
        """Chiamate per giorno o intervallo di giorni (stesso mese), formato `summary` o `detailed`."""
        end = end or start
        if (start.year, start.month) != (end.year, end.month):
            raise ValueError("per intervalli su più mesi usare cdr_search()")
        days = f"{start:%d}" if start == end else f"{start:%d}-{end:%d}"
        return self.get(f"/rest/cdr/{fmt}/{start:%Y}/{start:%m}/{days}") or []

    def cdr_search(self, begin: dt.datetime | None = None, end: dt.datetime | None = None,
                   fmt: str = "detailed", **filters: Any) -> list:
        """Ricerca con filtri in AND (POST).

        Filtri: unique_id, source_type, dest_type, caller_id, anonymous, called, duration,
        status, answered_by, account_code, gateway_name, conversation_time, src_peer_name,
        src_ip_port, src_exten. `duration` accetta anche operatori, es. "<100".
        """
        q: dict[str, Any] = dict(filters)
        if begin:
            q["begin"] = begin.strftime("%Y-%m-%d %H:%M:%S")
        if end:
            q["end"] = end.strftime("%Y-%m-%d %H:%M:%S")
        return self.request("POST", f"/rest/cdr/{fmt}", {"cdr": q}) or []

    # --- interni --------------------------------------------------------------------

    def extensions(self, redact: bool = True) -> list:
        """Elenco degli interni. La risposta contiene campi password: con `redact` vengono tolti."""
        rows = self.items(self.get("/rest/extension"))
        return [_redact(r) for r in rows] if redact else rows

    # --- code -----------------------------------------------------------------------

    def queues(self) -> list:
        return self.items(self.get("/api/pbx/v2/queue?itemsPerPage=200"))

    def queue(self, queue_id: int) -> dict:
        return self.get(f"/api/pbx/v2/queue/{queue_id}")

    def queue_members(self, queue_id: int) -> list:
        """I membri di una coda sono singoli account (dispositivi), non interni."""
        return self.items(self.get(f"/api/pbx/v2/queue/{queue_id}/account"))

    def queue_add_member(self, queue_id: int, account_id: int, penalty: int = 0) -> Any:
        return self.request("POST", "/api/pbx/v2/queueAccount",
                            {"queueId": queue_id, "accountId": account_id, "penalty": penalty})

    def queue_remove_member(self, membership_id: int) -> Any:
        return self.request("DELETE", f"/api/pbx/v2/queueAccount/{membership_id}")

    # --- gruppi di chiamata ---------------------------------------------------------

    def callgroups(self) -> list:
        return self.items(self.get("/api/pbx/v2/callgroup?itemsPerPage=200"))

    def callgroup_priorities(self, group_id: int) -> list:
        """Livelli di priorità del gruppo; `extensionsId` contiene gli id (non i numeri) degli interni."""
        return self.items(self.get(f"/api/pbx/v2/callgroup/{group_id}/priority"))

    # --- controlli orari ------------------------------------------------------------

    def checktimes(self) -> list:
        return self.items(self.get("/rest/checktime"))

    def checktime(self, checktime_id: int) -> dict:
        return self.get(f"/rest/checktime/{checktime_id}")

    def set_checktime_periods(self, checktime_id: int, periods: Iterable[dict]) -> Any:
        """ATTENZIONE: sostituisce TUTTI i periodi. Passare periodi già in formato di scrittura
        (vedi `writable_period`)."""
        return self.request("PATCH", f"/rest/checktime/{checktime_id}",
                            {"checktimePeriods": list(periods)})


# --- funzioni di aiuto ---------------------------------------------------------------

_SECRET_HINTS = ("password", "secret", "pin")


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if any(h in k.lower() for h in _SECRET_HINTS) and v else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def writable_destination(failover: dict | None) -> dict:
    """Toglie l'`id` da un oggetto destinazione letto dall'API: in scrittura non è accettato."""
    return {k: v for k, v in (failover or {}).items() if k != "id"}


def writable_period(period: dict) -> dict:
    """Converte un periodo di un controllo orario dal formato di lettura a quello di scrittura.

    In lettura: hourFrom/minuteFrom/hourTo/minuteTo. In scrittura: timeFrom/timeTo «HH:MM»
    (tutto il giorno = 00:00-23:59, non «*») e nessun `id`.
    """
    def hhmm(h: Any, m: Any, default: str) -> str:
        if str(h) == "*" or h is None:
            return default
        return f"{int(h):02d}:{int(m if str(m) != '*' and m is not None else 0):02d}"

    return {
        "priority": period["priority"],
        "enabled": period.get("enabled", True),
        "timeFrom": hhmm(period.get("hourFrom"), period.get("minuteFrom"), "00:00"),
        "timeTo": hhmm(period.get("hourTo"), period.get("minuteTo"), "23:59"),
        "weekDayFrom": period["weekDayFrom"], "weekDayTo": period["weekDayTo"],
        "monthDayFrom": period["monthDayFrom"], "monthDayTo": period["monthDayTo"],
        "month": period["month"],
        "overrideFailoverEnabled": period.get("overrideFailoverEnabled", False),
        "failover": writable_destination(period.get("failover")),
    }


def classify_call(call: dict) -> tuple[str, str | None]:
    """Esito di una chiamata del CDR `detailed`: (esito, dove).

    esito: «answered» (ha risposto un operatore o un cellulare inoltrato), «voicemail»,
    «message» (ha sentito solo un annuncio), «missed». Per le perse, `dove` è il tipo
    dell'ultima tratta significativa: ivr, queue, callg, checktime.
    """
    legs = call.get("detail") or []
    if any(u.get("answerTime") for leg in legs for u in (leg.get("userEvents") or [])):
        return "answered", None
    if any(leg.get("dest_type") == "obl" and leg.get("answer_time") for leg in legs):
        return "answered", "obl"
    if any(leg.get("dest_type") == "voicemail" for leg in legs):
        return "voicemail", None
    if call.get("status") == "OK" and not any(leg.get("dest_type") in ("queue", "callg") for leg in legs):
        return "message", None
    last = next((leg for leg in reversed(legs)
                 if leg.get("dest_type") in ("queue", "callg", "ivr", "checktime")),
                legs[-1] if legs else {})
    return "missed", last.get("dest_type")


def mask_number(number: str | None, keep: int = 3) -> str:
    """Nasconde le ultime cifre di un numero, per i log."""
    if not number:
        return ""
    return number[:-keep] + "x" * keep if len(number) > keep + 2 else number


# --- riga di comando -----------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kalliope_client", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cdr", help="chiamate di un giorno, con esito")
    c.add_argument("--day", default=dt.date.today().isoformat(), help="AAAA-MM-GG (predefinito: oggi)")
    c.add_argument("--inbound-only", action="store_true", help="solo chiamate entrate da linee esterne")
    c.add_argument("--show-numbers", action="store_true", help="non mascherare i numeri")
    sub.add_parser("queues", help="elenco delle code")
    sub.add_parser("callgroups", help="elenco dei gruppi di chiamata")
    sub.add_parser("extensions", help="elenco degli interni (senza campi riservati)")
    sub.add_parser("checktimes", help="elenco dei controlli orari")
    g = sub.add_parser("get", help="GET di un percorso qualsiasi, es. /api/doc.json")
    g.add_argument("path")
    a = p.parse_args(argv)
    k = KalliopeClient.from_env()

    if a.cmd == "cdr":
        day = dt.date.fromisoformat(a.day)
        for call in k.cdr(day):
            if a.inbound_only and call.get("source_type") != "ibl":
                continue
            outcome, where = classify_call(call)
            show = (lambda n: n or "") if a.show_numbers else mask_number
            print(f"{call.get('start_datetime')}  {call.get('source_type'):<12} "
                  f"{show(call.get('caller')):<16} -> {show(call.get('called')):<16} "
                  f"{call.get('duration', 0):>5}s  {outcome}{' (' + where + ')' if where else ''}")
        return 0
    result = {"queues": k.queues, "callgroups": k.callgroups, "extensions": k.extensions,
              "checktimes": k.checktimes}.get(a.cmd, lambda: k.get(a.path))()
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except KalliopeError as err:
        print(err, file=sys.stderr)
        sys.exit(1)
