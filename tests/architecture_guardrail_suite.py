"""Static architecture guardrail suite for Mak'Gora.

These are *guardrail* tests, not gameplay regression tests. They read engine
source files as text and assert that a handful of high-risk architectural
invariants from ``AGENTS.md`` still hold. They intentionally do NOT import or
run the engine, so they are fast and have no gameplay side effects.

Guardrails implemented here:

1. Player resource gains: direct gameplay writes to ``player.res.mp`` /
   ``player.res.energy`` / ``player.res.rage`` in engine gameplay files are
   flagged unless explicitly allowlisted. Player resource generation/restoration
   must route through ``effects.grant_player_resource()``.

2. Damage-based resource gains stay post-damage: damage-based ``resource_gain``
   entries ("damage" / "damage_x3") must be credited from actual dealt HP damage
   in ``_resolve_actor_post_damage_reactions_stage`` and skipped in the
   speculative ``resolve_action`` section. Queued damage application must only
   add ``dealt_data["hp_damage"]`` to combat totals.

3. Queued raw proc speculative damage: ``effects.trigger_on_hit_passives``
   branches that queue a raw ``damage_event`` (``raw_incoming`` set) must not
   also add that speculative reduced value to ``bonus_damage``.

4. Resource-gain logs use the actual gained amount: the ``on_hit_resource_gains``
   handler in ``resolver.py`` must format its log from the ``gained`` value
   returned by ``grant_resource``, never by appending the static ``gain["log"]``
   directly (the old Aimed Shot bug).

5. Queued damage events are centrally constructed: ad-hoc
   ``"type": "damage_event"`` dict literals in gameplay producers are flagged;
   queued events must be built via ``damage_events.make_queued_damage_event()``.

6. Player HP restoration: direct player resource HP mutations are rejected unless
   they are the canonical ``apply_player_healing`` write or a narrow documented
   non-healing exception (damage subtraction, HP sacrifice/spending, or embedded
   debug/setup). Pet HP remains outside this player-only rule. This guardrail
   uses Python's ``ast`` module to recognize assignment targets accurately and to
   distinguish player resource HP (``*.res.hp``) from pet HP (bare ``pet.hp``).

7. Generic next-offense/direct-damage contract: explicit ``direct_damage``
   metadata is strict boolean data, known ability categories classify correctly,
   every active next-offense effect is snapshotted/consumed through the shared
   helpers, and strike-again receives the explicit no-next-offense parent basis.

Design notes / limitations:

* These checks are deliberately conservative. They catch the *obvious* bad
  patterns described in ``AGENTS.md`` and in the historical bugs; they do not
  attempt to prove the whole engine correct.
* Where a check relies on isolating a function body, it uses indentation-based
  extraction rather than a full parser. This is robust for the current engine
  layout and is documented at each use.
* Every allowlist entry is narrow (file + enclosing function + exact code
  snippet) and carries a reason, so it is easy to audit and hard to abuse.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

import path_discovery


_REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Source-file location (supports both the flat and nested engine layouts).
# --------------------------------------------------------------------------- #

def _engine_dir() -> Path:
    """Return the directory that holds the engine modules.

    Mirrors the layout detection in ``regression_suite.py``: the repo is either
    flat (modules in the repo root) or nested (``engine/`` + ``content/``).
    """
    nested_engine = _REPO_ROOT / "engine"
    if (nested_engine / "resolver.py").exists():
        return nested_engine
    return _REPO_ROOT


def _gameplay_file(basename: str) -> Optional[Path]:
    """Resolve a gameplay engine file by basename, or ``None`` if absent."""
    candidate = _engine_dir() / basename
    if candidate.exists():
        return candidate
    root_candidate = _REPO_ROOT / basename
    if root_candidate.exists():
        return root_candidate
    return None


# Engine gameplay files that participate in the resource / damage / healing
# pipelines.
# NOTE: When a new engine gameplay module is added (e.g. a dedicated combat or
# damage module), append its basename here so the resource-write and player-HP
# guardrails scan it too. Do NOT add data modules (abilities.py / items.py /
# pets.py), tests, or duel.html -- those are out of scope by design.
GAMEPLAY_FILE_BASENAMES: Tuple[str, ...] = (
    "resolver.py",
    "effects.py",
    "pet_ai.py",
    "periodic_items.py",
    "damage_types.py",
    "damage_events.py",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _enclosing_function(lines: List[str], index: int) -> str:
    """Return the name of the nearest enclosing ``def`` at or above ``index``."""
    line_indent = len(lines[index]) - len(lines[index].lstrip())
    for i in range(index, -1, -1):
        stripped = lines[i].lstrip()
        if not stripped.startswith("def "):
            continue
        def_indent = len(lines[i]) - len(lines[i].lstrip())
        if def_indent <= line_indent:
            match = re.match(r"def\s+(\w+)", stripped)
            if match:
                return match.group(1)
    return "<module>"


def _extract_block(lines: List[str], start_index: int) -> List[str]:
    """Return the source lines of the block that opens at ``start_index``.

    The block runs from ``start_index`` until the first later non-blank line
    whose indentation is less than or equal to the opening line's indentation.
    Comment-only and blank lines never terminate the block.
    """
    start_indent = len(lines[start_index]) - len(lines[start_index].lstrip())
    block = [lines[start_index]]
    for i in range(start_index + 1, len(lines)):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            block.append(raw)
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= start_indent:
            break
        block.append(raw)
    return block


def _logical_statements(block_lines: List[str]) -> List[str]:
    """Join physical lines into logical statements by bracket balance.

    A statement accumulates lines until its ``()[]{}`` brackets are balanced, so
    a multi-line assignment (e.g. a dict literal spread over several lines) is
    returned as a single string. Limitation: bracket counting is textual and does
    not exclude brackets inside string literals; this is acceptable for the
    small, bracket-balanced blocks these guardrails inspect.
    """
    statements: List[str] = []
    buf: List[str] = []
    depth = 0
    for raw in block_lines:
        if not raw.strip() and not buf:
            continue
        buf.append(raw)
        depth += (
            raw.count("(") + raw.count("[") + raw.count("{")
            - raw.count(")") - raw.count("]") - raw.count("}")
        )
        if depth <= 0 and buf:
            statements.append("\n".join(buf))
            buf = []
            depth = 0
    if buf:
        statements.append("\n".join(buf))
    return statements


def _extract_block_starting_at(source: str, marker: str) -> Optional[str]:
    """Return a marker line plus the control statement that immediately follows it.

    Used to isolate one logical block inside a larger function -- e.g. the
    deferred ``damage_resource_gain = ability.get("resource_gain", {})`` block and
    its guarding ``if``/``for`` logic -- so a check cannot be satisfied by an
    unrelated sibling block elsewhere in the same function.

    The returned text is: the marker line, any blank lines, the first following
    non-blank line at the marker's indentation (expected to be an ``if``/``for``
    header), and that header's fully-indented body. A subsequent statement that
    dedents back to the marker's indentation ends the block, so exactly one
    control construct is captured.
    """
    lines = source.splitlines()
    idx = next((i for i, line in enumerate(lines) if marker in line), None)
    if idx is None:
        return None
    start_indent = len(lines[idx]) - len(lines[idx].lstrip())
    block = [lines[idx]]

    j = idx + 1
    while j < len(lines) and not lines[j].strip():
        block.append(lines[j])
        j += 1
    if j < len(lines) and (len(lines[j]) - len(lines[j].lstrip())) == start_indent:
        block.append(lines[j])  # the following control header (if/for)
        j += 1
        while j < len(lines):
            raw = lines[j]
            if not raw.strip():
                block.append(raw)
                j += 1
                continue
            if (len(raw) - len(raw.lstrip())) <= start_indent:
                break
            block.append(raw)
            j += 1
    return "\n".join(block)


def _find_def(lines: List[str], func_name: str) -> Optional[int]:
    pattern = re.compile(r"^\s*def\s+" + re.escape(func_name) + r"\s*\(")
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i
    return None


def _extract_function(source: str, func_name: str) -> Optional[str]:
    """Return the full source of ``func_name`` (signature + body).

    Signature-aware: a multi-line ``def`` whose closing ``)`` sits at the same
    column as ``def`` would otherwise fool a pure indentation scan, so the
    header is consumed by tracking parenthesis depth until the colon that ends
    the signature. The body then runs until the first non-blank line that
    dedents to at/under the ``def`` indentation.
    """
    lines = source.splitlines()
    idx = _find_def(lines, func_name)
    if idx is None:
        return None
    def_indent = len(lines[idx]) - len(lines[idx].lstrip())

    depth = 0
    header_end = idx
    for j in range(idx, len(lines)):
        code = lines[j].split("#", 1)[0]
        depth += code.count("(") - code.count(")")
        header_end = j
        if depth <= 0 and code.rstrip().endswith(":"):
            break

    block = list(lines[idx:header_end + 1])
    for j in range(header_end + 1, len(lines)):
        raw = lines[j]
        stripped = raw.strip()
        if not stripped:
            block.append(raw)
            continue
        if (len(raw) - len(raw.lstrip())) <= def_indent:
            break
        block.append(raw)
    return "\n".join(block)


# =========================================================================== #
# Guardrail 1: player resource gains must route through grant_player_resource.
# =========================================================================== #

# Assignment (incl. augmented) to ``<expr>.res.mp|energy|rage``. A trailing
# ``\b`` prevents matching ``.res.mp_max`` etc., and the ``(?!=)`` lookahead
# keeps ``==`` comparisons from being treated as assignments (``<=`` / ``>=`` /
# ``!=`` also fail because their operator char is not ``=``).
_RESOURCE_WRITE_RE = re.compile(
    r"\.res\.(mp|energy|rage)\b\s*([+\-*/]?=)(?!=)"
)


# Narrow, auditable allowlist. Each entry must match a violation by
# (file basename, enclosing function name, exact stripped source line). Adding a
# genuinely new direct write requires a new entry here with a documented reason,
# which forces reviewer attention on the resource pipeline.
ALLOWED_RESOURCE_MUTATIONS: Tuple[Dict[str, str], ...] = (
    {
        "file": "resolver.py",
        "function": "_handle_innervate_special",
        "snippet": "ctx.actor.res.mp = ctx.actor.res.mp_max",
        "reason": (
            "Innervate is a pre-existing, tightly-scoped special-handler mechanic "
            "that fully restores mana to the cap. It is a documented exceptional "
            "mechanic in current main, not a generic resource gain, and predates "
            "the grant_player_resource pipeline."
        ),
    },
    {
        "file": "resolver.py",
        "function": "_handle_frenzied_regeneration_special",
        "snippet": "ctx.actor.res.rage = 0",
        "reason": (
            "Frenzied Regeneration SPENDS all current rage as its cost (converting "
            "it into a HoT). Zeroing rage here is explicit cost spending, not "
            "resource generation, so it is exempt from grant_player_resource."
        ),
    },
    {
        "file": "resolver.py",
        "function": "debug_simulate_aoe_normalization_suite",
        "snippet": "warrior.res.rage = warrior.res.rage_max",
        "reason": (
            "Debug simulation harness embedded in resolver.py. This is test/setup "
            "scaffolding (topping rage to max before probing AoE normalization), "
            "not live gameplay resource generation."
        ),
    },
)


def _is_allowed_resource_mutation(basename: str, function: str, stripped: str) -> bool:
    for entry in ALLOWED_RESOURCE_MUTATIONS:
        if (
            entry["file"] == basename
            and entry["function"] == function
            and entry["snippet"] == stripped
        ):
            return True
    return False


def guardrail_player_resource_writes() -> Tuple[bool, str]:
    """Fail on un-allowlisted direct writes to player mp/energy/rage.

    Only ``<expr>.res.<resource>`` writes are considered -- this is the player
    resource path. Pet-owned resources in pet_ai use bare ``pet.mp`` / ``pet.rage``
    (no ``.res.`` segment) and are intentionally out of scope here, because
    grant_player_resource is only for player resources.
    """
    violations: List[str] = []
    scanned: List[str] = []
    for basename in GAMEPLAY_FILE_BASENAMES:
        path = _gameplay_file(basename)
        if path is None:
            continue
        scanned.append(basename)
        lines = _read(path).splitlines()
        for i, line in enumerate(lines):
            # Skip pure comment lines; a write hidden after code on the same line
            # is still scanned because the match is on the code segment.
            code = line.split("#", 1)[0]
            if not _RESOURCE_WRITE_RE.search(code):
                continue
            function = _enclosing_function(lines, i)
            stripped = line.strip()
            if _is_allowed_resource_mutation(basename, function, stripped):
                continue
            violations.append(
                f"{basename}:{i + 1} (in {function}()): {stripped}"
            )

    if not scanned:
        return False, "No gameplay files were found to scan for resource writes."

    if violations:
        detail = "\n".join(f"  - {v}" for v in violations)
        return False, (
            "Direct player resource write(s) outside the grant_player_resource "
            "pipeline. Route gameplay mp/energy/rage gains through "
            "effects.grant_player_resource(), or add a narrow, documented entry "
            "to ALLOWED_RESOURCE_MUTATIONS if this is a legitimate "
            "spend/setup/exception:\n" + detail
        )
    return True, f"Scanned {', '.join(scanned)}; no un-allowlisted resource writes."


# =========================================================================== #
# Guardrail 2: damage-based resource gains stay in the post-damage stage.
# =========================================================================== #

# Tokens that represent speculative / pre-mitigation damage. None of these may
# feed the damage-based resource grant math inside the post-damage stage.
_SPECULATIVE_DAMAGE_TOKENS = (
    "raw_incoming",
    "passive_bonus_damage_total",
    "total_effective_damage_for_resources",
    "aoe_raw_damage",
)


def guardrail_damage_resource_gains_post_damage() -> Tuple[bool, str]:
    path = _gameplay_file("resolver.py")
    if path is None:
        return False, "resolver.py not found."
    source = _read(path)
    lines = source.splitlines()
    problems: List[str] = []

    # (a) The canonical post-damage stage must exist and must never reference
    #     speculative pre-mitigation damage tokens (whole-function guard).
    post_stage = _extract_function(source, "_resolve_actor_post_damage_reactions_stage")
    if post_stage is None:
        problems.append(
            "_resolve_actor_post_damage_reactions_stage() is missing; damage-based "
            "resource gains no longer have a dedicated post-damage home."
        )
    else:
        for token in _SPECULATIVE_DAMAGE_TOKENS:
            if token in post_stage:
                problems.append(
                    "_resolve_actor_post_damage_reactions_stage() references "
                    f"speculative damage token `{token}`; the post-damage stage "
                    "must only use resolved `dealt` HP damage."
                )

    # (a2) Scope the correctness check to the DEFERRED normal resource_gain block
    #      specifically. The post-damage stage has two resource-gain sections
    #      (`resource_gain_on_dealt` and this `damage_resource_gain` block); a
    #      whole-function check could pass on the first while the second regresses.
    #      Anchor on the `damage_resource_gain` marker and validate only that block.
    dmg_block = _extract_block_starting_at(
        source, 'damage_resource_gain = ability.get("resource_gain", {})'
    )
    if dmg_block is None:
        problems.append(
            "Could not locate the deferred `damage_resource_gain = "
            'ability.get("resource_gain", {})` block in '
            "_resolve_actor_post_damage_reactions_stage(); the guardrail anchor "
            "may have moved."
        )
    else:
        block_checks: Tuple[Tuple[str, int, str], ...] = (
            (r"if\s+gain\s*==\s*[\"']damage[\"']\s*:", 0,
             'the `if gain == "damage":` branch is missing'),
            (r"^\s*gain_value\s*=\s*dealt\s*$", re.MULTILINE,
             'the "damage" branch must assign `gain_value = dealt`'),
            (r"elif\s+gain\s*==\s*[\"']damage_x3[\"']\s*:", 0,
             'the `elif gain == "damage_x3":` branch is missing'),
            (r"gain_value\s*=\s*dealt\s*\*\s*3", 0,
             'the "damage_x3" branch must assign `gain_value = dealt * 3`'),
            (r"else\s*:\s*\n\s*continue\b", 0,
             "non-damage gain values must be skipped with `continue`"),
            (r"grant_resource\([^\n]*challenger_mode\s*=\s*resource_challenger_mode", 0,
             "grant_resource(...) must be called with "
             "`challenger_mode=resource_challenger_mode`"),
        )
        for pattern, flags, desc in block_checks:
            if not re.search(pattern, dmg_block, flags):
                problems.append(f"damage_resource_gain block: {desc}.")

    # (b) resolve_action must SKIP immediate grants for "damage"/"damage_x3" so
    #     those are deferred to the post-damage stage.
    skip_re = re.compile(
        r"if\s+gain\s+in\s+\(\s*[\"']damage[\"']\s*,\s*[\"']damage_x3[\"']\s*\)\s*:"
    )
    skip_idx = next((i for i, ln in enumerate(lines) if skip_re.search(ln)), None)
    if skip_idx is None:
        problems.append(
            "resolve_action no longer skips immediate grants for resource_gain "
            "values \"damage\"/\"damage_x3\" (expected `if gain in (\"damage\", "
            "\"damage_x3\"): continue`)."
        )
    else:
        following = next(
            (lines[j].strip() for j in range(skip_idx + 1, min(skip_idx + 3, len(lines)))
             if lines[j].strip()),
            "",
        )
        if following != "continue":
            problems.append(
                "The \"damage\"/\"damage_x3\" branch in resolve_action must "
                "`continue` (defer to post-damage), but the next statement is: "
                f"{following!r}."
            )

    # (c) Queued damage application must add only dealt_data["hp_damage"] to the
    #     actor's running dealt total.
    append_logs = _extract_function(source, "append_extra_logs")
    if append_logs is None:
        problems.append("append_extra_logs() is missing.")
    else:
        uses_hp_damage = bool(
            re.search(r"dealt_amount\s*=\s*int\(\s*dealt_data\.get\(\s*[\"']hp_damage[\"']",
                      append_logs)
        )
        accumulates_hp_damage = bool(
            re.search(r"total_dealt_by_actor\[actor_sid\]\s*=[^\n]*\+\s*dealt_amount",
                      append_logs)
        )
        if not (uses_hp_damage and accumulates_hp_damage):
            problems.append(
                "append_extra_logs() must add only dealt_data[\"hp_damage\"] "
                "(via `dealt_amount`) to total_dealt_by_actor; the expected "
                "hp_damage-derived accumulation was not found."
            )
        for token in ("raw_incoming", "raw_instances_total"):
            # Guard against counting the raw queued value instead of dealt HP.
            if re.search(r"total_dealt_by_actor\[actor_sid\][^\n]*" + token, append_logs):
                problems.append(
                    f"append_extra_logs() adds `{token}` to total_dealt_by_actor; "
                    "only resolved dealt HP damage may count toward combat totals."
                )

    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        return False, "Damage-based resource pipeline invariant(s) violated:\n" + detail
    return True, (
        "Damage-based resource gains are deferred to the post-damage stage and "
        "credited from actual dealt HP damage."
    )


# =========================================================================== #
# Guardrail 3: queued raw proc events must not double-count as bonus_damage.
# =========================================================================== #

# passive_type branches whose queued events are fully resolved (already
# mitigated, will not be re-mitigated) and may legitimately add to bonus_damage.
# Raw (``raw_incoming``) branches must never appear here. Empty in current main.
RAW_EVENT_BONUS_DAMAGE_ALLOWLIST: Tuple[str, ...] = ()


def _split_passive_branches(func_lines: List[str]) -> List[Tuple[str, List[str]]]:
    """Split trigger_on_hit_passives into (passive_type, branch_lines) chunks."""
    branch_re = re.compile(r"passive_type\s*==\s*[\"'](?P<name>\w+)[\"']")
    branches: List[Tuple[str, List[str]]] = []
    current_name: Optional[str] = None
    current_indent = 0
    current: List[str] = []
    for line in func_lines:
        match = branch_re.search(line)
        is_branch_header = bool(match) and (
            line.lstrip().startswith("if ") or line.lstrip().startswith("elif ")
        )
        if is_branch_header:
            if current_name is not None:
                branches.append((current_name, current))
            current_name = match.group("name")
            current_indent = len(line) - len(line.lstrip())
            current = [line]
            continue
        if current_name is not None:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            # A dedent to at/under the branch header ends the branch (unless it
            # is a blank/comment line, which we keep with the current branch).
            if stripped and indent <= current_indent:
                branches.append((current_name, current))
                current_name = None
                current = []
            else:
                current.append(line)
    if current_name is not None:
        branches.append((current_name, current))
    return branches


def guardrail_raw_proc_bonus_damage() -> Tuple[bool, str]:
    path = _gameplay_file("effects.py")
    if path is None:
        return False, "effects.py not found."
    source = _read(path)
    func = _extract_function(source, "trigger_on_hit_passives")
    if func is None:
        return False, "trigger_on_hit_passives() not found in effects.py."

    func_lines = func.splitlines()
    branches = _split_passive_branches(func_lines)
    raw_branches_seen: List[str] = []
    problems: List[str] = []

    # A branch queues a RAW event when it sets raw_incoming — either as a dict
    # key ("raw_incoming": ...) or as a keyword argument to the
    # make_passive_damage_event() factory (raw_incoming=...). Prose mentions of
    # raw_incoming in comments (no following "=" / not quoted) do not match.
    raw_marker_re = re.compile(r"[\"']raw_incoming[\"']|\braw_incoming\s*=")

    for name, branch_lines in branches:
        text = "\n".join(branch_lines)
        if not raw_marker_re.search(text):
            continue
        raw_branches_seen.append(name)
        if name in RAW_EVENT_BONUS_DAMAGE_ALLOWLIST:
            continue
        if re.search(r"\bbonus_damage\s*\+=", text):
            problems.append(
                f"passive_type '{name}' queues a raw damage_event (raw_incoming) "
                "AND adds to bonus_damage. Speculative raw proc damage must not be "
                "counted in bonus_damage; it is re-mitigated against the final "
                "target on landing. (If this event is genuinely fully-resolved and "
                "non-raw, add it to RAW_EVENT_BONUS_DAMAGE_ALLOWLIST with a reason.)"
            )

    if not raw_branches_seen:
        return False, (
            "No raw_incoming proc branches found in trigger_on_hit_passives(). "
            "Expected lightning_blast / void_blade / duplicate_offensive_spell; "
            "the guardrail anchor may have moved."
        )

    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        return False, "Raw proc speculative-damage invariant violated:\n" + detail
    return True, (
        "Raw proc branches (" + ", ".join(sorted(set(raw_branches_seen))) + ") queue "
        "damage_events without double-counting speculative damage in bonus_damage."
    )


# =========================================================================== #
# Guardrail 4: on_hit_resource_gains logs must use the actual gained amount.
# =========================================================================== #

# Reference to the static ability-data log string on a resource-gain entry:
# ``gain['log']`` / ``gain["log"]`` / ``gain.get('log')``. Logging this value
# instead of a message built from the actual ``gained`` amount is the old Aimed
# Shot bug.
_STATIC_GAIN_LOG_RE = re.compile(
    r"gain(?:\[\s*[\"']log[\"']\s*\]|\.get\(\s*[\"']log[\"'])"
)

# Simple-name assignment (``foo = ...``), excluding ``==`` and augmented forms.
_SIMPLE_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=(?!=)\s*(.+)$", re.DOTALL)
_LOG_APPEND_RE = re.compile(r"log_parts\.append\((.*)\)", re.DOTALL)


def guardrail_resource_gain_logs_use_gained() -> Tuple[bool, str]:
    path = _gameplay_file("resolver.py")
    if path is None:
        return False, "resolver.py not found."
    lines = _read(path).splitlines()

    loop_re = re.compile(r"for\s+gain\s+in\s+ability\.get\(\s*[\"']on_hit_resource_gains[\"']")
    loop_idx = next((i for i, ln in enumerate(lines) if loop_re.search(ln)), None)
    if loop_idx is None:
        return False, (
            "The on_hit_resource_gains loop was not found in resolver.py; the "
            "guardrail anchor may have moved."
        )

    block = _extract_block(lines, loop_idx)
    statements = _logical_statements(block)
    problems: List[str] = []

    if "gained = grant_resource(" not in "\n".join(block):
        problems.append(
            "on_hit_resource_gains handler no longer captures the actual gained "
            "amount from grant_resource() (expected `gained = grant_resource(...)`)."
        )

    # One-hop taint tracking over local variables in the loop body. A variable
    # bound from the static gain log WITHOUT also deriving from the actual
    # ``gained`` amount is "tainted"; a variable whose RHS references ``gained``
    # is "gained-derived". This catches laundering the static log through an
    # intermediate (``resource_log = str(gain['log'])`` -> append resource_log)
    # while still passing the current main pattern, where ``resource_log`` is a
    # dict lookup whose values interpolate ``gained``.
    #
    # Limitation: tracking is a single hop over simple-name assignments; it does
    # not chase var -> var -> var chains. That is sufficient for this handler and
    # keeps the check auditable.
    tainted: set[str] = set()
    gained_derived: set[str] = set()
    for stmt in statements:
        m = _SIMPLE_ASSIGN_RE.match(stmt)
        if not m:
            continue
        var, rhs = m.group(1), m.group(2)
        rhs_has_static = bool(_STATIC_GAIN_LOG_RE.search(rhs))
        rhs_has_gained = bool(re.search(r"\bgained\b", rhs))
        if rhs_has_gained:
            gained_derived.add(var)
        if rhs_has_static and not rhs_has_gained:
            tainted.add(var)

    def _refs(text: str, names: set[str]) -> List[str]:
        return sorted(n for n in names if re.search(r"\b" + re.escape(n) + r"\b", text))

    append_problem = False
    good_append_found = False
    for stmt in statements:
        am = _LOG_APPEND_RE.search(stmt)
        if not am:
            continue
        arg = am.group(1)
        first_line = stmt.strip().splitlines()[0]
        if _STATIC_GAIN_LOG_RE.search(arg):
            append_problem = True
            problems.append(
                "on_hit_resource_gains appends the static gain['log'] directly "
                "instead of a message built from the actual `gained` amount "
                f"(the old Aimed Shot bug): {first_line}"
            )
            continue
        used_tainted = _refs(arg, tainted)
        if used_tainted:
            append_problem = True
            problems.append(
                "on_hit_resource_gains appends variable(s) "
                f"{used_tainted} derived from the static gain['log'] without the "
                f"actual `gained` amount (the old Aimed Shot bug): {first_line}"
            )
            continue
        if re.search(r"\bgained\b", arg) or _refs(arg, gained_derived):
            good_append_found = True

    if not good_append_found and not append_problem:
        problems.append(
            "on_hit_resource_gains no longer logs the actual `gained` amount: no "
            "log_parts.append references `gained` or a gained-derived value."
        )

    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        return False, "on_hit_resource_gains log invariant violated:\n" + detail
    return True, (
        "on_hit_resource_gains logs are formatted from the actual `gained` amount "
        "returned by grant_resource() (verified through one-hop variable taint)."
    )


# =========================================================================== #
# Guardrail 5: queued "damage_event" dicts are built by make_queued_damage_event.
# =========================================================================== #

# A dict-literal ENTRY of the form `"type": "damage_event"` marks an ad-hoc
# queued damage event. The colon keeps comparisons/consumer checks like
# `entry.get("type") != "damage_event"` and string mentions in comments from
# matching, so this only fires on actual dict construction.
_QUEUED_DAMAGE_EVENT_LITERAL_RE = re.compile(
    r"[\"']type[\"']\s*:\s*[\"']damage_event[\"']"
)

# Files where queued damage events may be produced. damage_events.py is
# deliberately NOT scanned: it is the single designated home of the
# "type": "damage_event" literal (inside make_queued_damage_event()).
_QUEUED_EVENT_PRODUCER_BASENAMES: Tuple[str, ...] = (
    "resolver.py",
    "effects.py",
    "pet_ai.py",
)

# Narrow allowlist, same shape as ALLOWED_RESOURCE_MUTATIONS: (file basename,
# enclosing function, exact stripped line). Empty in current main — every
# queued damage event is built via damage_events.make_queued_damage_event().
ALLOWED_QUEUED_DAMAGE_EVENT_LITERALS: Tuple[Dict[str, str], ...] = ()


def guardrail_queued_damage_event_literals() -> Tuple[bool, str]:
    """Fail on ad-hoc `"type": "damage_event"` dict literals in producers.

    Queued damage events must be built with
    ``damage_events.make_queued_damage_event()`` so the key schema and the
    incoming/source_kind/damage_instances normalization stay centralized.
    This intentionally only matches the queued-event ``type`` marker; plain
    non-damage dicts and producer-side passive events (no ``type`` key) are
    out of scope.
    """
    factory_path = _gameplay_file("damage_events.py")
    if factory_path is None:
        return False, (
            "damage_events.py not found; queued damage events no longer have a "
            "central factory module."
        )
    factory_source = _read(factory_path)
    if "def make_queued_damage_event(" not in factory_source:
        return False, "damage_events.py no longer defines make_queued_damage_event()."

    violations: List[str] = []
    for basename in _QUEUED_EVENT_PRODUCER_BASENAMES:
        path = _gameplay_file(basename)
        if path is None:
            continue
        lines = _read(path).splitlines()
        for i, line in enumerate(lines):
            code = line.split("#", 1)[0]
            if not _QUEUED_DAMAGE_EVENT_LITERAL_RE.search(code):
                continue
            function = _enclosing_function(lines, i)
            stripped = line.strip()
            if any(
                entry["file"] == basename
                and entry["function"] == function
                and entry["snippet"] == stripped
                for entry in ALLOWED_QUEUED_DAMAGE_EVENT_LITERALS
            ):
                continue
            violations.append(f"{basename}:{i + 1} (in {function}()): {stripped}")

    if violations:
        detail = "\n".join(f"  - {v}" for v in violations)
        return False, (
            "Ad-hoc queued \"damage_event\" dict literal(s) found. Build queued "
            "damage events with damage_events.make_queued_damage_event() so the "
            "schema and normalization stay centralized, or add a narrow, "
            "documented entry to ALLOWED_QUEUED_DAMAGE_EVENT_LITERALS:\n" + detail
        )
    return True, (
        "Queued \"damage_event\" entries are built via "
        "damage_events.make_queued_damage_event(); no ad-hoc literals in "
        + ", ".join(_QUEUED_EVENT_PRODUCER_BASENAMES) + "."
    )


# =========================================================================== #
# Guardrail 6: player HP restoration must route through apply_player_healing.
# =========================================================================== #
#
# Unlike guardrails 1 & 5, which match on source text, this guardrail parses the
# gameplay modules with Python's ``ast`` so it can identify assignment TARGETS
# precisely and distinguish PLAYER resource HP (any expression whose structural
# suffix is ``.res.hp``, including the bare local alias ``res.hp``) from PET HP (a
# bare entity ``pet.hp`` with no ``.res`` segment). Pet HP is intentionally out of
# scope: pet healing/damage/death/regen keep their explicit local ``pet.hp``
# clamps and must NOT be forced through the player helper.
#
# Recognition is STRUCTURAL, not name-rooted: it inspects only the trailing
# ``.res.hp`` (or ``.res`` for setattr) attribute segments, so it flags writes
# rooted in a subscript, call, or longer expression (``match.state[sid].res.hp``,
# ``players[0].res.hp``, ``get_player().res.hp``) just as readily as a plain
# ``player.res.hp``. A player HP write is recognized by its ``.res.hp`` shape
# regardless of what the root evaluates to.
#
# On top of the structural shape, the guardrail also tracks function-local
# resource ALIASES so a write cannot hide behind a one-line rebind. Within each
# function (and the module) scope, a local name bound from any ``*.res``
# expression, from ``getattr(<expr>, "res", ...)``, or from another known alias
# (a small fixed-point pass, so ``second = first`` chains resolve) is treated as a
# player-resource object; ``<alias>.hp`` and ``setattr(<alias>, "hp", ...)`` are
# then flagged too. Alias visibility follows LEXICAL scope: a nested helper sees
# aliases bound in the functions that enclose it (closure semantics), while a
# name the nested scope binds locally (e.g. a same-named parameter) shadows the
# outer alias. Sibling functions never share aliases -- they are not on each
# other's scope chain -- so this stays lexical and never becomes cross-function
# data-flow or type inference. It is deliberately shallow -- it catches the obvious
# ``r = x.res; r.hp -= v`` rebind (including from an enclosing scope), not aliases
# laundered through containers, attributes, calls, or returns.


class _HpMutation(NamedTuple):
    """One direct player-resource HP write found by the AST scan."""

    basename: str
    function: str
    lineno: int
    snippet: str      # exact stripped physical source line (for readable output)
    statement: str    # normalized (whitespace-collapsed) full statement
    kind: str         # "assign" | "augassign" | "annassign" | "setattr"


def _is_getattr_res(node: ast.AST) -> bool:
    """True iff ``node`` is ``getattr(<expr>, "res", ...)`` -- a dynamic ``.res`` read."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "res"
    )


