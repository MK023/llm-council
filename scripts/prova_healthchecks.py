#!/usr/bin/env python3
# Il banco del self-check di scripts/healthchecks.py.
#
# PERCHE' ESISTE. Il self-check e' l'unica cosa che tiene onesta la soppressione zizmor
# in healthchecks.yml e l'unica che si accorge di un cron nuovo rimasto senza check. Un
# gate che non e' mai stato visto diventare rosso non e' un gate: e' una riga che passa.
# Questo repository lo ha gia' imparato due volte nello stesso giorno — il gate
# sull'esclusione di mutmut controllava il lato sbagliato della regola, e quello dopo
# aveva un predicato che copriva un caso su quattro. Entrambi passavano.
#
# Qui ogni controllo viene esercitato su una realta' rotta apposta, in una copia
# usa-e-getta dei workflow, e si pretende il rosso.
#
# E NON BASTA IL ROSSO. Due casi — il filtro sullo schedule e il margine della finestra
# — vengono provati anche al contrario: si spegne il controllo e si pretende che il caso
# torni VERDE. Un caso che resta rosso anche senza il controllo che dovrebbe esercitarlo
# sta misurando qualcos'altro, e non se ne accorgerebbe nessuno.
#
# IL CASO PIU' IMPORTANTE E' IL SECONDO, la condizione NEUTRALIZZATA: `always() || <il
# filtro>` contiene il filtro e non filtra niente. Un self-check che cercasse la
# sottostringa lo accetterebbe, e il monitor direbbe "vivo" per un cron che non parte
# piu' da solo.
#
# Niente rete, niente segreti: gira su ogni PR dentro ci.yml.
#
#   python3 scripts/prova_healthchecks.py

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import shutil
import sys
import tempfile
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent


def carica_modulo():
    percorso = RADICE / "scripts" / "healthchecks.py"
    spec = importlib.util.spec_from_file_location("healthchecks_sotto_esame", percorso)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def esegui(modulo, workflows: Path, checks) -> tuple[int, str]:
    """Lancia verifica() contro una copia dei workflow, catturando cio' che dice."""
    modulo.WORKFLOWS = workflows
    modulo.WATCHER = workflows / "healthchecks.yml"
    modulo.CHECKS = checks
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        esito = modulo.verifica()
    return esito, buffer.getvalue()


def copia_workflow(base: Path, nome: str) -> Path:
    destinazione = base / nome
    shutil.copytree(RADICE / ".github" / "workflows", destinazione)
    return destinazione


ESITI: list[tuple[str, bool]] = []


def pretendi(condizione: bool, messaggio: str) -> None:
    """La mutazione DEVE applicarsi.

    Se il testo da rompere non c'e' piu' — un refactor del watcher, una riga
    riformattata — il caso girerebbe su un file intatto e passerebbe verde senza aver
    provato niente. Meglio un'esplosione che nomina il punto.
    """
    if not condizione:
        raise RuntimeError(messaggio)


def caso(nome: str, condizione: bool) -> None:
    ESITI.append((nome, condizione))
    print(f"  {'ok   ' if condizione else 'ROTTO'} {nome}")


def sostituisci(percorso: Path, vecchio: str, nuovo: str) -> None:
    testo = percorso.read_text(encoding="utf-8")
    pretendi(testo.count(vecchio) == 1, f"{percorso.name}: {vecchio!r} non trovato una volta sola")
    percorso.write_text(testo.replace(vecchio, nuovo), encoding="utf-8")


