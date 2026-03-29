"""Run dedicated Mak'Gora effect-tags validation suite."""

from __future__ import annotations

import sys

from effect_tags_validation_suite import run_all


if __name__ == "__main__":
    results = run_all()
    failed = [entry for entry in results if not entry[1]]

    for name, ok, reason in results:
        if ok:
            print(f"PASS: {name}")
        else:
            print(f"FAIL: {name} -> {reason}")

    if failed:
        sys.exit(1)

    print(f"All {len(results)} effect-tags validation scenarios passed.")
