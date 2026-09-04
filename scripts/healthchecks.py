#!/usr/bin/env python3
# I check healthchecks.io come codice: la tabella CHECKS qui sotto E' la configurazione.
#
# PERCHE' ESISTE QUESTO FILE. Questo repository ha due cron settimanali e, fino al
# 2026-09-04, nessuno dei due era sorvegliato davvero. `e2e.yml` spediva un check-in a
# un cron monitor Sentry `disabled` che non aveva MAI ricevuto un battito — il piano
# gratuito ne ammette uno attivo per account e il posto era di un altro progetto — e
# `mutation.yml` non era sorvegliato ne' da Sentry ne' dalla sentinella esterna nel repo
# del sito. Il caso che nessuno dei due copriva e' lo stesso: GitHub disabilita gli
# schedule dopo 60 giorni di inattivita' del repository, in silenzio, e qui si lavora a
# sprint. Serve qualcosa FUORI da GitHub che si accorga del silenzio.
#
# PERCHE' NON BASTA `?create=1`. L'URL di ping sa creare un check al primo battito, ma
# la documentazione e' esplicita: dall'URL non si possono impostare periodo e grace. I
# check nati cosi' prendono i default del progetto, che per entrambi questi sono
# sbagliati. La configurazione passa quindi dalla Management API, che ha l'upsert
# idempotente su `unique: ["slug"]`: rilanciare `--apply` non duplica niente e riallinea
# cio' che qualcuno avesse cambiato dalla dashboard.
#
# DUE CHIAVI, DUE POTERI, E QUI NE VIVE UNA SOLA. La *ping key* sa dire "sono vivo" e
# nient'altro: sta nei secret di GitHub perche' il watcher deve pingare. La *API key*
# puo' allungare un `grace` fino a rendere cieco l'allarme, e sta SOLO in Doppler:
# `--apply` lo lancia una persona dalla macchina, mai la CI. E' deliberato che questo
# repository non abbia nessuno step che chiami `--apply`: la CI sorvegliata da questi
# check non deve poterli riconfigurare.
#
# UN PROGETTO healthchecks.io PER REPOSITORY. Gli slug sono unici dentro un progetto, e
# `mutation` esiste gia' in agentic-os: con un progetto condiviso il ping avrebbe avuto
# in risposta `409 slug ambiguo`, che il watcher trasforma in un ::warning:: lasciando
# il job VERDE. La convenzione "slug = nome del file" rende le collisioni probabili, non
# rare, quindi la separazione e' strutturale e non una precauzione.
#
# DA DOVE VENGONO timeout E grace. Non sono stime: `gap_h` e' il divario massimo
# osservato fra due run SCHEDULATE consecutive, letto dall'API di GitHub il 2026-09-04.
# `timeout + grace` deve superarlo con margine, altrimenti l'allarme suona su un ritardo
# normale di GitHub e si impara a ignorarlo. Lo scheduling di GitHub e' best-effort
# dichiarato.
#
# SOLO STDLIB, come ogni altro script di questo repository. La versione da cui questo
# file discende (agentic-os) usa PyYAML; qui i workflow si leggono come testo, perche'
# `pin_dev_deps.py` dichiara "stdlib-only, like the package it serves" e un gate che
# gira solo dopo un `pip install` e' un gate che qualcuno salta. Le proprieta' sotto
# esame sono testuali per natura — la presenza di un filtro, l'assenza di un `uses:` —
# e nessuna di esse ha bisogno di un albero.
#
#   python3 scripts/healthchecks.py --self-check   # niente rete, niente segreti
#   doppler run -p llm-council -c prd -- python3 scripts/healthchecks.py --apply
#   doppler run -p llm-council -c prd -- python3 scripts/healthchecks.py --apply --dry-run

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
WORKFLOWS = RADICE / ".github" / "workflows"
WATCHER = WORKFLOWS / "healthchecks.yml"

# La RADICE dell'API, non la collezione: comporre `checks/../channels/` funziona solo
# finche' un proxy normalizza i dot-segment, e urllib non lo fa.
API = "https://healthchecks.io/api/v3/"

