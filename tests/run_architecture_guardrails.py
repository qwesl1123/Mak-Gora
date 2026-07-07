"""Run Mak'Gora static architecture guardrail suite.

These guardrails read engine source as text and assert resource/damage pipeline
invariants from AGENTS.md. They do not run the engine, so they are fast.

Usage:
    python tests/run_architecture_guardrails.py
"""

from __future__ import annotations

import sys

from architecture_guardrail_suite import run_all


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