def _is_res_object(node: ast.AST, alias_names: Optional[set[str]] = None) -> bool:
    """True iff ``node`` refers to a player-resource (``*.res``) object.

    Structural, root-agnostic: any ``ast.Attribute`` whose final attribute is
    ``res`` qualifies (``player.res``, ``match.state[sid].res``,
    ``get_player().res``), as does the bare ``ast.Name`` ``res`` and a direct
    ``getattr(<expr>, "res", ...)`` call (so an inline dynamic lookup like
    ``getattr(target, "res", None).hp`` is recognized without an intermediate
    binding). When ``alias_names`` is supplied, a local name bound to a resource
    object in the same scope (see ``_compute_resource_aliases``) also qualifies --
    so a rebound ``target_res = player.res`` is recognized too. This is the setattr
    base test -- ``setattr(<res-object>, "hp", ...)`` writes player HP.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == "res"
    if _is_getattr_res(node):
        return True
    if isinstance(node, ast.Name):
        if node.id == "res":
            return True
        if alias_names is not None and node.id in alias_names:
            return True
    return False


def _is_player_hp_target(node: ast.AST, alias_names: Optional[set[str]] = None) -> bool:
    """True iff ``node`` is a PLAYER resource HP target (``<res-object>.hp``).

    Recognized by the trailing two segments: an ``ast.Attribute`` ``.hp`` whose
    immediate value is a resource object (per ``_is_res_object``). This matches
    ``player.res.hp``, the bare alias ``res.hp``, subscript/call rooted forms like
    ``match.state[sid].res.hp`` / ``players[0].res.hp`` / ``get_player().res.hp``,
    and -- when ``alias_names`` is supplied -- function-local resource aliases such
    as ``target_res.hp`` where ``target_res = player.res``.

    It rejects bare ``pet.hp`` (its value ``pet`` is not a resource object) and
    guards against ``.res.hp_max`` (the final attribute is ``hp_max``, not ``hp``).
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "hp"
        and _is_res_object(node.value, alias_names)
    )