# Scritta per intero, non cercata come sottostringa: `if: always() || <questa>` contiene
# il filtro e non filtra niente.
CONDIZIONE_ATTESA = "github.event.workflow_run.event == 'schedule'"

ORA = 3600
GIORNO = 24 * ORA

CHECKS = [
    {
        "slug": "e2e",
        "workflow": "e2e.yml",
        "nome": "E2E settimanale",
        "timeout": 7 * GIORNO,
        "grace": 24 * ORA,
        "gap_h": 174.0,
        "campioni": 6,
        "desc": "La run reale contro OpenRouter, lunedi 06:00 UTC. Sostituisce un cron monitor Sentry che era disabled e non aveva mai ricevuto un battito.",
    },
    {
        "slug": "mutation",
        "workflow": "mutation.yml",
        "nome": "Mutation testing",
        "timeout": 7 * GIORNO,
        # 36h e non 24 come per e2e: quattro campioni sono pochi per fidarsi di un
        # massimo, e questo cron gira alle 03:00 UTC, quando la coda dei runner e' meno
        # prevedibile. Dodici ore in piu' su un allarme settimanale non costano niente e
        # tolgono il caso in cui il primo falso positivo insegna a ignorare il monitor.
        "grace": 36 * ORA,
        "gap_h": 178.1,
        "campioni": 4,
        "desc": "Il gate di mutation testing, giovedi 03:00 UTC. Prima del 2026-09-04 non era sorvegliato da niente.",
    },
]


def _leggi(percorso: Path) -> str | None:
    try:
        return percorso.read_text(encoding="utf-8")
    except OSError:
        return None


def _righe_blocco(testo: str, intestazione: str) -> list[str]:
    """Le righe indentate sotto `intestazione`, fino al primo rientro a sinistra.

    Sostituisce l'accesso a un albero YAML per i pochi blocchi che questo file guarda.
    L'indentazione dell'intestazione fa da soglia: si raccoglie finche' le righe sono
    piu' indentate di lei, saltando quelle vuote e i commenti.
    """
    righe = testo.splitlines()
    for i, riga in enumerate(righe):
        if riga.strip().startswith(intestazione):
            soglia = len(riga) - len(riga.lstrip())
            dentro = []
            for successiva in righe[i + 1 :]:
                if not successiva.strip() or successiva.lstrip().startswith("#"):
                    continue
                if len(successiva) - len(successiva.lstrip()) <= soglia:
                    break
                dentro.append(successiva)
            return dentro
    return []


def nome_workflow(file: str) -> str | None:
    """Il valore di `name:` a colonna zero, che e' come GitHub nomina il workflow."""
    testo = _leggi(WORKFLOWS / file)
    if testo is None:
        return None
    trovato = re.search(r"^name:[ \t]*(.+?)[ \t]*$", testo, re.MULTILINE)
    return trovato.group(1) if trovato else None


def elenco_workflow_run() -> list[str]:
    """I workflow che il watcher dichiara di sorvegliare, per NOME.

    `workflow_run.workflows` vuole i nomi, non i file: e' l'unico punto del disegno in
    cui la convenzione "slug = nome del file" non vale, ed e' per questo che il
    self-check confronta le due cose invece di fidarsi.
    """
    testo = _leggi(WATCHER)
    if testo is None:
        return []
    return [
        riga.strip()[2:].strip()
        for riga in _righe_blocco(testo, "workflows:")
        if riga.strip().startswith("- ")
    ]


