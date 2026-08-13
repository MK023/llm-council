#!/usr/bin/env python3
"""Emit a hash-pinned requirements file for the dev tools, straight from PyPI.

A hash typed by hand is a hash nobody checked. This reads the digests PyPI
publishes for an exact version and prints them in pip's `--require-hashes`
format, so bumping a tool is one command and never a copy-paste.

    python scripts/pin_dev_deps.py ruff==0.15.9 coverage==7.13.4 > requirements-dev.txt

Versions are arguments, not constants: the pinned set lives in
requirements-dev.txt alone, and this script has no opinion about it.

stdlib-only, like the package it serves.
"""

from __future__ import annotations

import json
import sys
import urllib.request

PYPI_JSON = "https://pypi.org/pypi/{name}/{version}/json"


def wanted(filename: str) -> bool:
    """True for the artifacts this project is willing to install.

    CI runs ubuntu x86_64; the macOS wheels are what Marco installs locally.
    Everything else (musllinux, windows, other arches) is deliberately out — an
    unused hash is noise, and noise is what makes a lockfile stop being read.

    Matching is on the platform TAG, not on a filename suffix: real manylinux
    wheels carry compound tags like
    `manylinux_2_17_x86_64.manylinux2014_x86_64.whl`, so a suffix test for
    `linux_x86_64.whl` matches none of them and silently produces a lockfile with
    no Linux hashes at all. That version of this function shipped, and only
    `pip install --require-hashes` on the runner would have noticed.

    sdists are refused on purpose: without a matching wheel, pip should fail loudly
    rather than start building ruff and zizmor from Rust on an unexpected platform.
    """
    if not filename.endswith(".whl"):
        return False
    if filename.endswith("-none-any.whl"):  # pure python, runs anywhere
        return True
    if "musllinux" in filename:  # Alpine: not a platform this project runs on
        return False
    if "macosx" in filename:
        return True
    return "manylinux" in filename and "x86_64" in filename


def hashes_for(name: str, version: str) -> list[str]:
    """Returns the sha256 of every published artifact we are willing to install."""
    with urllib.request.urlopen(PYPI_JSON.format(name=name, version=version)) as response:
        payload = json.load(response)
    return [entry["digests"]["sha256"] for entry in payload["urls"] if wanted(entry["filename"])]


def render(requirement: str) -> str:
    """Turns 'ruff==0.15.9' into a pip requirement block with its hashes."""
    name, _, version = requirement.partition("==")
    if not version:
        raise SystemExit(f"'{requirement}': pin an exact version, e.g. ruff==0.15.9")

    digests = hashes_for(name, version)
    # Silence here would produce a requirement with zero hashes, which pip accepts
    # as "no hashes required" for that line — the failure mode this script exists
    # to prevent. Better a loud exit than a lockfile that locks nothing.
    if not digests:
        raise SystemExit(f"'{requirement}': no artifact matched the platform filter")

    lines = [f"{name}=={version} \\"]
    lines += [f"    --hash=sha256:{d} \\" for d in digests[:-1]]
    lines.append(f"    --hash=sha256:{digests[-1]}")
    return "\n".join(lines)


def main(requirements: list[str]) -> None:
    if not requirements:
        raise SystemExit(__doc__)
    print("\n\n".join(render(r) for r in requirements))


if __name__ == "__main__":
    main(sys.argv[1:])