def _build_parent_map(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    parents: Dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _ast_enclosing_function(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> str:
    """Return the nearest enclosing ``def``/``async def`` name, or ``<module>``.

    Nested functions report their innermost enclosing function (the resolver's
    HP writes live in nested helpers such as the damage-application stage),
    which is exactly the granularity the allowlist matches on.
    """
    cur: Optional[ast.AST] = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parents.get(cur)
    return "<module>"


def _owning_scope(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> ast.AST:
    """Return the nearest enclosing function node, or the module root.

    This is the ALIAS scope key: the innermost ``def``/``async def`` a node lives
    in, falling back to the ``Module`` root. Nested and sibling functions get
    distinct scopes, so their resource-alias sets never bleed into one another.
    """
    cur: Optional[ast.AST] = node
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return cur
        cur = parents.get(cur)
    return node  # unreachable for a parsed module, but keeps the type total


def _enclosing_scope(scope: ast.AST, parents: Dict[ast.AST, ast.AST]) -> Optional[ast.AST]:
    """Return the lexically enclosing scope of ``scope`` (its parent def/module).

    Used to resolve CLOSURE aliases: a nested function sees names bound in the
    functions that lexically contain it. Returns ``None`` above the module root.
    """
    cur = parents.get(scope)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return cur
        cur = parents.get(cur)
    return None


def _collect_scope_local_names(
    tree: ast.AST, parents: Dict[ast.AST, ast.AST]
) -> Dict[ast.AST, set[str]]:
    """Map each scope to the set of names it binds locally (params + Store names).

    A name a scope binds locally SHADOWS the same name in enclosing scopes, so
    closure-alias resolution must not inherit an outer resource alias when the
    inner scope rebinds that name (e.g. a nested function parameter of the same
    name). Store-context ``Name`` targets cover assignment / ``for`` / ``with as``
    / ``except as`` bindings; function parameters are added explicitly. This is a
    safe over-approximation: attributing a name to a scope it doesn't truly own
    only suppresses inheritance (fewer detections), never invents a false one.
    """
    locals_by_scope: Dict[ast.AST, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            names = locals_by_scope.setdefault(node, set())
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
                names.add(arg.arg)
            if args.vararg:
                names.add(args.vararg.arg)
            if args.kwarg:
                names.add(args.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            scope = _owning_scope(node, parents)
            locals_by_scope.setdefault(scope, set()).add(node.id)
    return locals_by_scope


def _record(
    node: ast.AST,
    kind: str,
    basename: str,
    source_lines: List[str],
    source: str,
    parents: Dict[ast.AST, ast.AST],
) -> _HpMutation:
    line = source_lines[node.lineno - 1].strip() if 0 < node.lineno <= len(source_lines) else ""
    segment = ast.get_source_segment(source, node) or line
    statement = " ".join(segment.split())
    return _HpMutation(
        basename=basename,
        function=_ast_enclosing_function(node, parents),
        lineno=node.lineno,
        snippet=line,
        statement=statement,
        kind=kind,
    )


def _flatten_targets(target: ast.AST) -> List[ast.AST]:
    """Flatten an assignment target into its leaf expressions.

    Recurses through ``Tuple``/``List`` destructuring targets (and unwraps
    ``Starred``) so that ``a.res.hp, rest = ...`` and nested forms like
    ``(a.res.hp, (b, c)) = ...`` still surface the ``*.res.hp`` leaf. Non-container
    targets are returned as-is.
    """
    if isinstance(target, (ast.Tuple, ast.List)):
        leaves: List[ast.AST] = []
        for elt in target.elts:
            leaves.extend(_flatten_targets(elt))
        return leaves
    if isinstance(target, ast.Starred):
        return _flatten_targets(target.value)
    return [target]


def _iter_alias_bindings(target: ast.AST, value: ast.AST) -> List[Tuple[str, ast.AST]]:
    """Yield ``(name, bound_value)`` pairs a binding introduces, for alias seeding.

    * ``name = <value>`` -> ``(name, <value>)``.
    * Tuple/list unpacking is paired element-wise ONLY when both sides are
      tuple/list literals of equal length with no ``Starred`` target -- e.g.
      ``target_res, = (player.res,)`` -> ``(target_res, player.res)`` and
      ``a, b = x.res, y.res`` -> ``(a, x.res)``, ``(b, y.res)``. Non-decomposable
      pairings (a starred target, a length mismatch, or a value that is not a
      literal tuple/list such as a call) yield nothing -- the guardrail does not
      guess how an opaque right-hand side unpacks. Attribute/subscript targets are
      not alias names and yield nothing.
    """
    if isinstance(target, ast.Name):
        return [(target.id, value)]
    if (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and not any(isinstance(elt, ast.Starred) for elt in target.elts)
        and len(target.elts) == len(value.elts)
    ):
        pairs: List[Tuple[str, ast.AST]] = []
        for elt_target, elt_value in zip(target.elts, value.elts):
            pairs.extend(_iter_alias_bindings(elt_target, elt_value))
        return pairs
    return []


def _compute_resource_aliases(
    tree: ast.AST, parents: Dict[ast.AST, ast.AST]
) -> Dict[ast.AST, set[str]]:
    """Map each scope (function / module node) to its set of resource-alias names.

    A simple ``name = <rhs>`` binding introduces an alias when ``<rhs>`` is:

    * any ``*.res`` expression (per ``_is_res_object`` -- includes bare ``res``);
    * ``getattr(<expr>, "res", ...)`` (per ``_is_getattr_res``); or
    * another name already known to be an alias in the same scope.

    Plain ``Assign``, value-bearing ``AnnAssign`` (a typed ``name: T = <rhs>``),
    and ``NamedExpr`` walrus bindings (``(name := <rhs>)``) are all considered, so a
    typed alias (``target_res: Resources = player.res``) or a walrus alias
    (``if (target_res := player.res): ...``) is tracked just like a plain one.

    The third rule is applied via a fixed-point pass so propagation chains like
    ``first = player.res`` / ``second = first`` / ``second.hp += ...`` resolve.
    Each simple ``Name`` binding seeds an alias, including every ``Name`` target of
    a chained assignment (``primary = target_res = player.res`` seeds both) and each
    ``Name`` of a tuple/list unpacking paired element-wise with a tuple/list value
    (``target_res, = (player.res,)`` seeds ``target_res``; see
    ``_iter_alias_bindings``). Attribute targets are ignored here. The pass is
    intentionally shallow (no container/attribute/call/return laundering). This map
    holds each scope's OWN aliases; closure visibility (merging aliases from
    lexically enclosing scopes, with inner-scope shadowing) is applied at lookup
    time in ``_find_player_hp_mutations``. Sibling scopes therefore never share
    aliases.
    """
    scope_assigns: Dict[ast.AST, List[Tuple[str, ast.AST]]] = {}

    def _seed(target: ast.AST, value: Optional[ast.AST], scope_node: ast.AST) -> None:
        if value is None:
            return
        for name, bound in _iter_alias_bindings(target, value):
            scope_assigns.setdefault(scope_node, []).append((name, bound))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            scope = _owning_scope(node, parents)
            for target in node.targets:  # a = b = value -> seed every Name target
                _seed(target, node.value, scope)
        elif isinstance(node, ast.AnnAssign):
            _seed(node.target, node.value, _owning_scope(node, parents))
        elif isinstance(node, ast.NamedExpr):  # walrus: (target := value)
            _seed(node.target, node.value, _owning_scope(node, parents))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            # for TARGET in (a, b, ...): the loop var takes each element in turn,
            # so pair TARGET with every element of a literal iterable. Non-literal
            # iterables (a name/call) are opaque and seed nothing.
            if isinstance(node.iter, (ast.Tuple, ast.List)):
                scope = _owning_scope(node, parents)
                for element in node.iter.elts:
                    _seed(node.target, element, scope)

    aliases: Dict[ast.AST, set[str]] = {}
    for scope, assigns in scope_assigns.items():
        names: set[str] = set()
        changed = True
        while changed:  # fixed point over name -> name propagation
            changed = False
            for name, rhs in assigns:
                if name in names:
                    continue
                if (
                    _is_res_object(rhs)
                    or _is_getattr_res(rhs)
                    or (isinstance(rhs, ast.Name) and rhs.id in names)
                ):
                    names.add(name)
                    changed = True
        aliases[scope] = names
    return aliases


def _find_player_hp_mutations(source: str, basename: str) -> List[_HpMutation]:
    """Return every direct player-resource HP write in ``source``.

    Handles ``Assign`` (incl. multi-target chains and ``Tuple``/``List``
    destructuring targets), ``AnnAssign``, ``AugAssign``, and
    ``setattr(<res-object>, "hp", ...)`` calls, where a "res object" is a ``*.res``
    expression, the bare alias ``res``, or a function-local resource alias resolved
    by ``_compute_resource_aliases``. RHS arithmetic is NOT inspected: the scan
    finds ALL such HP writes (healing, damage, sacrifice, death, setup) and the
    allowlist then decides which are legitimate non-healing writes. This ordering
    is deliberate -- it is far safer than trying to infer whether an arbitrary
    right-hand side is a positive (healing) or negative (damage) delta.
    """
    tree = ast.parse(source)
    parents = _build_parent_map(tree)
    aliases_by_scope = _compute_resource_aliases(tree, parents)
    locals_by_scope = _collect_scope_local_names(tree, parents)
    source_lines = source.splitlines()
    mutations: List[_HpMutation] = []

    def scope_aliases(node: ast.AST) -> set[str]:
        """Aliases visible at ``node``: its own scope plus enclosing scopes.

        Walks the lexical scope chain outward (closure semantics), so a nested
        helper that closes over an outer resource alias sees it. A name a NEARER
        scope binds locally shadows the same name in enclosing scopes, so an inner
        parameter/local of the same name correctly hides an outer alias. Sibling
        functions never share aliases because they are not on each other's chain.
        """
        visible: set[str] = set()
        shadowed: set[str] = set()
        scope: Optional[ast.AST] = _owning_scope(node, parents)
        while scope is not None:
            for name in aliases_by_scope.get(scope, set()):
                if name not in shadowed:
                    visible.add(name)
            shadowed |= locals_by_scope.get(scope, set())
            scope = _enclosing_scope(scope, parents)
        return visible

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            # a = b = value -> inspect every target; destructuring targets
            # (a.res.hp, rest = ...) are flattened to their leaves.
            aliases = scope_aliases(node)
            for target in node.targets:
                for leaf in _flatten_targets(target):
                    if _is_player_hp_target(leaf, aliases):
                        mutations.append(_record(node, "assign", basename, source_lines, source, parents))
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None and _is_player_hp_target(node.target, scope_aliases(node)):
                mutations.append(_record(node, "annassign", basename, source_lines, source, parents))
        elif isinstance(node, ast.AugAssign):
            if _is_player_hp_target(node.target, scope_aliases(node)):
                mutations.append(_record(node, "augassign", basename, source_lines, source, parents))
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "hp"
                and _is_res_object(node.args[0], scope_aliases(node))
            ):
                mutations.append(_record(node, "setattr", basename, source_lines, source, parents))

    return mutations


# Narrow, auditable allowlist for direct player-resource HP writes, same shape as
# ALLOWED_RESOURCE_MUTATIONS: each entry names a (file basename, enclosing
# function, exact statement) plus a reason that explains why the write is NOT
# player healing. Matching is by basename + function + exact statement (the
# stripped physical line OR the whitespace-normalized full statement), so it can
# never become a file-wide, function-wide, substring, or line-number exemption.
#
# Exactly ONE entry is a positive restoration write: the canonical mutation
# inside effects.apply_player_healing(). Every other entry is damage subtraction
# or explicit HP spending, and must stay local precisely because it is not
# healing. Adding a new entry forces reviewer attention on the healing pipeline.
ALLOWED_PLAYER_HP_MUTATIONS: Tuple[Dict[str, str], ...] = (
    {
        "file": "effects.py",
        "function": "apply_player_healing",
        "snippet": "res.hp = min(int(res.hp_max), before_hp + amount)",
        "reason": (
            "CANONICAL player-healing write. apply_player_healing() is the "
            "centralized final player-HP restoration primitive: it caps at hp_max, "
            "performs the single res.hp mutation, and returns the actual capped "
            "gain. This is the only allowlisted POSITIVE player-HP application; all "
            "other production restoration must route through this function."
        ),
    },
    {
        "file": "resolver.py",
        "function": "_apply_damage_application_stage",
        "snippet": "target_entity.res.hp -= total_remaining",
        "reason": (
            "Damage subtraction, not healing. This is the post-absorb HP loss in "
            "the damage-application resolution stage (the player branch of the "
            "damage pipeline); it reduces HP by resolved incoming damage and must "
            "not route through apply_player_healing()."
        ),
    },
    {
        "file": "effects.py",
        "function": "apply_player_hp_sacrifice",
        "snippet": "target.res.hp = current_hp - actual_cost",
        "reason": (
            "CANONICAL player-HP sacrifice write. apply_player_hp_sacrifice() "
            "reduces HP as an explicit non-damage cost, enforces the caller's "
            "minimum-HP remainder, and returns the actual amount paid. It must "
            "not route through apply_player_healing() or apply_damage()."
        ),
    },
)


def _is_allowed_player_hp_mutation(mutation: _HpMutation) -> bool:
    for entry in ALLOWED_PLAYER_HP_MUTATIONS:
        if (
            entry["file"] == mutation.basename
            and entry["function"] == mutation.function
            and entry["snippet"] in (mutation.snippet, mutation.statement)
        ):
            return True
    return False


# Synthetic self-test cases. A guardrail is worthless if its detector silently
# stops matching, so before trusting the real scan we assert the detector still
# flags obvious bypasses and still ignores pet HP. Each "bad" source MUST yield
# >=1 player-HP mutation; each "pet" source MUST yield exactly 0.
_SELFTEST_BAD_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("bad_plus", "def bad_plus(player):\n    player.res.hp += 5\n"),
    ("bad_add", "def bad_add(player, heal):\n    player.res.hp = player.res.hp + heal\n"),
    ("bad_min", "def bad_min(player, heal):\n    player.res.hp = min(player.res.hp + heal, player.res.hp_max)\n"),
    ("bad_full", "def bad_full(player):\n    player.res.hp = player.res.hp_max\n"),
    ("bad_setattr", "def bad_setattr(player, heal):\n    setattr(player.res, \"hp\", player.res.hp + heal)\n"),
    ("bad_alias", "def bad_alias(res, heal):\n    res.hp += heal\n"),
    # Non-Name roots must be flagged too (Codex review on PR #34): the target's
    # structural `.res.hp` suffix is what matters, not whether the root is a plain
    # Name -- subscript, index, and call roots are all real bypass shapes.
    ("bad_subscript_aug", "def bad_subscript_aug(match, sid, heal):\n    match.state[sid].res.hp += heal\n"),
    ("bad_index_assign", "def bad_index_assign(players, heal):\n    players[0].res.hp = players[0].res.hp + heal\n"),
    ("bad_subscript_setattr", "def bad_subscript_setattr(match, sid, heal):\n    setattr(match.state[sid].res, \"hp\", heal)\n"),
    ("bad_call_root", "def bad_call_root(get_player):\n    get_player().res.hp = get_player().res.hp_max\n"),
    # Function-local resource aliases must be flagged too (2nd Codex review on
    # PR #34): a one-line rebind of `.res` (or getattr(..., "res")) must not hide
    # the subsequent `.hp` write. Propagation chains (`second = first`) resolve via
    # the fixed-point pass.
    ("bad_attribute_alias",
     "def bad_attribute_alias(player, heal):\n    target_res = player.res\n    target_res.hp += heal\n"),
    ("bad_getattr_alias",
     "def bad_getattr_alias(target, heal):\n    target_res = getattr(target, \"res\", None)\n"
     "    target_res.hp = min(target_res.hp + heal, target_res.hp_max)\n"),
    ("bad_alias_setattr",
     "def bad_alias_setattr(target, heal):\n    resources = getattr(target, \"res\", None)\n"
     "    setattr(resources, \"hp\", heal)\n"),
    ("bad_propagated_alias",
     "def bad_propagated_alias(player, heal):\n    first = player.res\n    second = first\n    second.hp += heal\n"),
    # A typed (annotated) alias binding must seed the alias set too, so the
    # subsequent `.hp` write is caught (3rd Codex review on PR #34).
    ("bad_annotated_alias",
     "def bad_annotated_alias(player, heal):\n    target_res: object = player.res\n    target_res.hp += heal\n"),
    # A chained assignment must seed every Name target as an alias, so a write
    # through any of them is caught (4th Codex review on PR #34).
    ("bad_chained_alias",
     "def bad_chained_alias(player, heal):\n    primary = target_res = player.res\n    target_res.hp += heal\n"),
    # An inline dynamic `getattr(..., "res", ...)` base must be flagged without an
    # intermediate binding (5th Codex review on PR #34).
    ("bad_inline_getattr",
     "def bad_inline_getattr(target, heal):\n    getattr(target, \"res\", None).hp += heal\n"),
    ("bad_inline_getattr_setattr",
     "def bad_inline_getattr_setattr(target, heal):\n    setattr(getattr(target, \"res\", None), \"hp\", heal)\n"),
    # A nested helper that closes over an outer resource alias must be flagged
    # (6th Codex review on PR #34): alias visibility follows lexical scope.
    ("bad_closure_alias",
     "def bad_closure_alias(player, amount):\n    target_res = player.res\n"
     "    def heal():\n        target_res.hp += amount\n"),
    # A resource bound through tuple/list unpacking must seed the alias too
    # (7th Codex review on PR #34): element-wise pairing of literal tuples/lists.
    ("bad_destructured_alias",
     "def bad_destructured_alias(player, heal):\n    target_res, = (player.res,)\n    target_res.hp += heal\n"),
    ("bad_destructured_alias_pair",
     "def bad_destructured_alias_pair(player, other, heal):\n"
     "    target_res, spare = player.res, other\n    target_res.hp += heal\n"),
    # A resource bound via a walrus assignment expression must seed the alias too
    # (8th Codex review on PR #34): ast.NamedExpr binding.
    ("bad_walrus_alias",
     "def bad_walrus_alias(player, heal):\n    if (target_res := player.res):\n        target_res.hp += heal\n"),
    # A resource bound via a loop target over a literal iterable must seed the
    # alias too (9th Codex review on PR #34): ast.For target.
    ("bad_loop_alias",
     "def bad_loop_alias(player, heal):\n    for target_res in (player.res,):\n        target_res.hp += heal\n"),
)

_SELFTEST_PET_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("allowed_pet_heal", "def allowed_pet_heal(pet, heal):\n    pet.hp = min(pet.hp + heal, pet.hp_max)\n"),
    ("allowed_pet_damage", "def allowed_pet_damage(pet, damage):\n    pet.hp -= damage\n"),
    # Negative: a local bound from a NON-``.res`` attribute is not a resource
    # alias, so writing its ``.hp`` must stay unflagged (guards the alias tracker
    # against over-reach onto arbitrary ``x.hp`` locals).
    ("clean_unrelated_alias",
     "def clean_unrelated_alias(entity, heal):\n    snapshot = entity.stats\n    snapshot.hp += heal\n"),
    # Negative: aliases are per-scope -- an alias defined in a SIBLING function
    # must not make a bare `first.hp` write in another function a player-HP write.
    ("clean_sibling_scope",
     "def defines_alias(player):\n    first = player.res\n"
     "def other(first, heal):\n    first.hp += heal\n"),
    # Negative: a nested function whose own PARAMETER shadows an outer resource
    # alias name must not inherit the outer alias -- the local binding wins.
    ("clean_closure_shadowed_param",
     "def outer(player, heal):\n    target_res = player.res\n"
     "    def inner(target_res, amount):\n        target_res.hp += amount\n"),
)


def _run_detector_self_tests() -> List[str]:
    """Return a list of self-test failures (empty when the detector is healthy)."""
    problems: List[str] = []
    for name, src in _SELFTEST_BAD_SOURCES:
        found = _find_player_hp_mutations(src, f"<selftest:{name}>")
        if not found:
            problems.append(
                f"detector self-test '{name}' regressed: expected a player-HP "
                "mutation to be detected, but none was found."
            )
    for name, src in _SELFTEST_PET_SOURCES:
        found = _find_player_hp_mutations(src, f"<selftest:{name}>")
        if found:
            problems.append(
                f"detector self-test '{name}' regressed: a non-player HP write was "
                f"misclassified as a player-resource write ({found[0].snippet!r})."
            )
    return problems


def guardrail_player_hp_writes() -> Tuple[bool, str]:
    """Fail on un-allowlisted direct player-resource HP writes (`*.res.hp`).

    All production player HP RESTORATION must route through
    ``effects.apply_player_healing()``. Direct ``*.res.hp`` writes are prohibited
    unless they are the canonical mutation inside that helper or a narrow,
    documented non-healing exception (damage subtraction, HP sacrifice/spending,
    embedded debug/setup) in ``ALLOWED_PLAYER_HP_MUTATIONS``. Pet HP (bare
    ``pet.hp``) is out of scope and stays explicit and local.
    """
    # (0) Detector self-tests: a broken matcher must fail loudly, not pass by
    #     silently matching nothing.
    self_test_problems = _run_detector_self_tests()
    if self_test_problems:
        detail = "\n".join(f"  - {p}" for p in self_test_problems)
        return False, "Player-HP detector self-test(s) failed:\n" + detail

    # (1) Current-source assertion: the canonical helper must still exist and the
    #     allowlist must still recognize its HP mutation.
    effects_path = _gameplay_file("effects.py")
    if effects_path is None:
        return False, "effects.py not found; cannot verify apply_player_healing()."
    effects_source = _read(effects_path)
    if "def apply_player_healing(" not in effects_source:
        return False, (
            "effects.py no longer defines apply_player_healing(); the canonical "
            "player-healing primitive is missing."
        )
    canonical = _extract_function(effects_source, "apply_player_healing")
    canonical_muts = _find_player_hp_mutations(canonical or "", "effects.py") if canonical else []
    if not canonical_muts:
        return False, (
            "apply_player_healing() no longer contains a recognizable player-HP "
            "mutation; the canonical healing write may have changed shape."
        )
    if not all(_is_allowed_player_hp_mutation(m) for m in canonical_muts):
        return False, (
            "apply_player_healing()'s HP mutation is not recognized by "
            "ALLOWED_PLAYER_HP_MUTATIONS; the canonical allowlist snippet may be "
            "out of date with the helper's actual write."
        )

    # (2) Scan every gameplay module and classify each player-HP mutation.
    violations: List[str] = []
    scanned: List[str] = []
    for basename in GAMEPLAY_FILE_BASENAMES:
        path = _gameplay_file(basename)
        if path is None:
            continue
        scanned.append(basename)
        for mutation in _find_player_hp_mutations(_read(path), basename):
            if _is_allowed_player_hp_mutation(mutation):
                continue
            violations.append(
                f"{mutation.basename}:{mutation.lineno} "
                f"(in {mutation.function}()): {mutation.snippet}"
            )

    if not scanned:
        return False, "No gameplay files were found to scan for player-HP writes."

    if violations:
        detail = "\n".join(f"  - {v}" for v in violations)
        return False, (
            "Direct player HP mutation(s) outside effects.apply_player_healing().\n"
            "All production player HP restoration must route through the shared "
            "helper. Damage, sacrifice, setup, and other non-healing writes require "
            "a narrow, documented ALLOWED_PLAYER_HP_MUTATIONS entry:\n\n" + detail +
            "\n\nEither route restoration through effects.apply_player_healing(), "
            "or -- only if this write is genuinely NOT healing -- add a narrow "
            "documented exception. Do NOT allowlist ordinary restoration."
        )
    return True, (
        f"Scanned {', '.join(scanned)}; every direct player-resource HP write is "
        "the canonical apply_player_healing() mutation or a documented non-healing "
        f"exception ({len(ALLOWED_PLAYER_HP_MUTATIONS)} allowlisted). Pet HP "
        "(bare pet.hp) is correctly out of scope."
    )


def guardrail_next_offense_direct_damage_contract() -> Tuple[bool, str]:
    """Pin strict direct-damage metadata and generic next-offense wiring."""

    abilities_path = _gameplay_file("abilities.py")
    resolver_path = _gameplay_file("resolver.py")
    effects_path = _gameplay_file("effects.py")
    if abilities_path is None or resolver_path is None or effects_path is None:
        return False, "abilities.py, resolver.py, or effects.py is missing."

    abilities_tree = ast.parse(_read(abilities_path), filename=str(abilities_path))
    abilities_assignment = next(
        (
            node
            for node in abilities_tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "ABILITIES"
                for target in node.targets
            )
        ),
        None,
    )
    if abilities_assignment is None:
        return False, "abilities.py does not define a literal ABILITIES mapping."
    try:
        abilities = ast.literal_eval(abilities_assignment.value)
    except (ValueError, TypeError, SyntaxError) as exc:
        return False, f"ABILITIES must remain statically auditable: {exc}"
    if not isinstance(abilities, dict):
        return False, "ABILITIES is not a mapping."

    invalid_explicit = sorted(
        ability_id
        for ability_id, ability in abilities.items()
        if isinstance(ability, dict)
        and "direct_damage" in ability
        and type(ability["direct_damage"]) is not bool
    )
    if invalid_explicit:
        return False, (
            "direct_damage must be exactly bool for: "
            + ", ".join(invalid_explicit)
        )

    def classified_direct(ability_id: str) -> bool:
        ability = abilities.get(ability_id)
        if not isinstance(ability, dict):
            raise AssertionError(f"missing ability {ability_id}")
        if "direct_damage" in ability:
            explicit = ability["direct_damage"]
            if type(explicit) is not bool:
                raise AssertionError(
                    f"{ability_id} direct_damage is not strict bool"
                )
            return explicit
        return any(
            value is not None and value != {} and value != []
            for value in (
                ability.get("dice"),
                ability.get("scaling"),
                ability.get("flat_damage"),
            )
        )

    expected_true = (
        "basic_attack",
        "fireball",
        "arcane_barrage",
        "cleave",
        "wildfire_bomb",
    )
    expected_false = (
        "pass_turn",
        "holy_light",
        "flash_heal",
        "cheap_shot",
        "corruption",
        "unstable_affliction",
        "agony",
        "vampiric_touch",
        "devouring_plague",
        "astral_explosion",
    )
    wrong_true = [ability_id for ability_id in expected_true if not classified_direct(ability_id)]
    wrong_false = [ability_id for ability_id in expected_false if classified_direct(ability_id)]
    if wrong_true or wrong_false:
        return False, (
            f"Direct-damage classification drifted; expected true={wrong_true or 'ok'}, "
            f"expected false={wrong_false or 'ok'}."
        )

    resolver_source = _read(resolver_path)
    required_resolver_snippets = (
        "def ability_has_direct_damage(",
        "if type(explicit) is not bool:",
        "has_direct_damage = ability_has_direct_damage(ability)",
        "snapshot_next_offense_empowerments(actor)",
        "combined_next_offense_multiplier(",
        "consume_next_offense_empowerments(",
        "secondary_proc_base_damage=strike_again_base_damage",
    )
    missing = [
        snippet
        for snippet in required_resolver_snippets
        if snippet not in resolver_source
    ]
    if missing:
        return False, (
            "Generic next-offense/direct-damage wiring is missing: "
            + ", ".join(missing)
        )
    if 'bool(ability.get("direct_damage"' in resolver_source:
        return False, (
            "resolver.py still uses permissive truthiness for direct_damage."
        )

    resolver_tree = ast.parse(resolver_source, filename=str(resolver_path))
    special_handlers = next(
        (
            node
            for node in resolver_tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "SPECIAL_ABILITY_HANDLERS"
            and isinstance(node.value, ast.Dict)
        ),
        None,
    )
    if special_handlers is None:
        return False, "SPECIAL_ABILITY_HANDLERS is not statically auditable."
    special_ids = [
        key.value
        for key in special_handlers.value.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    ]
    bypassing_specials = [
        ability_id
        for ability_id in special_ids
        if classified_direct(ability_id)
    ]
    if bypassing_specials:
        return False, (
            "Current special handlers classify as direct damage but bypass the "
            "generic direct packet multiplier path: "
            + ", ".join(sorted(bypassing_specials))
        )

    effects_source = _read(effects_path)
    if "secondary_proc_base_damage" not in effects_source:
        return False, (
            "trigger_on_hit_passives() lacks the explicit secondary-proc basis."
        )

    return True, (
        f"Validated {len(abilities)} abilities, {len(special_ids)} special "
        "handlers, strict direct_damage metadata, all-effect next-offense "
        "snapshot wiring, and the strike-again no-next-offense basis."
    )


# =========================================================================== #
# Guardrail 8: repository documentation contract (AGENTS.md / ROADMAP.md).
#
# These assertions used to live in the gameplay regression suite, which forced
# run_regression.py to require development Markdown inside a deployed runtime
# tree. Repository documentation is not a runtime artifact, so the contract is
# validated here instead: this suite is only meaningful in a full source
# checkout, and it fails loudly (FileNotFoundError from the shared
# repository-root detector) when the documentation is absent.
# =========================================================================== #

_REQUIRED_ROADMAP_TEXT: Tuple[str, ...] = (
    "## Recently completed: periodic-item expansion",
    "- [x] Scourgelord Chestplate: periodic current-HP sacrifice and Death's Bargain\n"
    "  next-offense empowerment",
    "## Active phase: remaining classes",
    "Every class uses exactly one primary combat resource.",
)

_FORBIDDEN_ROADMAP_TEXT: Tuple[str, ...] = (
    "## Active phase: periodic-item expansion",
    "- [ ] Scourgelord Chestplate",
)

_REQUIRED_AGENTS_NEXT_OFFENSE_TEXT: Tuple[str, ...] = (
    "Every active `empower_next_offense` effect snapshots onto the same next valid",
    "ordered canonically by effect ID",
    "combine multiplicatively into one scalar before the canonical damage stage",
    "All snapshotted effects consume together",
    "exactly once per cast in canonical order",
    "basis that excludes only the generic next-offense multiplier",
    "`direct_damage` is a strict boolean ability-definition property",
    "Offensive-action classification remains broader and separate",
    "Fixed-value shield-derived damage is explicitly non-direct",
)


def guardrail_next_offense_docs_and_roadmap_contract() -> Tuple[bool, str]:
    """Validate the roadmap phase state and the AGENTS.md next-offense contract."""
    repository_root = path_discovery.detect_repository_root()
    roadmap = _read(repository_root / "ROADMAP.md")
    agents = _read(repository_root / "AGENTS.md")

    problems: List[str] = []
    for text in _REQUIRED_ROADMAP_TEXT:
        if text not in roadmap:
            problems.append(f"ROADMAP.md is missing required text: {text!r}")
    for text in _FORBIDDEN_ROADMAP_TEXT:
        if text in roadmap:
            problems.append(f"ROADMAP.md still contains stale text: {text!r}")

    normalized_agents = " ".join(agents.split())
    for text in _REQUIRED_AGENTS_NEXT_OFFENSE_TEXT:
        if text not in normalized_agents:
            problems.append(f"AGENTS.md is missing next-offense contract text: {text!r}")

    if problems:
        return False, "; ".join(problems)

    return True, (
        f"Validated {len(_REQUIRED_ROADMAP_TEXT)} roadmap statements, "
        f"{len(_FORBIDDEN_ROADMAP_TEXT)} stale-roadmap prohibitions, and "
        f"{len(_REQUIRED_AGENTS_NEXT_OFFENSE_TEXT)} AGENTS.md next-offense "
        f"contract statements in {repository_root}."
    )


# =========================================================================== #
# Guardrail 9: test path discovery stays canonical and layout-agnostic.
#
# Static scan of the test tree. Two classes of historical bug are prohibited:
# hardcoded ``parents[N] / "duel.html"`` template lookups (which only work in
# the flat checkout) and repository-documentation lookups derived from the
# template directory (which only work when the documentation happens to sit
# next to duel.html). Gameplay regression modules must not read repository
# documentation at all, so a deployed runtime tree without development Markdown
# still runs the full gameplay suite.
#
# This file is excluded from the scan because it necessarily spells out the
# prohibited patterns and the documentation filenames it validates.
# =========================================================================== #

_TESTS_DIR = Path(__file__).resolve().parent
_GAMEPLAY_REGRESSION_DIR = _TESTS_DIR / "regression"

# Development-documentation extensions. A gameplay regression has no legitimate
# runtime dependency on any of them, whatever the filename or directory is.
_MARKDOWN_SUFFIXES: Tuple[str, ...] = (".md", ".markdown")


def _is_markdown_path_literal(value: object) -> bool:
    """Return True when ``value`` is a string literal naming a Markdown file."""
    if not isinstance(value, str):
        return False
    normalized = value.strip().replace("\\", "/").lower()
    return normalized.endswith(_MARKDOWN_SUFFIXES)


def _docstring_constant_ids(tree: ast.AST) -> set:
    """Return the ids of every Constant node that is a real docstring.

    Module, class, and function docstrings are documentation, not executable
    path construction, so they are exempt from the Markdown prohibition.
    Comments never reach the AST at all, so they need no special handling.
    """
    docstring_ids = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstring_ids.add(id(first.value))
    return docstring_ids


def _markdown_path_literals(source: str, filename: str) -> List[Tuple[int, str]]:
    """Return ``(line, literal)`` for every executable Markdown path literal.

    Detection is AST-based rather than a raw-source substring scan, so it sees
    the literal regardless of how it is consumed -- ``Path("README.md")``,
    ``root / "docs/design.md"``, ``open(...)``, ``.read_text()``,
    ``.read_bytes()``, a project helper such as ``_read(...)``, or any other
    loader call -- while comments and docstrings that merely *discuss* Markdown
    are ignored.
    """
    tree = ast.parse(source, filename=filename)
    docstring_ids = _docstring_constant_ids(tree)
    findings = {
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and id(node) not in docstring_ids
        and _is_markdown_path_literal(node.value)
    }
    return sorted(findings)


def _gameplay_regression_modules() -> List[Path]:
    """Return every Python module in the gameplay regression package."""
    return sorted(_GAMEPLAY_REGRESSION_DIR.rglob("*.py"))

_PROHIBITED_TEST_PATH_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (
        r"""parents\[\s*\d+\s*\]\s*/\s*["']duel\.html["']""",
        'hardcoded parents[N] / "duel.html"; use the canonical '
        "_detect_duel_html_path() detector instead",
    ),
    (
        r"""parents\[\s*\d+\s*\]\s*/\s*["'](?:AGENTS|ROADMAP)\.md["']""",
        "hardcoded parents[N] repository-documentation path; use the canonical "
        "repository-root detector instead",
    ),
    (
        r"""_detect_duel_html_path\(\)\s*\.parent\s*/\s*["'](?:AGENTS|ROADMAP)\.md["']""",
        "repository documentation resolved from the template directory; "
        "documentation lookup must be independent of duel.html",
    ),
)