def forma_watcher() -> list[str]:
    """Il tripwire sulla soppressione zizmor.

    `workflow_run` e' HIGH per categoria: gira sul ramo di default CON i segreti,
    svegliato da workflow che possono essere partiti da una PR di un fork — e questo
    repository e' pubblico, quindi il caso non e' teorico. L'exploit noto e' in tre
    passi: la PR del fork produce un artefatto, il workflow privilegiato lo scarica, lo
    esegue con i segreti in mano.

    La soppressione nel watcher e' legittima perche' nessuno dei tre passi esiste li'
    dentro. Ma copre QUELLA FORMA, non il trigger: se un domani quel job acquisisse un
    checkout, un artefatto o dei permessi, la riga continuerebbe a tacere su un rischio
    diventato reale. Quindi la giustificazione si verifica invece di leggersi.
    """
    testo = _leggi(WATCHER)
    if testo is None:
        return [f"{WATCHER.name} non trovato: il watcher e' stato rinominato o rimosso"]

    errori: list[str] = []

    if not re.search(r"^permissions:[ \t]*\{\}[ \t]*$", testo, re.MULTILINE):
        errori.append(f"{WATCHER.name}: manca `permissions: {{}}` a livello di workflow")

    # Un job solo, e si chiama battito. Non e' rigidita' fine a se stessa: ogni job in
    # piu' qui dentro gira sul ramo di default con i segreti del repository.
    jobs = [
        riga.strip().rstrip(":")
        for riga in _righe_blocco(testo, "jobs:")
        if re.match(r"^  [A-Za-z0-9_-]+:[ \t]*$", riga)
    ]
    if jobs != ["battito"]:
        errori.append(f"{WATCHER.name}: i job non sono piu' esattamente ['battito'] ma {jobs}")
        return errori

    battito = "\n".join(_righe_blocco(testo, "battito:"))

    if not re.search(r"^ +permissions:[ \t]*\{\}[ \t]*$", battito, re.MULTILINE):
        errori.append("battito: non ha piu' `permissions: {}`")

    if not re.search(r"^ +timeout-minutes:", battito, re.MULTILINE):
        errori.append("battito: non dichiara `timeout-minutes`")

    # Il filtro sullo schedule e' IL controllo che tiene in piedi il disegno.
    # `workflow_run` scatta per ogni trigger, non solo per lo schedule: senza questo
    # filtro un `workflow_dispatch` manuale manderebbe un battito e il monitor direbbe
    # "vivo" per un cron che non parte piu' da solo. E' il modo di fallire piu'
    # insidioso, perche' lascia il verde.
    #
    # L'ORACOLO STA QUI, NON NEL FILE SOTTO ESAME: cercare la sottostringa accetterebbe
    # `if: always() || github.event.workflow_run.event == 'schedule'`, che contiene il
    # filtro e non filtra niente. La condizione attesa si scrive per intero.
    trovata = re.search(r"^ +if:[ \t]*(.+?)[ \t]*$", battito, re.MULTILINE)
    condizione = trovata.group(1) if trovata else ""
    if condizione != CONDIZIONE_ATTESA:
        errori.append("battito: la condizione del job non e' piu' quella attesa")
        errori.append(f"  attesa:  {CONDIZIONE_ATTESA}")
        errori.append(f"  trovata: {condizione or '(nessuna)'}")

    # QUATTRO PORTE, NON UNA. Il commento nel watcher promette "niente `uses:` di nessun
    # tipo", e guardare solo gli step ne terrebbe aperte tre: `jobs.battito.uses` e' una
    # reusable workflow, che con `secrets: inherit` riceve ogni segreto del repository;
    # `container:` e `services:` fanno girare un'immagine di terzi CON l'ambiente del
    # job dentro.
    # `- uses:` per uno step, `uses:` per una reusable workflow: DUE forme, e la prima
    # stesura vedeva solo la seconda. Il banco l'ha trovato al primo giro — uno step
    # `- uses: actions/checkout@v4` passava indisturbato, cioe' proprio il primo passo
    # dell'exploit che la soppressione zizmor dichiara impossibile qui dentro.
    for chiave in ("uses", "container", "services"):
        if re.search(rf"^ +(- )?{chiave}:", battito, re.MULTILINE):
            errori.append(
                f"battito: dichiara `{chiave}:`, che il commento sulla soppressione zizmor "
                "dichiara assente"
            )

    if "download-artifact" in testo or "gh run download" in testo:
        errori.append("battito: scaricare artefatti e' il vettore dell'exploit workflow_run")

    # LA CLAIM CHE REGGE TUTTA LA SOPPRESSIONE, e fino al 2026-09-04 era l'unica senza
    # controllo. Il commento dice "l'unico segreto presente e' la ping key, che sa solo
    # dire sono vivo": e' cio' che rende accettabile far girare questo job sul ramo di
    # default con i segreti. Aggiungere `X: ${{ secrets.OPENROUTER_API_KEY }}` all'`env:`
    # dello step passava il tripwire verde e rendeva falsa la frase su cui si regge il
    # ragionamento. Trovato da una security review del cambiamento che introduce questo
    # file.
    segreti = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", testo))
    if segreti != {"HEALTHCHECKS_PING_KEY"}:
        errori.append(
            f"battito: i segreti raggiungibili sono {sorted(segreti)}, non solo la ping key. "
            "La soppressione zizmor e' argomentata sul fatto che l'unica credenziale a "
            "portata sappia solo dire 'sono vivo'"
        )

    # SOLO `workflow_run`. La riga `# zizmor: ignore[dangerous-triggers]` sta sulla chiave
    # `on:` e coprirebbe anche un trigger nuovo aggiunto li' accanto — un
    # `pull_request_target:` erediterebbe la soppressione scritta per un altro rischio.
    trigger = [
        riga.strip().rstrip(":")
        for riga in _righe_blocco(testo, "on:")
        if re.match(r"^  [a-z_]+:[ \t]*$", riga)
    ]
    if trigger != ["workflow_run"]:
        errori.append(f"{WATCHER.name}: i trigger non sono piu' solo ['workflow_run'] ma {trigger}")

    # NIENTE `${{ }}` DENTRO `run:`. E' la difesa vera contro la template injection — i
    # dati dell'evento passano da `env:` e nel corpo si leggono come variabili di shell —
    # ed era dichiarata in un commento senza che niente la tenesse vera.
    dentro_run = False
    for riga in testo.splitlines():
        if re.match(r"^ +run: \|", riga):
            dentro_run = True
            continue
        if dentro_run:
            if riga.strip() and not riga.startswith("          "):
                dentro_run = False
            elif "${{" in riga:
                errori.append(
                    "battito: un'espressione `${{ }}` compare dentro `run:`. I dati "
                    "dell'evento passano da `env:`, o diventano iniezione"
                )
                break

    # ENTRAMBE le estensioni. GitHub accetta `.yml` e `.yaml`: se il watcher togliesse
    # solo `.yml`, un cron chiamato `backup.yaml` pingherebbe lo slug `backup.yaml` per
    # un check di nome `backup`. Risposta 404, cioe' un ::warning:: e il job VERDE,
    # mentre il check non riceve mai un battito e allarma per sempre.
    # Cercati nel CORPO del job, non nel file intero: una revisione ha fatto notare che
    # su `testo` bastava un commento contenente `%.yml` per tenere verde la guardia
    # mentre la riga che toglie l'estensione era sparita dallo script.
    for suffisso in ("%.yml", "%.yaml"):
        if suffisso not in battito:
            errori.append(
                f"battito: lo slug non toglie `{suffisso[1:]}` dal nome del file: "
                "un cron con l'altra estensione pingherebbe uno slug inesistente"
            )

    return errori


