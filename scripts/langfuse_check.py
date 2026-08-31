"""Confronta le chiamate che il council ha fatto con quelle arrivate a Langfuse.

PERCHE' ESISTE: le tracce non partono da questo codice. Partono da OpenRouter Broadcast,
una spunta nell'account, fuori dal repo. Fino al 2026-08-31 nessun gate misurava quel
pezzo e nessuno sapeva dire se avesse mai funzionato — funzionava, ma per fortuna, non
per prova.

WARNING-ONLY, DI PROPOSITO. Un guardiano che uccide cio' che sorveglia e' peggio di
nessun guardiano: se Langfuse ha un disservizio, il verdetto sul council resta quello
dello step precedente. Percio' `main` restituisce sempre 0.

TRE COSE MISURATE, NON DEDOTTE DALLA DOCUMENTAZIONE:

1. Si filtra per `sessionId`, NON per `traceId`. Il 2026-08-31 una run si e' spezzata in
   DUE tracce: i sei voter sotto il trace_id chiesto, il presidente caduto sotto un
   trace_id che OpenRouter ha generato per conto suo. Il sessionId invece era lo stesso
   su entrambe. Un controllo che filtrasse per traceId avrebbe contato 6 su 7 e gridato
   al dato perso: esattamente il falso allarme che lo farebbe ignorare.

2. Il confronto e' `arrivate >= partite`, non `==`. Un tentativo fallito produce comunque
   una generazione su Langfuse, mentre nella telemetria non lascia `generation_id`. Il
   guasto da vedere e' Langfuse che perde roba, cioe' `arrivate < partite`; il verso
   opposto e' rumore normale, e allarmarci sopra renderebbe muto il controllo.

3. `LANGFUSE_BASE_URL` non ha un default. Langfuse Cloud ha regioni distinte (UE e USA) e
   un default cablato puntato alla regione sbagliata non fallisce: risponde 200 con zero
   generazioni, cioe' produce il falso allarme piu' convincente che ci sia.

4. L'ALLARME NON GUARDA LA RUN APPENA FATTA. Le docs Langfuse, lette il 2026-08-31:
   *"direct OpenTelemetry exporters that do not send `x-langfuse-ingestion-version: 4`
   can be delayed by up to 15 minutes on v2 endpoints"*
   (langfuse.com/docs/api-and-data-platform/features/observations-api, letta 2026-08-31).
   OpenRouter Broadcast e' uno di quegli esportatori e non sappiamo cosa dichiari. Un
   controllo che aspetta un minuto e poi grida "dato perso" produrrebbe falsi allarmi
   — e un monitor che grida al lupo e' un monitor che si smette di leggere.
   Percio' l'allarme sta su una domanda che il ritardo non tocca: **negli ultimi 8 giorni
   e' arrivata almeno una run completa?** Otto perche' la sentinella e' settimanale: se
   l'ingestione e' viva, la run della settimana scorsa e' li' da giorni. Il confronto
   sulla run corrente resta, ma stampato come informazione, mai come allarme.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# `python scripts/langfuse_check.py` mette `scripts/` su sys.path, non la radice del repo,
# quindi `council` non sarebbe importabile. La suite non se ne accorge — pytest la radice
# ce la mette — e il difetto uscirebbe solo lanciando il comando, cioe' in CI.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from council.config import USER_ID  # noqa: E402

# L'ingestione e' asincrona e puo' ritardare fino a 15 minuti (vedi nota 4 in testa), quindi
# questo sondaggio e' un best-effort per STAMPARE un confronto, mai per allarmare.
# Il budget si calcola invece di scriverlo a mano: si dorme FRA i tentativi, non dopo
# l'ultimo, quindi sei tentativi da 15s fanno 75 secondi e non 90 — e "90" era finito in
# tre documenti prima che qualcuno contasse.
POLL_ATTEMPTS = 6
POLL_INTERVAL_S = 15
POLL_BUDGET_S = POLL_INTERVAL_S * (POLL_ATTEMPTS - 1)
TIMEOUT_S = 20
SPEND_WINDOW_DAYS = 30
# Tetto alle pagine: senza, un'API che restituisce sempre lo stesso cursore manda il ciclo
# all'infinito e il job muore per `timeout-minutes`, cioe' con un rosso attribuito al
# council. 50 pagine da 100 sono 5000 osservazioni, due ordini di grandezza sopra una
# finestra di 30 giorni misurata a ~270.
MAX_PAGES = 50
# Otto e non sette: la sentinella e' settimanale e i cron di GitHub slittano — il 2026-08-31
# di quasi sette ore. Una finestra di esattamente sette giorni farebbe scattare l'allarme
# per un ritardo dello scheduler invece che per un'ingestione morta.
FRESHNESS_WINDOW_DAYS = 8
# Una run del council sono 3 risposte + 3 valutazioni + 1 sintesi. Una sessione che ne porta
# almeno tante e' arrivata intera; sotto, e' arrivata a pezzi.
CALLS_PER_RUN = 7
REQUIRED_ENV = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
# Importato e non ricopiato: e' il valore per cui questo script FILTRA Langfuse, e lo stesso
# che `stages.py` spedisce a OpenRouter. Due copie tenute a mano che divergono non farebbero
# tacere il controllo — lo farebbero urlare ogni settimana per sempre su un'ingestione sana.
COUNCIL_USER_ID = USER_ID


def read_telemetry(path: str) -> tuple[str | None, int]:
    """Estrae (session_id, chiamate che hanno davvero raggiunto OpenRouter) da stderr.

    Il council non emette `query_complete` se cade lo stadio 3 — torna 1 prima. Percio' i
    contatori di quel record non sono una base affidabile: si contano invece i
    `generation_id` non nulli, che OpenRouter assegna a ogni chiamata riuscita e che
    esistono anche in una run che muore a meta'.
    """
    session_id: str | None = None
    generations = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or "trace_id" not in record:
                    continue
                if session_id is None:
                    session_id = record["trace_id"]
                if record.get("generation_id"):
                    generations += 1
    except OSError:
        # Lo step gira con `if: always()`, quindi anche quando il council non e' partito
        # affatto e il file non esiste. Sollevare qui farebbe fallire il job: il guardiano
        # ucciderebbe cio' che sorveglia, che e' il difetto che tutto questo file evita.
        return None, 0
    return session_id, generations


def _api_get(base_url: str, auth: str, params: dict[str, str]) -> dict:
    """Una GET autenticata all'API pubblica v2. `base_url` DEVE essere https.

    Lo schema si controlla perche' `base_url` arriva da un segreto e finisce in
    `urlopen`, che parla anche `file://` — con cui questa funzione leggerebbe il disco
    invece della rete — e `http://`, su cui la Basic auth viaggerebbe in chiaro.
    """
    if not base_url.lower().startswith("https://"):
        raise ValueError("LANGFUSE_BASE_URL deve iniziare con https://")
    url = f"{base_url.rstrip('/')}/api/public/v2/observations?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 — schema forzato sopra
        return json.loads(resp.read())


def count_arrived(base_url: str, auth: str, session_id: str) -> tuple[int, float]:
    """Ritorna (generazioni, costo) della sessione secondo Langfuse."""
    data = _api_get(
        base_url,
        auth,
        {"sessionId": session_id, "type": "GENERATION", "limit": "100", "fields": "core,usage"},
    )["data"]
    # `float(...)`: nel campione delle docs Langfuse i campi di prezzo arrivano come
    # stringhe, e sommare una stringa a un float solleva TypeError.
    return len(data), sum(float(o.get("totalCost") or 0.0) for o in data)


def _window_start(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def complete_runs_since(base_url: str, auth: str, days: int) -> tuple[int, int, str | None]:
    """Quante sessioni del council sono arrivate INTERE nella finestra, e quando l'ultima.

    Questo, non il conteggio della run appena fatta, e' cio' su cui si allarma: il ritardo
    di ingestione si misura in minuti, la finestra in giorni, quindi un ritardo legittimo
    non puo' produrre un falso allarme. Se qui esce zero, l'ingestione via OpenRouter
    Broadcast e' ferma davvero — la spunta nell'account e' saltata, le chiavi sono cambiate,
    o il progetto Langfuse non e' piu' quello.
    """
    sessions: dict[str, int] = {}
    latest: str | None = None
    cursor = None
    for _ in range(MAX_PAGES):
        params = {
            "type": "GENERATION",
            "userId": COUNCIL_USER_ID,
            "fromStartTime": _window_start(days),
            "limit": "100",
            "fields": "core,basic",
        }
        if cursor:
            params["cursor"] = cursor
        page = _api_get(base_url, auth, params)
        for observation in page["data"]:
            session = observation.get("sessionId")
            if not session:
                continue
            sessions[session] = sessions.get(session, 0) + 1
            start = observation.get("startTime")
            if start and (latest is None or start > latest):
                latest = start
        cursor = (page.get("meta") or {}).get("cursor")
        if not cursor:
            break
    complete = sum(1 for n in sessions.values() if n >= CALLS_PER_RUN)
    return complete, len(sessions), latest


def recent_spend(base_url: str, auth: str, days: int) -> tuple[float, int]:
    """Spesa e numero di chiamate della finestra: visibilita', non un gate.

    Il controllo che BLOCCA e' il tetto di spesa sulla chiave OpenRouter. Una soglia qui
    sarebbe teatro: la spesa misurata ad agosto 2026 e' $0,22 sul mese contro un tetto di
    $5, e per farla scattare servirebbe un fattore venti che si vede prima altrove.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    total, calls, cursor = 0.0, 0, None
    for _ in range(MAX_PAGES):
        params = {
            "type": "GENERATION",
            "fromStartTime": since,
            "limit": "100",
            "fields": "core,usage",
        }
        if cursor:
            params["cursor"] = cursor
        page = _api_get(base_url, auth, params)
        for observation in page["data"]:
            total += float(observation.get("totalCost") or 0.0)
            calls += 1
        cursor = (page.get("meta") or {}).get("cursor")
        if not cursor:
            break
    return total, calls