_SCOURGELORD_DOC_SCENARIO = "scenario_scourgelord_item_effect_docs_and_handler_validation"


def _test_source_files() -> List[Path]:
    """Return every module in the test tree except this guardrail suite itself.

    Recursive on purpose: a regression domain organized into a subpackage must
    not escape the canonical-path rules, and this scan must stay consistent with
    the (also recursive) Markdown scan over ``_gameplay_regression_modules()``.
    """
    this_file = Path(__file__).resolve()
    return [path for path in sorted(_TESTS_DIR.rglob("*.py")) if path != this_file]


def _function_source(path: Path, function_name: str) -> Optional[str]:
    source = _read(path)
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node)
    return None


def guardrail_test_path_discovery_contract() -> Tuple[bool, str]:
    """Reject layout-specific path construction inside the test tree."""
    problems: List[str] = []

    scanned = _test_source_files()
    if not scanned:
        return False, "No test sources found to scan; the guardrail anchor moved."

    # The path-pattern scan and the Markdown scan must cover the same tree, so a
    # regression subpackage cannot be exempt from one but not the other.
    uncovered = [path for path in _gameplay_regression_modules() if path not in scanned]
    if uncovered:
        problems.append(
            "gameplay regression modules missing from the path-pattern scan: "
            + ", ".join(str(path.relative_to(_TESTS_DIR)) for path in uncovered)
        )

    for path in scanned:
        source = _read(path)
        for pattern, reason in _PROHIBITED_TEST_PATH_PATTERNS:
            for match in re.finditer(pattern, source):
                line_no = source.count("\n", 0, match.start()) + 1
                problems.append(
                    f"{path.relative_to(_TESTS_DIR)}:{line_no}: {reason}"
                )

    # Gameplay regressions may depend only on runtime source/data/template
    # artifacts, never on development Markdown -- any Markdown file, not just
    # the repository documentation this suite happens to validate.
    for path in _gameplay_regression_modules():
        try:
            findings = _markdown_path_literals(_read(path), str(path))
        except SyntaxError as exc:
            problems.append(f"{path.relative_to(_TESTS_DIR)}: unparseable ({exc}).")
            continue
        for line_no, literal in findings:
            problems.append(
                f"{path.relative_to(_TESTS_DIR)}:{line_no}: gameplay regression "
                f"references development Markdown {literal!r}; repository-"
                "documentation assertions belong to the architecture suite"
            )

    # The Scourgelord item/UI scenario must read the template through the
    # canonical detector.
    scourgelord_source = _function_source(
        _GAMEPLAY_REGRESSION_DIR / "test_periodic_items.py",
        _SCOURGELORD_DOC_SCENARIO,
    )
    if scourgelord_source is None:
        problems.append(
            f"{_SCOURGELORD_DOC_SCENARIO} was not found in test_periodic_items.py; "
            "the guardrail anchor may have moved."
        )
    elif "_detect_duel_html_path()" not in scourgelord_source:
        problems.append(
            f"{_SCOURGELORD_DOC_SCENARIO} must load duel.html through "
            "_detect_duel_html_path()."
        )

    # Documentation discovery must be independent of template discovery, and
    # both must be anchored to __file__ rather than the invocation directory.
    discovery_path = _TESTS_DIR / "path_discovery.py"
    if not discovery_path.is_file():
        problems.append("tests/path_discovery.py is missing.")
    else:
        discovery_source = _read(discovery_path)
        if "Path(__file__).resolve()" not in discovery_source:
            problems.append(
                "path_discovery.py must anchor discovery on Path(__file__).resolve()."
            )
        for cwd_pattern in ("Path.cwd(", "os.getcwd(", "os.curdir"):
            if cwd_pattern in discovery_source:
                problems.append(
                    f"path_discovery.py must not resolve paths from {cwd_pattern})."
                )
        for function_name in ("repository_root_candidates", "detect_repository_root"):
            function_source = _function_source(discovery_path, function_name)
            if function_source is None:
                problems.append(f"path_discovery.{function_name}() is missing.")
                continue
            for template_token in ("duel.html", '"templates"', "detect_duel_html_path"):
                if template_token in function_source:
                    problems.append(
                        f"path_discovery.{function_name}() must not derive "
                        f"documentation paths from the template lookup "
                        f"({template_token})."
                    )

        nested_deployment_supported = any(
            candidate.parent.name == "templates"
            for candidate in path_discovery.duel_html_candidates()
        )
        if not nested_deployment_supported:
            problems.append(
                "duel_html_candidates() no longer supports the nested "
                "<app root>/templates/duel.html deployment layout."
            )

        # Both detectors must accept only readable files: a directory named
        # duel.html (or AGENTS.md) must never shadow the real artifact.
        for function_name in ("detect_duel_html_path", "detect_repository_root"):
            function_source = _function_source(discovery_path, function_name)
            if function_source is None:
                problems.append(f"path_discovery.{function_name}() is missing.")
                continue
            if ".is_file()" not in function_source:
                problems.append(
                    f"path_discovery.{function_name}() must select candidates with "
                    ".is_file(), so a non-file entry cannot shadow the real artifact."
                )
            if ".exists()" in function_source:
                problems.append(
                    f"path_discovery.{function_name}() must not accept candidates "
                    "via .exists(); directories would pass."
                )

    if problems:
        return False, "; ".join(problems)

    return True, (
        f"Scanned {len(scanned)} test sources for "
        f"{len(_PROHIBITED_TEST_PATH_PATTERNS)} prohibited path patterns and "
        f"{len(_gameplay_regression_modules())} gameplay regression modules for "
        f"executable {'/'.join(_MARKDOWN_SUFFIXES)} path literals; the "
        "Scourgelord docs scenario uses the canonical detector, and "
        "documentation discovery is __file__-anchored and independent of "
        "template discovery."
    )