def righe_orfane(testo: str) -> list[str]:
    """Righe che non appartengono a nessuna chiave: il file non e' piu' YAML valido.

    Questo file legge i workflow come testo, e una revisione del 2026-09-04 ha mostrato
    il buco che quella scelta apre: rimuovendo uno step si erano lasciate indietro nove
    righe del suo corpo shell, `e2e.yml` aveva smesso di essere YAML — quindi GitHub
    avrebbe rifiutato il workflow e il cron non sarebbe piu' partito — e il self-check
    era passato VERDE, perche' `schedule:` e `name:` c'erano ancora. Il gate che esiste
    per garantire che ogni cron sia sorvegliato non vedeva un cron che GitHub non
    avrebbe caricato.

    Il controllo non e' un parser: fuori da un blocco scalare (`|`, `>`) ogni riga di un
    workflow e' `chiave:` o `- elemento`. Le nove righe orfane erano `fi`, `stato=ok`,
    `curl ...`, `exit 0` — nessuna delle due forme.
    """
    orfane = []
    indent_blocco: int | None = None
    for numero, riga in enumerate(testo.splitlines(), 1):
        if not riga.strip() or riga.lstrip().startswith("#"):
            continue
        indentazione = len(riga) - len(riga.lstrip())
        if indent_blocco is not None:
            if indentazione > indent_blocco:
                continue
            indent_blocco = None
        contenuto = riga.strip()
        if re.search(r"[:>|]\s*(\|-?|>-?)?\s*$", contenuto) and re.match(
            r"^(- )?[A-Za-z0-9_.\-\"' ]+:", contenuto
        ):
            if re.search(r"[|>]-?\s*$", contenuto):
                indent_blocco = indentazione
            continue
        if re.match(r"^(- )?[A-Za-z0-9_.\-\"' ]+:", contenuto) or contenuto.startswith("- "):
            continue
        orfane.append(f"riga {numero}: {contenuto[:60]}")
    return orfane


