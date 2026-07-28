"""Run Mak'Gora static architecture guardrail suite.

These guardrails read engine source as text and assert resource/damage pipeline
invariants from AGENTS.md. They do not run the engine, so they are fast.

This runner also *enforces* the suite's documentation-independence at runtime:
an audit hook fails the run if any guardrail opens a Markdown file. The suite's
static checks approximate that boundary by inspecting source; this observes the
real thing, so a path assembled, aliased, imported dynamically, or produced by
any route the static scan cannot follow is still caught -- in the source
checkout, where the documents exist, rather than only in a deployed tree that
ships without them.

Usage:
    python tests/run_architecture_guardrails.py
"""

from __future__ import annotations

import sys


# Kept literal and local: this module is not the guardrail suite, so it is free
# to name the extensions it refuses to let the suite open.
_PROHIBITED_READ_SUFFIXES = (".md", ".markdown")


class MarkdownDependencyError(RuntimeError):
    """Raised when a guardrail opens development documentation."""


def _reject_markdown_reads(event: str, arguments: tuple) -> None:
    """Fail the run if architecture validation opens a Markdown file.

    Architecture guardrails validate *source*. Nothing they legitimately read
    is Markdown, so any such open is a documentation dependency that would
    break the supported nested deployment, which ships no documentation.
    """
    if event != "open" or not arguments:
        return
    target = arguments[0]
    if isinstance(target, int):  # already-open file descriptor, not a path
        return
    try:
        path = str(target)
    except Exception:  # pragma: no cover - defensive; never block on a repr
        return
    if path.lower().endswith(_PROHIBITED_READ_SUFFIXES):
        raise MarkdownDependencyError(
            f"architecture validation opened development documentation: {path}. "
            "Architecture guardrails must validate source architecture without "
            "reading AGENTS.md, ROADMAP.md, or any other Markdown, so the suite "
            "still passes in a deployed tree that ships none."
        )


sys.addaudithook(_reject_markdown_reads)

from architecture_guardrail_suite import run_all  # noqa: E402  (hook first)


if __name__ == "__main__":
    results = run_all()
    failed = [entry for entry in results if not entry[1]]

    for name, ok, reason in results:
        if ok:
            print(f"PASS: {name} -> {reason}")
        else:
            print(f"FAIL: {name} -> {reason}")

    if failed:
        print(f"{len(failed)} guardrail(s) failed!")
        sys.exit(1)

    print(f"All {len(results)} architecture guardrails passed.")
