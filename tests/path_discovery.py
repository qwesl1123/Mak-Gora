"""Canonical test-time path discovery for Mak'Gora.

Every test suite resolves runtime artifacts (``duel.html``) and repository
documentation (``AGENTS.md`` / ``ROADMAP.md``) through this module so that a
single layout change never has to be chased through individual scenarios.

Two layouts are supported:

* the flat repository checkout, where the engine modules, ``duel.html``, and
  the repository documentation all live in the repository root; and
* the deployed nested application layout, where the duel package lives under
  ``<app root>/games/duel/`` and the template is served from
  ``<app root>/templates/duel.html``.

Two rules keep the discovery honest:

* Everything is derived from ``Path(__file__).resolve()``, never from the
  process working directory, so running ``run_regression.py`` from the
  repository root and from inside ``games/duel/tests/`` resolves identically.
* Template discovery and documentation discovery are independent. Repository
  documentation is *not* a runtime artifact and is never looked up relative to
  the template directory: a deployed tree legitimately ships
  ``templates/duel.html`` without ever shipping ``AGENTS.md``.

Gameplay regressions may therefore depend on :func:`detect_duel_html_path`,
while :func:`detect_repository_root` is reserved for architecture/static
validation, which is only meaningful in a full source checkout.

This module deliberately has no engine imports, so the static guardrail suite
can use it without bootstrapping gameplay modules.
"""
from __future__ import annotations

from pathlib import Path
from typing import Tuple


# tests/ -> the duel package root (flat repository root, or <app>/games/duel/).
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def duel_html_candidates() -> Tuple[Path, ...]:
    """Return every supported location of ``duel.html``, in priority order."""
    return (
        _PACKAGE_ROOT / "duel.html",
        _PACKAGE_ROOT / "templates" / "duel.html",
        _PACKAGE_ROOT.parent / "templates" / "duel.html",
        _PACKAGE_ROOT.parent.parent / "templates" / "duel.html",
    )


def detect_duel_html_path() -> Path:
    """Return the runtime ``duel.html`` template for the current layout.

    Candidates must be readable *files*: a directory (or any other filesystem
    entry) named ``duel.html`` is not a template and must not shadow the real
    one further down the candidate list.
    """
    candidates = duel_html_candidates()
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Unable to find duel.html; checked: "
        + ", ".join(str(path) for path in candidates)
    )


def repository_root_candidates() -> Tuple[Path, ...]:
    """Return every supported repository-root location, in priority order.

    Resolved from this file only: the flat checkout root is the package root
    itself, while the nested deployment walks up ``games/duel`` to the
    application root that holds the checked-out documentation.
    """
    return (
        _PACKAGE_ROOT,
        _PACKAGE_ROOT.parent,
        _PACKAGE_ROOT.parent.parent,
        _PACKAGE_ROOT.parent.parent.parent,
    )


def detect_repository_root() -> Path:
    """Return the repository root that holds ``AGENTS.md`` and ``ROADMAP.md``.

    Raises ``FileNotFoundError`` when the documentation is absent, which is the
    correct outcome for architecture validation: it is only defined for a full
    source checkout. Gameplay regressions must never call this helper.
    """
    candidates = repository_root_candidates()
    for candidate in candidates:
        if (candidate / "AGENTS.md").is_file() and (candidate / "ROADMAP.md").is_file():
            return candidate
    raise FileNotFoundError(
        "Unable to find repository root containing AGENTS.md and ROADMAP.md; "
        f"checked: {', '.join(str(path) for path in candidates)}"
    )
