"""Run every standard Mak'Gora validation suite in one command.

This is the canonical full source-checkout validation entry point:

    python tests/run_all_tests.py

It does not contain any test logic of its own. Each standard suite keeps its
own runner script, and this module only launches those runners, one per child
process, so module bootstrapping and process state stay isolated exactly as
they are when a suite is run on its own. The child runners remain the
authority on their own pass counts and output.

Design rules this module keeps:

* Suites are launched with ``sys.executable``, so the aggregate run uses the
  currently active interpreter/virtualenv rather than whatever ``python``
  happens to resolve to.
* Every path is derived from ``Path(__file__).resolve()``, never from the
  process working directory, so ``python tests/run_all_tests.py`` from the
  repository root and ``python run_all_tests.py`` from inside ``tests/``
  behave identically. Children are launched with the tests directory as their
  working directory, which is what the individual runners already assume.
* Child output is never captured or suppressed; it streams straight to the
  terminal.
* Every suite runs even when an earlier one fails: the point of this command
  is one complete validation report, not a fail-fast gate. That includes
  suite-level launch problems -- a missing runner script, or a child the OS
  refuses to start -- which are recorded as failures rather than aborting the
  run.
* ``KeyboardInterrupt`` is deliberately not caught, so Ctrl+C stops the
  aggregate run the way it stops any other Python program.

Exit code is ``0`` only when all suites pass, and ``1`` when any suite fails,
is missing, or cannot be started.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple


# Anchored on this file, never on the invocation directory.
TESTS_DIR = Path(__file__).resolve().parent


# The five standard validation suites, in the order AGENTS.md documents them.
# Fixed and immutable on purpose: this tuple is the declaration of what "full
# validation" means, and a suite must appear exactly once.
SUITES: Tuple[Tuple[str, str], ...] = (
    ("Regression", "run_regression.py"),
    ("Architecture guardrails", "run_architecture_guardrails.py"),
    ("Source-kind validation", "run_source_kind_validation.py"),
    ("Effect-tag validation", "run_effect_tags_validation.py"),
    ("Subschool validation", "run_subschool_validation.py"),
)

# Exit code recorded for a suite whose runner script is absent. A missing
# runner is a failure of the aggregate run, not a crash.
MISSING_RUNNER_EXIT_CODE = 1

# Exit code recorded when the OS refuses to start a child at all (process-limit
# exhaustion, a vanished interpreter, ...). Like a missing runner, that is a
# failed suite, not a reason to abandon the remaining ones.
LAUNCH_ERROR_EXIT_CODE = 1

SEPARATOR = "=" * 72


def _emit(line: str = "") -> None:
    """Print a framing line, flushed so it cannot trail the child's output."""
    print(line, flush=True)


def suite_environment(base_environment: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Return the child environment: the current one, with UTF-8 mode ensured.

    An explicit ``PYTHONUTF8`` in the parent environment is preserved; only an
    absent value is defaulted to ``"1"``.
    """
    source = os.environ if base_environment is None else base_environment
    environment = dict(source)
    environment.setdefault("PYTHONUTF8", "1")
    return environment


def run_suite(
    script_name: str,
    *,
    tests_dir: Path = TESTS_DIR,
    runner: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
) -> int:
    """Run one suite runner in its own process and return its exit code.

    Never raises for a suite-level problem: a missing runner script yields
    :data:`MISSING_RUNNER_EXIT_CODE` and a child the OS refuses to start yields
    :data:`LAUNCH_ERROR_EXIT_CODE`, so both are reported as ordinary suite
    failures that leave the remaining suites (and the final summary) intact
    instead of aborting the run with a traceback. ``KeyboardInterrupt`` is not
    an ``OSError`` and still propagates.
    """
    script_path = tests_dir / script_name
    if not script_path.is_file():
        _emit(f"MISSING RUNNER: {script_path} does not exist.")
        return MISSING_RUNNER_EXIT_CODE

    try:
        completed = runner(
            [sys.executable, str(script_path)],
            cwd=str(tests_dir),
            env=suite_environment(),
            check=False,
        )
    except OSError as exc:
        _emit(f"LAUNCH FAILED: {script_path} could not be started ({type(exc).__name__}: {exc}).")
        return LAUNCH_ERROR_EXIT_CODE

    return int(completed.returncode)


def run_suites(
    suites: Sequence[Tuple[str, str]] = SUITES,
    *,
    tests_dir: Path = TESTS_DIR,
    runner: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
) -> int:
    """Run every declared suite once and return the aggregate exit code.

    All suites run even after a failure; the return value is ``0`` only when
    every suite exited ``0``.
    """
    results: List[Tuple[str, int]] = []

    for name, script_name in suites:
        _emit(SEPARATOR)
        _emit(f"RUNNING: {name}")
        _emit(SEPARATOR)

        exit_code = run_suite(script_name, tests_dir=tests_dir, runner=runner)
        results.append((name, exit_code))

        _emit()
        if exit_code == 0:
            _emit(f"PASS: {name}")
        else:
            _emit(f"FAIL: {name} (exit code {exit_code})")
        _emit()

    return _report_summary(results)


def _report_summary(results: Sequence[Tuple[str, int]]) -> int:
    """Print the compact end-of-run summary and return the aggregate code."""
    _emit(SEPARATOR)
    _emit("TEST SUMMARY")
    _emit(SEPARATOR)

    failed = [(name, code) for name, code in results if code != 0]
    for name, exit_code in results:
        if exit_code == 0:
            _emit(f"PASS: {name}")
        else:
            _emit(f"FAIL: {name} (exit code {exit_code})")

    _emit()
    if failed:
        _emit(
            f"{len(failed)} of {len(results)} suites failed: "
            + ", ".join(name for name, _ in failed)
        )
        return 1

    _emit(f"All {len(results)} suites passed.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. ``argv`` is accepted and ignored; there are no options."""
    return run_suites()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