# =========================================================================== #
# Guardrail 10: the Markdown-reference detector itself.
#
# Guardrail 9 is only as strong as _markdown_path_literals(). These focused
# self-tests run the detector over synthetic sources so a future refactor
# cannot silently weaken it into a no-op (or into a docstring-tripping false
# positive). They live here, in static validation, because they must not add a
# Markdown dependency to the gameplay suite -- no file is ever opened; the
# synthetic sources are parsed as text.
# =========================================================================== #

_MARKDOWN_DETECTOR_MUST_DETECT: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    (
        "path_read_text",
        'Path("README.md").read_text()\n',
        ("README.md",),
    ),
    (
        "builtin_open_markdown_extension",
        'open("docs/design.markdown")\n',
        ("docs/design.markdown",),
    ),
    (
        "project_helper_with_composition",
        '_read(root / "CLASS_IMPLEMENTATION.md")\n',
        ("CLASS_IMPLEMENTATION.md",),
    ),
    (
        "mixed_case_suffix",
        'Path("MixedCase.MD")\n',
        ("MixedCase.MD",),
    ),
    (
        "directory_composition_mixed_case",
        'load_file(Path("Docs") / "Combat.MD")\n',
        ("Combat.MD",),
    ),
    (
        "windows_separator",
        'Path("docs\\\\architecture.md").open()\n',
        ("docs\\architecture.md",),
    ),
    (
        "read_bytes_on_composed_path",
        '(repository_root / "SECURITY.md").read_bytes()\n',
        ("SECURITY.md",),
    ),
    (
        "inside_function_body",
        (
            "def scenario_example() -> bool:\n"
            '    """Legitimate docstring."""\n'
            '    text = _read(root / "docs/testing.markdown")\n'
            "    return bool(text)\n"
        ),
        ("docs/testing.markdown",),
    ),
)