def main() -> int:
    stderr_path = sys.argv[1] if len(sys.argv) > 1 else ""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print(f"::warning::{', '.join(missing)} assenti — controllo dell'ingestione saltato")
        return 0

    session_id, sent = read_telemetry(stderr_path)
    if not session_id:
        print("::warning::nessun trace_id nella telemetria — il council non ha emesso nulla")
        return 0

    base_url = os.environ["LANGFUSE_BASE_URL"]
    auth = base64.b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
    ).decode()

    arrived, run_cost = 0, 0.0
    try:
        for attempt in range(1, POLL_ATTEMPTS + 1):
            arrived, run_cost = count_arrived(base_url, auth, session_id)
            if arrived >= sent:
                break
            if attempt < POLL_ATTEMPTS:
                time.sleep(POLL_INTERVAL_S)
        complete, sessions, latest = complete_runs_since(base_url, auth, FRESHNESS_WINDOW_DAYS)
        spend, calls = recent_spend(base_url, auth, SPEND_WINDOW_DAYS)
    # `Exception` e non un elenco di tipi, e qui e' la scelta giusta invece della pigra.
    # La prima stesura elencava URLError/OSError/KeyError/ValueError e lasciava fuori
    # TypeError: `{"data": null}` o un `totalCost` che arriva come stringa — e nel campione
    # delle docs Langfuse i campi prezzo SONO stringhe — facevano uscire il processo con un
    # traceback, cioe' job rosso, cioe' falso allarme sul council piu' uno `status=error`
    # spedito a Sentry. Il contratto di questo file e' "non fallire mai": un contratto
    # scritto come elenco di tipi vale solo per i guasti gia' immaginati.
    # Si stampa il NOME del tipo e mai `str(exc)`: il messaggio potrebbe contenere l'URL.
    except Exception as exc:  # noqa: BLE001 — vedi sopra: fallire qui e' il difetto
        # Qui si sorveglia la sentinella, non si e' la sentinella: se Langfuse non
        # risponde, il verdetto sul council resta quello dello step precedente.
        print(f"::warning::Langfuse non interrogabile ({type(exc).__name__}) — verdetto invariato")
        return 0

    delay_note = (
        ""
        if arrived >= sent
        else f"  (non ancora dopo {POLL_BUDGET_S}s: l'ingestione puo' ritardare fino a 15 min)"
    )
    print(f"sessione        {session_id}")
    print(f"partite         {sent} chiamate riuscite secondo la telemetria del council")
    print(f"arrivate        {arrived} generazioni su Langfuse{delay_note}")
    print(f"costo run       ${run_cost:.6f}")
    print(f"ultimi {FRESHNESS_WINDOW_DAYS}gg      {complete} run complete su {sessions} sessioni")
    print(f"ultima traccia  {latest or 'nessuna'}")
    print(f"spesa {SPEND_WINDOW_DAYS}gg     ${spend:.4f} su {calls} chiamate")

    # L'UNICO allarme, e sta qui e non sul conteggio della run appena fatta: quello puo'
    # essere basso per un ritardo legittimo, questo no.
    if complete == 0:
        print(
            f"::warning::nessuna run del council arrivata intera a Langfuse negli ultimi "
            f"{FRESHNESS_WINDOW_DAYS} giorni ({sessions} sessioni parziali viste). "
            f"L'ingestione passa da OpenRouter Broadcast, che e' una spunta nell'account "
            f"e non vive in questo repo: controlla Settings > Observability."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
