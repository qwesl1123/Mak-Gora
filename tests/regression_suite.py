"""Compatibility wrapper for the Mak'Gora regression suite.

Scenario functions now live in the tests/regression/ domain modules and the
ordered registry lives in tests/regression/registry.py. This module keeps the
historical import surface working: the aggregate runner entry points and the
harness re-exports used by the other validation suites.
"""

from __future__ import annotations

from harness import (  # noqa: F401  (re-exported for downstream suites)
    ABILITIES,
    CLASSES,
    EFFECT_TEMPLATES,
    MatchState,
    PETS,
    PET_AI,
    SOCKETS,
    PetState,
    _BREAK_ON_DAMAGE_CC_CASES,
    _assert_no_stun_effect,
    _detect_duel_html_path,
    _has_effect,
    _player_states,
    _turn_lines,
    apply_prep_build,
    effects,
    make_match,
    resolver,
    run_turns,
    state_extract,
    submit_action,
    submit_turn,
)
from regression.registry import (  # noqa: F401
    SCENARIOS,
    get_scenario_count,
    run_all,
    validate_scenario_registry,
)
