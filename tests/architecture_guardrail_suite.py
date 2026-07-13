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
# ``player.res.hp``. It does not resolve the root object -- that would require
# data-flow analysis the guardrail deliberately avoids -- but a player HP write is
# recognized by its ``.res.hp`` shape regardless of what the root evaluates to.


class _HpMutation(NamedTuple):
    """One direct player-resource HP write found by the AST scan."""

    basename: str
    function: str
    lineno: int
    snippet: str      # exact stripped physical source line (for readable output)
    statement: str    # normalized (whitespace-collapsed) full statement
    kind: str         # "assign" | "augassign" | "annassign" | "setattr"


def _is_res_object(node: ast.AST) -> bool:
    """True iff ``node`` is a ``*.res`` object (or the bare local alias ``res``).

    Structural, root-agnostic: any ``ast.Attribute`` whose final attribute is
    ``res`` qualifies (``player.res``, ``match.state[sid].res``,
    ``get_player().res``), as does the bare ``ast.Name`` ``res``. This is the
    setattr base test -- ``setattr(<*.res>, "hp", ...)`` writes player resource HP.
    """
    if isinstance(node, ast.Attribute):
        return node.attr == "res"
    if isinstance(node, ast.Name):
        return node.id == "res"
    return False


def _is_player_hp_target(node: ast.AST) -> bool:
    """True iff ``node`` is a PLAYER resource HP target (``*.res.hp`` / ``res.hp``).

    Recognized structurally by the trailing two segments: an ``ast.Attribute``
    ``.hp`` whose immediate value is a ``*.res`` object (per ``_is_res_object``).
    This matches ``player.res.hp``, the bare alias ``res.hp``, and subscript/call
    rooted forms like ``match.state[sid].res.hp``, ``players[0].res.hp``, and
    ``get_player().res.hp`` -- regardless of the root expression.

    It rejects bare ``pet.hp`` (its value ``pet`` is not a ``.res`` object) and
    guards against ``.res.hp_max`` (the final attribute is ``hp_max``, not ``hp``).
    """
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "hp"
        and _is_res_object(node.value)
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
    HP writes live in nested helpers such as ``apply_self_inflicted_magical_damage``),
    which is exactly the granularity the allowlist matches on.
    """
    cur: Optional[ast.AST] = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parents.get(cur)
    return "<module>"


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


def _find_player_hp_mutations(source: str, basename: str) -> List[_HpMutation]:
    """Return every direct player-resource HP write in ``source``.

    Handles ``Assign`` (incl. multi-target chains and ``Tuple``/``List``
    destructuring targets), ``AnnAssign``, ``AugAssign``, and
    ``setattr(<*.res>, "hp", ...)`` calls. RHS arithmetic is NOT inspected: the
    scan finds ALL ``*.res.hp`` writes (healing, damage, sacrifice, death, setup)
    and the allowlist then decides which are legitimate non-healing writes. This
    ordering is deliberate -- it is far safer than trying to infer whether an
    arbitrary right-hand side is a positive (healing) or negative (damage) delta.
    """
    tree = ast.parse(source)
    parents = _build_parent_map(tree)
    source_lines = source.splitlines()
    mutations: List[_HpMutation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            # a = b = value -> inspect every target; destructuring targets
            # (a.res.hp, rest = ...) are flattened to their leaves.
            for target in node.targets:
                for leaf in _flatten_targets(target):
                    if _is_player_hp_target(leaf):
                        mutations.append(_record(node, "assign", basename, source_lines, source, parents))
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None and _is_player_hp_target(node.target):
                mutations.append(_record(node, "annassign", basename, source_lines, source, parents))
        elif isinstance(node, ast.AugAssign):
            if _is_player_hp_target(node.target):
                mutations.append(_record(node, "augassign", basename, source_lines, source, parents))
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "hp"
                and _is_res_object(node.args[0])
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
        "file": "resolver.py",
        "function": "apply_self_inflicted_magical_damage",
        "snippet": "ps.res.hp -= remaining",
        "reason": (
            "Damage subtraction, not healing. Applies post-absorb self-inflicted "
            "Mindgames magical damage to the caster's HP; a negative HP delta, so "
            "it is out of scope for the healing helper."
        ),
    },
    {
        "file": "resolver.py",
        "function": "apply_hp_sacrifice_absorb",
        "snippet": "actor.res.hp = max(min_hp_leave, int(actor.res.hp) - sacrificed_hp)",
        "reason": (
            "HP sacrifice / explicit HP spending, not healing. Deliberately reduces "
            "the actor's HP as a cost (converting sacrificed HP into an absorb "
            "shield), clamped to min_hp_leave. It is a cost mechanic and must stay "
            "local rather than routing through apply_player_healing()."
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
)

_SELFTEST_PET_SOURCES: Tuple[Tuple[str, str], ...] = (
    ("allowed_pet_heal", "def allowed_pet_heal(pet, heal):\n    pet.hp = min(pet.hp + heal, pet.hp_max)\n"),
    ("allowed_pet_damage", "def allowed_pet_damage(pet, damage):\n    pet.hp -= damage\n"),
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
                f"detector self-test '{name}' regressed: pet HP write was "
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