def main() -> int:
    modulo = carica_modulo()
    originali = copy.deepcopy(modulo.CHECKS)

    print("banco del self-check di healthchecks.py\n")

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        # Il controllo zero: sulla realta' intatta il gate deve TACERE. Senza questo, un
        # self-check che dicesse sempre rosso passerebbe ogni caso qui sotto.
        w = copia_workflow(base, "intatta")
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("la realta' intatta passa", esito == 0)

        # 1. Il filtro sullo schedule RIMOSSO.
        w = copia_workflow(base, "senza-filtro")
        sostituisci(w / "healthchecks.yml", f"    if: {modulo.CONDIZIONE_ATTESA}\n", "")
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("il filtro sullo schedule rimosso e' rosso", esito != 0)

        # 2. Il filtro NEUTRALIZZATO. Contiene la condizione attesa e non filtra niente:
        # e' il caso che una ricerca per sottostringa accetterebbe.
        w = copia_workflow(base, "filtro-neutro")
        sostituisci(
            w / "healthchecks.yml",
            f"    if: {modulo.CONDIZIONE_ATTESA}",
            f"    if: always() || {modulo.CONDIZIONE_ATTESA}",
        )
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("il filtro neutralizzato con `always() ||` e' rosso", esito != 0)

        # 2-bis. LA MUTAZIONE DI CONTROLLO: spento il confronto sulla condizione, il caso
        # sopra deve tornare VERDE. Se restasse rosso, starebbe misurando altro.
        w = copia_workflow(base, "filtro-neutro-controllo")
        sostituisci(
            w / "healthchecks.yml",
            f"    if: {modulo.CONDIZIONE_ATTESA}",
            f"    if: always() || {modulo.CONDIZIONE_ATTESA}",
        )
        atteso = modulo.CONDIZIONE_ATTESA
        modulo.CONDIZIONE_ATTESA = f"always() || {atteso}"
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        modulo.CONDIZIONE_ATTESA = atteso
        caso("...e torna verde se si spegne il controllo sulla condizione", esito == 0)

        # 3. `permissions: {}` a livello di workflow rimosso: il job girerebbe con i
        # permessi di default sul ramo di default, con i segreti.
        w = copia_workflow(base, "senza-permessi")
        sostituisci(w / "healthchecks.yml", "\npermissions: {}\n", "\n")
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("`permissions: {}` rimosso e' rosso", esito != 0)

        # 4. Uno step che usa codice di terzi: il primo passo dell'exploit workflow_run
        # che la soppressione zizmor dichiara impossibile qui dentro.
        w = copia_workflow(base, "con-uses")
        sostituisci(
            w / "healthchecks.yml",
            "      - name: Ping\n",
            "      - uses: actions/checkout@v4\n      - name: Ping\n",
        )
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("uno step con `uses:` e' rosso", esito != 0)

        # 5. Un cron nuovo senza check: e' lo stato in cui `mutation.yml` e' rimasto
        # fino al 2026-09-04, sorvegliato da niente.
        w = copia_workflow(base, "cron-orfano")
        (w / "nuovo-cron.yml").write_text(
            "name: Nuovo cron\non:\n  schedule:\n    - cron: '0 5 * * 2'\njobs: {}\n",
            encoding="utf-8",
        )
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("un cron nuovo senza check e' rosso", esito != 0)

        # 6. Lo slug che non corrisponde al nome del file: il battito finirebbe su un
        # check inesistente e healthchecks risponderebbe 404 senza che nessuno guardi.
        w = copia_workflow(base, "slug-storto")
        rotti = copy.deepcopy(originali)
        rotti[0]["slug"] = "e2e-settimanale"
        esito, _ = esegui(modulo, w, rotti)
        caso("uno slug diverso dal nome del file e' rosso", esito != 0)

        # 7. La finestra sotto il divario misurato: l'allarme suonerebbe su un ritardo
        # normale di GitHub, e si imparerebbe a ignorarlo.
        w = copia_workflow(base, "finestra-corta")
        rotti = copy.deepcopy(originali)
        rotti[0]["timeout"] = modulo.GIORNO
        rotti[0]["grace"] = modulo.ORA
        esito, _ = esegui(modulo, w, rotti)
        caso("una finestra sotto il divario misurato e' rossa", esito != 0)

        # 7-bis. LA MUTAZIONE DI CONTROLLO della finestra: con un divario dichiarato
        # zero il caso sopra deve tornare verde.
        w = copia_workflow(base, "finestra-corta-controllo")
        rotti = copy.deepcopy(originali)
        rotti[0]["timeout"] = modulo.GIORNO
        rotti[0]["grace"] = modulo.ORA
        rotti[0]["gap_h"] = 0.0
        esito, _ = esegui(modulo, w, rotti)
        caso("...e torna verde se il divario misurato e' zero", esito == 0)

        # 8. Il workflow c'e', il check c'e', ma il nome non e' nell'elenco
        # `workflow_run`: quel cron non manda nessun battito e nessuno se ne accorge.
        w = copia_workflow(base, "fuori-elenco")
        sostituisci(w / "healthchecks.yml", "      - Mutation testing\n", "")
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("un cron fuori dall'elenco `workflow_run` e' rosso", esito != 0)

        # 9. IL SEGRETO IN PIU'. E' la claim che regge tutta la soppressione zizmor —
        # "l'unico segreto presente e' la ping key, che sa solo dire sono vivo" — ed era
        # l'unica delle otto senza controllo: una security review del 2026-09-04 ha
        # mostrato che bastava aggiungere un `secrets.*` all'`env:` per rendere falsa la
        # frase su cui si regge il ragionamento, con il tripwire verde.
        w = copia_workflow(base, "segreto-in-piu")
        sostituisci(
            w / "healthchecks.yml",
            "          PING_KEY: ${{ secrets.HEALTHCHECKS_PING_KEY }}\n",
            "          PING_KEY: ${{ secrets.HEALTHCHECKS_PING_KEY }}\n"
            "          ALTRO: ${{ secrets.OPENROUTER_API_KEY }}\n",
        )
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("un secondo segreto nell'env del battito e' rosso", esito != 0)

        # 10. UN TRIGGER IN PIU'. La riga `# zizmor: ignore[dangerous-triggers]` sta sulla
        # chiave `on:` e coprirebbe anche un trigger aggiunto li' accanto: un
        # `pull_request_target:` erediterebbe una soppressione scritta per un altro
        # rischio.
        w = copia_workflow(base, "trigger-in-piu")
        sostituisci(
            w / "healthchecks.yml",
            "  workflow_run:\n",
            "  pull_request_target:\n  workflow_run:\n",
        )
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("un trigger oltre a `workflow_run` e' rosso", esito != 0)

        # 11. UN'ESPRESSIONE DENTRO `run:`. E' la difesa vera contro la template
        # injection, dichiarata in un commento e fino ad oggi non sorvegliata.
        w = copia_workflow(base, "iniezione")
        sostituisci(
            w / "healthchecks.yml",
            "          set -euo pipefail\n",
            "          set -euo pipefail\n"
            "          echo ${{ github.event.workflow_run.head_branch }}\n",
        )
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("un `${{ }}` dentro `run:` e' rosso", esito != 0)

        # 12. LE RIGHE ORFANE. Il difetto realmente accaduto il 2026-09-04: rimuovendo
        # uno step da `e2e.yml` erano rimaste indietro nove righe del suo corpo shell, il
        # file aveva smesso di essere YAML — GitHub avrebbe rifiutato il workflow e il
        # cron non sarebbe piu' partito — e il self-check era passato VERDE.
        w = copia_workflow(base, "yaml-rotto")
        sostituisci(
            w / "e2e.yml",
            "jobs:\n",
            "jobs:\n          exit 0\n",
        )
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("un workflow sorvegliato che non e' piu' YAML e' rosso", esito != 0)

        # 13. LO SLUG CHE NON TOGLIE L'ESTENSIONE. Cercato nel CORPO del job e non nel
        # file intero: sul testo completo bastava un commento contenente `%.yml` per
        # tenere verde la guardia mentre la riga era sparita.
        w = copia_workflow(base, "slug-con-estensione")
        sostituisci(w / "healthchecks.yml", "          slug=${slug%.yaml}\n", "")
        esito, _ = esegui(modulo, w, copy.deepcopy(originali))
        caso("lo slug che non toglie `.yaml` e' rosso", esito != 0)

        # 14-16. Le tre porte per far girare codice di terzi che non sono uno step, piu'
        # il timeout e i permessi del job: guardie che esistevano e non erano mai state
        # viste diventare rosse.
        for etichetta, vecchio, nuovo in (
            (
                "`container:` sul job",
                "    timeout-minutes: 5\n",
                "    container: node:20\n    timeout-minutes: 5\n",
            ),
            (
                "`services:` sul job",
                "    timeout-minutes: 5\n",
                "    services:\n      db: postgres\n    timeout-minutes: 5\n",
            ),
            ("`timeout-minutes` rimosso", "    timeout-minutes: 5\n", ""),
            ("`permissions: {}` del job rimosso", "    permissions: {}\n", ""),
        ):
            w = copia_workflow(
                base, "porta-" + etichetta.replace(" ", "-").replace("`", "").replace(":", "")
            )
            sostituisci(w / "healthchecks.yml", vecchio, nuovo)
            esito, _ = esegui(modulo, w, copy.deepcopy(originali))
            caso(f"{etichetta} e' rosso", esito != 0)

    falliti = [nome for nome, ok in ESITI if not ok]
    print(f"\n{len(ESITI) - len(falliti)}/{len(ESITI)} casi")
    if falliti:
        for nome in falliti:
            print(f"::error::il banco ha trovato un buco nel self-check: {nome}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