def workflow_schedulati() -> list[str]:
    """I file di workflow che dichiarano uno `schedule:`, cioe' cio' che va sorvegliato."""
    trovati = []
    for percorso in sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml"))):
        testo = _leggi(percorso) or ""
        if any(riga.strip().startswith("schedule:") for riga in testo.splitlines()):
            trovati.append(percorso.name)
    return trovati


def verifica() -> int:
    """Il gate. Niente rete, niente segreti: gira su ogni PR."""
    errori: list[str] = []
    visti: set[str] = set()

    dichiarati = elenco_workflow_run()

    for c in CHECKS:
        visti.add(c["workflow"])

        if not (WORKFLOWS / c["workflow"]).exists():
            errori.append(f"{c['slug']}: {c['workflow']} non esiste")
            continue

        orfane = righe_orfane(_leggi(WORKFLOWS / c["workflow"]) or "")
        if orfane:
            errori.append(
                f"{c['workflow']}: righe che non appartengono a nessuna chiave, il file "
                f"non e' piu' YAML valido e GitHub lo rifiuterebbe — {orfane[0]}"
            )

        # La convenzione su cui si regge il ping: il watcher ricava lo slug dal nome del
        # file. Se i due divergono il battito finisce su un check inesistente e
        # healthchecks risponde 404 senza che nessuno guardi.
        atteso = c["workflow"].rsplit(".", 1)[0]
        if c["slug"] != atteso:
            errori.append(
                f"{c['slug']}: lo slug non corrisponde al nome del file ({atteso}): "
                "il watcher pingherebbe un check che non esiste"
            )

        # Il nome, non il file: `workflow_run.workflows` vuole i nomi, ed e' l'unico
        # punto in cui le due convenzioni si incontrano.
        nome = nome_workflow(c["workflow"])
        if nome != c["nome"]:
            errori.append(
                f"{c['slug']}: `name:` in {c['workflow']} e' {nome!r}, la tabella dice {c['nome']!r}"
            )
        elif nome not in dichiarati:
            errori.append(
                f"{c['slug']}: il nome {nome!r} non e' nell'elenco `workflow_run` di "
                f"{WATCHER.name}: quel cron non manda nessun battito"
            )

        # La finestra deve superare il divario MISURATO, non quello dichiarato dal cron.
        # Sotto, l'allarme suona su un ritardo normale di GitHub e si impara a
        # ignorarlo, che e' il modo in cui un monitor smette di essere un monitor.
        finestra_h = (c["timeout"] + c["grace"]) / ORA
        if finestra_h <= c["gap_h"]:
            errori.append(
                f"{c['slug']}: finestra {finestra_h:.0f}h non supera il divario misurato "
                f"{c['gap_h']:.1f}h ({c['campioni']} campioni)"
            )

    # Un cron schedulato che nessuno sorveglia e' esattamente il buco che questo file
    # chiude: `mutation.yml` e' stato in quello stato fino al 2026-09-04.
    for file in workflow_schedulati():
        if file not in visti:
            errori.append(f"{file} ha uno `schedule:` e nessun check in CHECKS")

    errori += forma_watcher()

    if errori:
        for e in errori:
            print(f"::error::{e}" if not e.startswith("  ") else e)
        return 1

    print(f"healthchecks: {len(CHECKS)} check coerenti con i cron e con il watcher")
    return 0


