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

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


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


# Engine gameplay files that participate in the resource / damage pipelines.
# NOTE: When a new engine gameplay module is added (e.g. a dedicated combat or
# damage module), append its basename here so the resource-write guardrail scans
# it too. Do NOT add data modules (abilities.py / items.py / pets.py), tests, or
# duel.html -- those are out of scope by design.
GAMEPLAY_FILE_BASENAMES: Tuple[str, ...] = (
    "resolver.py",
    "effects.py",
    "pet_ai.py",
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

    # (a) The canonical post-damage stage must exist and credit damage-based
    #     gains from the actual dealt HP amount.
    post_stage = _extract_function(source, "_resolve_actor_post_damage_reactions_stage")
    if post_stage is None:
        problems.append(
            "_resolve_actor_post_damage_reactions_stage() is missing; damage-based "
            "resource gains no longer have a dedicated post-damage home."
        )
    else:
        if "gain_value = dealt" not in post_stage:
            problems.append(
                "_resolve_actor_post_damage_reactions_stage() no longer credits "
                "damage-based gains from the actual `dealt` HP amount "
                "(expected `gain_value = dealt`)."
            )
        for token in _SPECULATIVE_DAMAGE_TOKENS:
            if token in post_stage:
                problems.append(
                    "_resolve_actor_post_damage_reactions_stage() references "
                    f"speculative damage token `{token}`; the post-damage stage "
                    "must only use resolved `dealt` HP damage."
                )

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

    for name, branch_lines in branches:
        text = "\n".join(branch_lines)
        if '"raw_incoming"' not in text and "'raw_incoming'" not in text:
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
# Runner plumbing (mirrors the other suites' run_all() contract).
# =========================================================================== #

_GUARDRAILS: Tuple[Tuple[str, Callable[[], Tuple[bool, str]]], ...] = (
    ("guardrail_player_resource_writes", guardrail_player_resource_writes),
    ("guardrail_damage_resource_gains_post_damage", guardrail_damage_resource_gains_post_damage),
    ("guardrail_raw_proc_bonus_damage", guardrail_raw_proc_bonus_damage),
    ("guardrail_resource_gain_logs_use_gained", guardrail_resource_gain_logs_use_gained),
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
