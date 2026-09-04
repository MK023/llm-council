"""The stdlib-only promise, checked against the manifest that actually declares it.

Until 2026-08-13 this invariant was defended in CI by `test ! -f requirements.txt`
— a file this project would never create, since its manifest is pyproject.toml.
Adding `dependencies = ["requests"]` passed every gate. The public case study on
marcobellingeri.dev says "zero runtime dependencies, no supply chain attack
surface"; that sentence now has something behind it.

Runtime only. Dev tools (ruff, coverage, zizmor) are a separate, hash-pinned
supply chain — see requirements-dev.txt.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

# Come in `test_reported_debt.py`: senza, lanciare questo file direttamente muore su
# `import council` invece di eseguire i due gate che ci vivono dentro.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import council  # noqa: E402

if sys.version_info >= (3, 11):
    import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent


def _restituisce_una_stringa(ritorno: ast.Return) -> bool:
    """Vero se il valore restituito e' una stringa o ne contiene una.

    Copre `return "X"`, `return "X" if c else "Y"` e `return f"..."`. Non prova a dire
    quale stringa: il punto e' che una funzione di presentazione non deve RESTITUIRE
    un'etichetta, non quale etichetta restituisca.
    """
    if ritorno.value is None:
        return False
    return any(
        isinstance(n, ast.JoinedStr) or (isinstance(n, ast.Constant) and isinstance(n.value, str))
        for n in ast.walk(ritorno.value)
    )


@unittest.skipUnless(sys.version_info >= (3, 11), "tomllib landed in 3.11")
class TestNoRuntimeDependencies(unittest.TestCase):
    """`python -m council` must run on a bare interpreter, with nothing installed."""

    def setUp(self) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as manifest:
            self.project = tomllib.load(manifest)["project"]

    def test_no_declared_dependencies(self) -> None:
        declared = self.project.get("dependencies", [])
        self.assertEqual(
            declared,
            [],
            f"pyproject declares runtime dependencies {declared}. The README, SECURITY.md "
            "and the public case study all claim there are none — change the claims first.",
        )

    def test_no_optional_dependency_groups(self) -> None:
        """An `[project.optional-dependencies]` extra is still a runtime import path.

        `pip install llm-council[something]` would pull it, and 'zero dependencies'
        would quietly become 'zero dependencies unless you ask nicely'.
        """
        extras = self.project.get("optional-dependencies", {})
        self.assertEqual(
            extras,
            {},
            f"pyproject declares optional dependency groups {sorted(extras)}.",
        )

    def test_no_bare_requirements_txt(self) -> None:
        """The old CI guard, kept but narrowed to what it actually means.

        A bare `requirements.txt` is what pip, Dependabot, Snyk and every human
        reader treat as "the runtime dependencies of this project". Its absence
        stays part of the contract even though it was never the real manifest.

        Suffixed files (`requirements-dev.txt`, `requirements-mutation.txt`) are
        tooling and are allowed: they are installed by CI jobs, never by anyone
        running the council.
        """
        self.assertFalse(
            (REPO_ROOT / "requirements.txt").exists(),
            "requirements.txt exists — runtime dependencies belong in pyproject, "
            "and this project has none. Name tooling files requirements-<what>.txt.",
        )


class TestTheMutationExclusionStaysNarrow(unittest.TestCase):
    """L'esclusione di `__main__.py` dal gate di mutation ha una condizione scritta, e
    fino al 2026-09-04 nessuno la controllava.

    `pyproject.toml` dice che l'esclusione e' difendibile *"solo finche' quel file e' ONLY
    presentation"*, e chiude con un'istruzione: **"If it happens again, move the code — do
    not widen the exclusion."** Era gia' successo il 2026-08-14 (`_is_truncated` e
    `_collect_failures` decidevano l'exit code da dentro il file non mutato) ed e' successo
    di nuovo con `_rank_status`, scoperto solo perche' qualcuno e' andato a mutarlo a mano:
    quattro mutazioni sopravvissute con la coverage di quel file al 100% di righe E branch.

    Il repo ha gia' registrato questa forma di difetto con le sue parole — *"A rule that
    only a human can check is a rule that is already broken somewhere"*. Servono DUE
    controlli, perche' la regola si puo' rompere da due lati e il primo che e' stato scritto
    ne copriva uno solo:

      - allargando la lista attorno al codice nuovo  -> il test sulla lista;
      - lasciando entrare della logica nel file gia' escluso, lista intatta -> il test
        sui `return` letterali. **E' questo il lato da cui si e' rotta entrambe le volte**,
        e una revisione del 2026-09-04 ha fatto notare che il primo test lo mancava del
        tutto pur dichiarando di chiuderlo.
    """

    def test_the_exempt_module_classifies_nothing(self) -> None:
        """Nessuna funzione di `__main__.py` restituisce un letterale stringa.

        E' il criterio "ONLY presentation" reso eseguibile. Una funzione che stampa
        restituisce `None` o un conteggio; una che restituisce una STRINGA COSTANTE sta
        assegnando una categoria — e una categoria decide qualcosa, qui persino quale meta'
        di un votante finisce sotto gli occhi di chi legge. Entrambe le volte che questa
        regola si e' rotta, il difetto aveva esattamente questa forma: `_rank_status`
        restituiva `"FAILED"` / `"MALFORMED"` / `"OK"`, e la classificazione di Stage 1
        restituiva `"FAILED"` / `"TRUNCATED"` / `"OK"`.

        Letto con `ast` e non con una grep: una grep su `return "` non vede un ritorno
        spezzato su piu' righe dal formatter, ed e' esattamente cosi' che il gate sui writer
        a stderr in `test_reported_debt.py` era gia' stato aggirato una volta.

        La prima stesura cercava solo `return <costante>`, e una revisione ha misurato che
        lasciava passare le tre riscritture piu' probabili: il TERNARIO — che e' la forma in
        cui la classificazione di Stage 1 era scritta prima di essere spostata, quindi la
        piu' naturale in cui qualcuno la riscriverebbe — la f-string, e le funzioni `async`.
        Ora il criterio e' "il valore restituito contiene una stringa", che le copre tutte e
        tre. Resta fuori il ritorno indiretto (`return _ETICHETTE[k]` da un dizionario di
        costanti): flaggarlo vorrebbe dire flaggare ogni `return d[k]`, e un gate che grida
        sempre e' un gate che qualcuno silenzia.
        """
        source = (REPO_ROOT / "council" / "__main__.py").read_text(encoding="utf-8")
        colpevoli = [
            f"{nodo.name}():{figlio.lineno}"
            for nodo in ast.walk(ast.parse(source))
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
            for figlio in ast.walk(nodo)
            if isinstance(figlio, ast.Return) and _restituisce_una_stringa(figlio)
        ]
        self.assertEqual(
            colpevoli,
            [],
            f"{colpevoli} restituisce una stringa costante da `council/__main__.py`, che il "
            "gate di mutation non prova. Se e' una classificazione, sposta la funzione in "
            "`council/stages.py` accanto a `response_status` e `ranking_status` — cio' che "
            "pyproject.toml chiede: muovi il codice, non l'esclusione.",
        )

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib landed in 3.11")
    def test_only_the_presentation_module_is_exempt_from_mutation(self) -> None:
        with (REPO_ROOT / "pyproject.toml").open("rb") as manifest:
            excluded = tomllib.load(manifest)["tool"]["mutmut"]["do_not_mutate"]
        self.assertEqual(
            excluded,
            ["council/__main__.py"],
            "la lista `do_not_mutate` e' cambiata. Allargarla e' esattamente cio' che "
            "pyproject.toml dice di NON fare quando della logica finisce nel file escluso: "
            "sposta il codice nell'area mutata, non l'esclusione attorno al codice.",
        )


class TestTheVersionIsWrittenOnce(unittest.TestCase):
    """Three files carry the version, and nothing checked that they agree.

    It is the same shape as the test count that was written in four places with four
    different numbers: a fact repeated by hand drifts, and the drift is silent because
    each copy looks right on its own. `pyproject.toml` is the manifest and therefore
    the source; `__init__.py` feeds the User-Agent that OpenRouter sees, and
    `sonar-project.properties` labels the analysis. A run reporting a version the
    package does not have is a run nobody can correlate.
    """

    def _manifest_version(self) -> str:
        with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
            return tomllib.load(fh)["project"]["version"]

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib landed in 3.11")
    def test_the_package_reports_the_manifest_version(self) -> None:
        self.assertEqual(council.__version__, self._manifest_version())

    @unittest.skipUnless(sys.version_info >= (3, 11), "tomllib landed in 3.11")
    def test_sonar_analyses_the_manifest_version(self) -> None:
        righe = (REPO_ROOT / "sonar-project.properties").read_text().splitlines()
        dichiarata = next(
            r.split("=", 1)[1].strip() for r in righe if r.startswith("sonar.projectVersion")
        )
        self.assertEqual(dichiarata, self._manifest_version())


if __name__ == "__main__":
    # Unico file di `tests/` che ne era privo. I due gate che vivono qui sono il seguito
    # del difetto delle tredici classi finite in zona morta: valgono solo se qualcuno puo'
    # lanciarli come lancia gli altri file.
    unittest.main()