_MARKDOWN_DETECTOR_MUST_IGNORE: Tuple[Tuple[str, str], ...] = (
    (
        "module_docstring",
        '"""README.md is development documentation."""\n',
    ),
    (
        "function_docstring",
        (
            "def scenario_example() -> bool:\n"
            '    """Gameplay regressions must not read README.md."""\n'
            "    return True\n"
        ),
    ),
    (
        "class_docstring",
        (
            "class Example:\n"
            '    """See docs/architecture.md for background."""\n'
        ),
    ),
    (
        "comment_only",
        "# docs/design.md must not be loaded here\nvalue = 1\n",
    ),
    (
        "ordinary_string",
        'value = "ordinary gameplay text"\n',
    ),
    (
        "runtime_template_artifact",
        'duel_html = _detect_duel_html_path().read_text(encoding="utf-8")\n',
    ),
    (
        "non_string_constants",
        "value = 5\nother = None\n",
    ),
)


def guardrail_markdown_reference_detector_self_test() -> Tuple[bool, str]:
    """Prove the Markdown-reference detector detects and ignores correctly."""
    problems: List[str] = []

    for name, source, expected in _MARKDOWN_DETECTOR_MUST_DETECT:
        try:
            found = _markdown_path_literals(source, f"<detect:{name}>")
        except SyntaxError as exc:
            problems.append(f"must-detect case {name!r} is unparseable: {exc}")
            continue
        literals = tuple(literal for _, literal in found)
        if literals != expected:
            problems.append(
                f"must-detect case {name!r} expected {expected} but the detector "
                f"reported {literals}"
            )
            continue
        if any(line_no < 1 for line_no, _ in found):
            problems.append(f"must-detect case {name!r} reported a bad line number")

    for name, source in _MARKDOWN_DETECTOR_MUST_IGNORE:
        try:
            found = _markdown_path_literals(source, f"<ignore:{name}>")
        except SyntaxError as exc:
            problems.append(f"must-ignore case {name!r} is unparseable: {exc}")
            continue
        if found:
            problems.append(
                f"must-ignore case {name!r} produced false positives: "
                f"{tuple(literal for _, literal in found)}"
            )

    # The literal predicate itself: extensions are matched case-insensitively
    # and only at the end of the path, and non-strings are never paths.
    predicate_cases: Tuple[Tuple[object, bool], ...] = (
        ("README.md", True),
        ("readme.MARKDOWN", True),
        ("  docs/design.md  ", True),
        ("docs\\notes.Md", True),
        ("duel.html", False),
        ("md", False),
        ("design.md.py", False),
        ("markdown", False),
        (5, False),
        (None, False),
        (b"README.md", False),
    )
    for value, expected_flag in predicate_cases:
        if _is_markdown_path_literal(value) is not expected_flag:
            problems.append(
                f"_is_markdown_path_literal({value!r}) should be {expected_flag}"
            )

    if problems:
        return False, "; ".join(problems)

    return True, (
        f"Markdown-reference detector validated on "
        f"{len(_MARKDOWN_DETECTOR_MUST_DETECT)} must-detect sources, "
        f"{len(_MARKDOWN_DETECTOR_MUST_IGNORE)} must-ignore sources "
        "(docstrings/comments/ordinary strings), and "
        f"{len(predicate_cases)} literal-predicate cases."
    )


