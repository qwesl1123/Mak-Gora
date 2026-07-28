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
while :func:`detect_repository_root` is reserved for focused tooling that
genuinely needs a full source checkout. No standard suite calls it: the
architecture guardrails validate *source* architecture and must pass in a
deployed nested tree that ships no documentation at all. What the standard
suites do pin is this module's shape -- that documentation discovery stays
scoped, ``__file__``-anchored, and independent of template discovery -- so the
API stays covered without any suite requiring the documents to exist.

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


_NESTED_PACKAGE_NAME = "duel"
_NESTED_PACKAGE_PARENT = "games"


def repository_root_candidates_for(package_root: Path) -> Tuple[Path, ...]:
    """Return the repository roots ``package_root``'s layout allows, best first.

    * The package root always comes first. A flat checkout *is* its repository
      root, and that stays true wherever it is cloned -- including a directory
      that happens to be called ``games/duel``. Real artifacts (the
      documentation actually being there) therefore decide the layout;
      directory names never override them.
    * The application root two levels up is offered only for a package that
      looks like a nested deployment, and only as a fallback. The intermediate
      ``games/`` directory is never a candidate, and nothing higher is either.

    The list is deliberately short: walking further up would let the
    architecture guardrail silently validate an unrelated checkout, which is
    exactly the fail-loud contract it must keep.
    """
    candidates = [package_root]
    if (
        package_root.name == _NESTED_PACKAGE_NAME
        and package_root.parent.name == _NESTED_PACKAGE_PARENT
    ):
        candidates.append(package_root.parent.parent)
    return tuple(candidates)


def repository_root_candidates() -> Tuple[Path, ...]:
    """Return the repository-root candidates for the current layout."""
    return repository_root_candidates_for(_PACKAGE_ROOT)


def detect_repository_root() -> Path:
    """Return the repository root that holds ``AGENTS.md`` and ``ROADMAP.md``.

    Candidates are tried in order, so a checkout that carries its own
    documentation always wins over the nested-deployment fallback. Raises
    ``FileNotFoundError`` when none of the layout's roots has the documentation,
    which is the correct outcome for a documentation lookup: validating some
    other checkout found further up the tree would be worse than failing.

    Only call this from focused tooling that is explicitly defined for a full
    source checkout. Gameplay regressions must never call it, and neither may
    the architecture guardrails: source architecture is validated from source,
    so a deployed nested tree without ``AGENTS.md``/``ROADMAP.md`` must still
    run every standard suite.
    """
    candidates = repository_root_candidates()
    for candidate in candidates:
        if (candidate / "AGENTS.md").is_file() and (candidate / "ROADMAP.md").is_file():
            return candidate
    raise FileNotFoundError(
        "Unable to find repository root containing AGENTS.md and ROADMAP.md; "
        f"checked: {', '.join(str(path) for path in candidates)} "
        f"(layout anchored on {_PACKAGE_ROOT})"
    )