def _chiama(percorso: str, chiave: str, corpo: dict | None = None) -> tuple[int, object]:
    """Una sola porta verso healthchecks.io, cosi' l'URL non si compone altrove.

    `percorso` e' un suffisso costante scritto qui dentro (`checks/`, `channels/`):
    nessun valore che arrivi da fuori raggiunge questa stringa, e `API` porta schema e
    host gia' dentro.
    """
    dati = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    richiesta = urllib.request.Request(
        API + percorso,
        data=dati,
        headers={"X-Api-Key": chiave, "Content-Type": "application/json"},
        method="POST" if corpo is not None else "GET",
    )
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as risposta:
            grezzo = risposta.read()
            try:
                return risposta.status, json.loads(grezzo)
            except json.JSONDecodeError:
                # Una pagina di manutenzione o un proxy che risponde 200 con dell'HTML:
                # senza questo ramo il comando moriva con un traceback invece della
                # diagnosi, che e' esattamente cio' che il ramo `URLError` qui sotto
                # esiste per evitare.
                return risposta.status, grezzo.decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except urllib.error.URLError as e:
        # DNS giu', TLS rotto, healthchecks.io irraggiungibile. Senza questo ramo il
        # comando morirebbe con un traceback invece della diagnosi che stampa ogni altro
        # percorso di errore.
        return 0, f"connessione fallita: {e.reason}"


def applica(dry_run: bool) -> int:
    chiave = os.environ.get("HEALTHCHECKS_API_KEY")
    if not chiave:
        print(
            "::error::HEALTHCHECKS_API_KEY assente: sta in Doppler, usa "
            "`doppler run -p llm-council -c prd --`"
        )
        return 1

    # I check nascerebbero senza destinatario se non ci fosse nessuna integrazione, e un
    # allarme che non raggiunge nessuno e' indistinguibile da nessun allarme. E' lo
    # stato esatto in cui il cron monitor Sentry e' rimasto per tre settimane.
    codice, canali = _chiama("channels/", chiave)
    if codice != 200 or not isinstance(canali, dict):
        print(f"::error::lettura dei canali fallita (HTTP {codice}): {canali}")
        return 1
    if not (canali.get("channels") or []):
        print(
            "::error::nessuna integrazione su healthchecks.io: i check nascerebbero senza "
            "destinatario. Creane una (Email, Telegram, Slack) e rilancia"
        )
        return 1

    uscita = 0
    for c in CHECKS:
        corpo = {
            "name": c["slug"],
            "slug": c["slug"],
            "timeout": c["timeout"],
            "grace": c["grace"],
            "desc": f"{c['desc']} Divario massimo misurato {c['gap_h']:.1f}h su {c['campioni']} run.",
            # L'upsert e' su slug: rilanciare non duplica niente e riallinea cio' che
            # qualcuno avesse cambiato dalla dashboard.
            "unique": ["slug"],
            "channels": "*",
        }
        if dry_run:
            print(f"[dry-run] {c['slug']}: timeout={c['timeout']}s grace={c['grace']}s")
            continue
        codice, risposta = _chiama("checks/", chiave, corpo)
        if codice in (200, 201):
            print(f"{c['slug']}: {'aggiornato' if codice == 200 else 'creato'}")
        else:
            print(f"::error::{c['slug']}: HTTP {codice} — {risposta}")
            uscita = 1
    return uscita


def main() -> int:
    argomenti = sys.argv[1:]
    if "--self-check" in argomenti:
        return verifica()
    if "--apply" in argomenti:
        return applica("--dry-run" in argomenti)
    print("uso: healthchecks.py --self-check | --apply [--dry-run]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