# =========================================================================== #
# Runner plumbing (mirrors the other suites' run_all() contract).
# =========================================================================== #

_GUARDRAILS: Tuple[Tuple[str, Callable[[], Tuple[bool, str]]], ...] = (
    ("guardrail_player_resource_writes", guardrail_player_resource_writes),
    ("guardrail_damage_resource_gains_post_damage", guardrail_damage_resource_gains_post_damage),
    ("guardrail_raw_proc_bonus_damage", guardrail_raw_proc_bonus_damage),
    ("guardrail_resource_gain_logs_use_gained", guardrail_resource_gain_logs_use_gained),
    ("guardrail_queued_damage_event_literals", guardrail_queued_damage_event_literals),
    ("guardrail_player_hp_writes", guardrail_player_hp_writes),
    (
        "guardrail_next_offense_direct_damage_contract",
        guardrail_next_offense_direct_damage_contract,
    ),
    (
        "guardrail_next_offense_docs_and_roadmap_contract",
        guardrail_next_offense_docs_and_roadmap_contract,
    ),
    (
        "guardrail_test_path_discovery_contract",
        guardrail_test_path_discovery_contract,
    ),
    (
        "guardrail_markdown_reference_detector_self_test",
        guardrail_markdown_reference_detector_self_test,
    ),
)


def run_all() -> List[Tuple[str, bool, str]]:
    results: List[Tuple[str, bool, str]] = []
    for name, fn in _GUARDRAILS:
        try:
            ok, reason = fn()
        except Exception as exc:  # a crashing guardrail is itself a failure
            results.append((name, False, f"guardrail raised {type(exc).__name__}: {exc}"))
            continue
        results.append((name, ok, reason))
    return results


if __name__ == "__main__":
    import sys

    outcomes = run_all()
    for name, ok, reason in outcomes:
        print(f"PASS: {name} -> {reason}" if ok else f"FAIL: {name} -> {reason}")
    failures = [entry for entry in outcomes if not entry[1]]
    if failures:
        print(f"{len(failures)} guardrail(s) failed!")
        sys.exit(1)
    print(f"All {len(outcomes)} architecture guardrails passed.")
