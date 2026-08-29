"""Authority boundary for aios.mas.selection (section 31, A1-A18).

EconomicSelector is a consumer of already-produced evidence. It must never
create numerical controls or candidates, never call the simulator/resolver/
arbiter, never recompute Guardian physics or the CHDD formula, never use
randomness/wall-clock/list-position/candidate-kind to decide the winner.
"""

from __future__ import annotations

import ast
import datetime
from pathlib import Path

import pytest

from aios.agents.constraint_guardian import ConstraintReport, ConstraintSummary
from aios.chdd import AnnualChddRow, ChddResult
from aios.evaluation.simulation import SimulationResult, SimulationStatus
from aios.mas.common import CandidateKind
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.selection import (
    CandidateEvaluation,
    ChddOutcome,
    EconomicSelector,
    GuardianOutcome,
    SimulationOutcome,
    VerifiedChddInput,
    VerifiedExecution,
    VerifiedRealizedState,
)

SEL_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "aios" / "mas" / "selection"
ENGINE_FILE = SEL_SRC_DIR / "economic.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_names_and_modules(tree: ast.Module) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                names.add(alias.name)
    return names, modules


def _called_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_package_exists() -> None:
    assert SEL_SRC_DIR.is_dir()


# A1 / A2 / A3
def test_a1_a2_a3_does_not_construct_control_or_candidate_types() -> None:
    offenders: dict[str, set[str]] = {}
    forbidden_constructors = {"ProducerControl", "InjectorControl", "MagnitudeCandidate"}
    for path in sorted(SEL_SRC_DIR.glob("*.py")):
        called = _called_names(_tree(path))
        hit = called & forbidden_constructors
        if hit:
            offenders[path.name] = hit
    assert offenders == {}, f"selection package must not instantiate: {offenders}"


# A4 / A5 / A6
def test_a4_a5_a6_no_simulator_resolver_or_arbiter_invocation() -> None:
    forbidden_names = {
        "Simulator",
        "SimulatorRuntime",
        "DeterministicMagnitudeResolver",
        "MagnitudeContext",
        "DeterministicArbiter",
    }
    forbidden_module_prefixes = (
        "aios.evaluation.simulator",
        "aios.mas.magnitude_resolution",
        "aios.mas.arbitration",
    )
    offenders: dict[str, set[str]] = {}
    for path in sorted(SEL_SRC_DIR.glob("*.py")):
        names, modules = _imported_names_and_modules(_tree(path))
        hit_names = names & forbidden_names
        hit_modules = {
            module
            for module in modules
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in forbidden_module_prefixes
            )
        }
        hit = hit_names | hit_modules
        if hit:
            offenders[path.name] = hit
    assert offenders == {}, f"forbidden downstream/upstream-authority reference in: {offenders}"


# A7 -- does not compute Guardian physics itself: never touches raw StateCard.
def test_a7_does_not_read_raw_state_card() -> None:
    forbidden_names = {"StateCard", "WellRole", "WellStateSnapshot", "ConstraintGuardian"}
    offenders: dict[str, set[str]] = {}
    for path in sorted(SEL_SRC_DIR.glob("*.py")):
        names, modules = _imported_names_and_modules(_tree(path))
        hit = (names & forbidden_names) | {m for m in modules if m.startswith("aios.state")}
        if hit:
            offenders[path.name] = hit
    assert offenders == {}, f"selection package must consume ConstraintReport only: {offenders}"


# A8 -- does not duplicate the CHDD formula.
def test_a8_no_chdd_formula_vocabulary() -> None:
    forbidden_tokens = ("discount_rate", "capex", "opex", "price_per_ton", "compute_calculation")
    for path in sorted(SEL_SRC_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token not in text, f"{path.name} unexpectedly references {token!r}"


# A9 / A12 / A13 -- no LLM/provider SDK, no random, no wall clock.
def test_a9_a12_a13_no_llm_random_or_wallclock_dependency() -> None:
    forbidden_tokens = (
        "openai",
        "anthropic",
        "import random",
        "from random",
        "time.time",
        "datetime.now",
    )
    for path in sorted(SEL_SRC_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token not in text, f"{path.name} unexpectedly references {token!r}"


# A10 / A11 -- no compensation ratio, no water-inventory hard gate.
def test_a10_a11_no_compensation_or_water_rule() -> None:
    forbidden_tokens = (
        "compensation_ratio",
        "compensation_risk",
        "water_inventory",
        "water_rule",
        "official_water",
    )
    for path in sorted(SEL_SRC_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden_tokens:
            assert token not in text, f"{path.name} unexpectedly references {token!r}"


# A14 -- does not join by list position (no zip, no integer-literal indexing
# into the evaluations argument).
def test_a14_no_positional_join() -> None:
    tree = _tree(ENGINE_FILE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "zip", "economic.py must not zip() separate evaluation lists"
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        ):
            pytest.fail("economic.py must not index evaluations by literal position")


# A15 -- candidate_kind is passed through for audit, never branched on.
def test_a15_candidate_kind_never_used_in_a_branch_condition() -> None:
    tree = _tree(ENGINE_FILE)

    def references_candidate_kind(expr: ast.expr) -> bool:
        return any(
            isinstance(node, ast.Attribute) and node.attr == "candidate_kind"
            for node in ast.walk(expr)
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.If) and references_candidate_kind(node.test):
            pytest.fail("candidate_kind must never gate a branch in economic.py")
        if isinstance(node, ast.Compare) and references_candidate_kind(node):
            pytest.fail("candidate_kind must never be compared in economic.py")


def _candidate(kind: CandidateKind, bundle_digest: str) -> MagnitudeCandidate:
    return MagnitudeCandidate(
        candidate_kind=kind,
        bundle_digest=bundle_digest,
        effective_date=datetime.date(2020, 4, 1),
        resolver_version="test-resolver/1.0.0",
    )


def _evaluation(kind: CandidateKind, bundle_digest: str, npv: float) -> CandidateEvaluation:
    candidate = _candidate(kind, bundle_digest)
    sim_result = SimulationResult.create(
        status=SimulationStatus.SUCCESS,
        request_digest=f"request-{bundle_digest}",
        simulation_key=f"key-{bundle_digest}",
    )
    root = VerifiedExecution(
        candidate_digest=candidate.digest(),
        request_digest=sim_result.request_digest or "",
        simulation_key=sim_result.simulation_key or "",
        result_digest=sim_result.result_digest,
    )
    state_digest = f"state-{bundle_digest}"
    chdd_input_digest = f"chdd-input-{bundle_digest}"
    guardian_report = ConstraintReport(
        metadata={"state_card_digest": state_digest, "evaluation_version": "test/1.0.0"},
        official_checks=(),
        summary=ConstraintSummary(total_checks=0, passed=0, failed=0, near_binding=0),
        violations=(),
        near_binding=(),
    )
    chdd_result = ChddResult(
        input_digest=chdd_input_digest,
        calculator_version="test-chdd/1.0.0",
        provenance="OFFICIAL",
        npv_mln_rub=npv,
        profitability_index=1.0,
        economic_month_count=1,
        months_by_year={"2020": 1},
        yearly_profile=(
            AnnualChddRow(
                year=2020,
                npv_mln_rub=npv,
                cumulative_mln_rub=npv,
                months=1,
                active_well_months=1,
                stop_events=0,
                start_events=0,
            ),
        ),
        start_date="2020-01-01",
        last_economic_month="2020-01",
        source_max_date=None,
        oil_kt_total=0.0,
        liquid_kt_total=0.0,
        injection_k_total=0.0,
        active_well_months=1,
        pump_events=0,
        stop_events=0,
        start_events=0,
        conversion_events=0,
        excluded_negative_rows=0,
        duplicate_well_months=0,
        invalid_dates=0,
        ignored_blank_wells=0,
        warnings=(),
        raw={},
    )
    return CandidateEvaluation(
        candidate=candidate,
        simulation=SimulationOutcome.verified(root, sim_result),
        guardian=GuardianOutcome.verified(
            VerifiedRealizedState(execution=root, state_card_digest=state_digest),
            guardian_report,
        ),
        chdd=ChddOutcome.verified(
            VerifiedChddInput(execution=root, input_digest=chdd_input_digest),
            chdd_result,
        ),
    )


# A16
def test_a16_does_not_favor_hold() -> None:
    hold = _evaluation(CandidateKind.HOLD, "hold", 10.0)
    agent = _evaluation(CandidateKind.AGENT_BUNDLE, "agent", 90.0)
    result = EconomicSelector().select([hold, agent])
    assert result.selected.candidate.candidate_kind is CandidateKind.AGENT_BUNDLE


# A17
def test_a17_does_not_favor_baseline() -> None:
    baseline = _evaluation(CandidateKind.DETERMINISTIC_BASELINE, "baseline", 10.0)
    agent = _evaluation(CandidateKind.AGENT_BUNDLE, "agent", 90.0)
    result = EconomicSelector().select([baseline, agent])
    assert result.selected.candidate.candidate_kind is CandidateKind.AGENT_BUNDLE


# A18
def test_a18_does_not_favor_agent_bundle() -> None:
    agent = _evaluation(CandidateKind.AGENT_BUNDLE, "agent", 10.0)
    hold = _evaluation(CandidateKind.HOLD, "hold", 90.0)
    result = EconomicSelector().select([agent, hold])
    assert result.selected.candidate.candidate_kind is CandidateKind.HOLD


# Frozen contract files unmodified.
def test_frozen_contract_files_unmodified() -> None:
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    frozen_files = [
        "src/aios/mas/__init__.py",
        "src/aios/mas/common.py",
        "src/aios/mas/intents.py",
        "src/aios/mas/envelopes.py",
        "src/aios/mas/proposals.py",
        "src/aios/mas/arbiter.py",
        "src/aios/mas/magnitude.py",
        "src/aios/mas/invariants.py",
        "src/aios/mas/protocols.py",
    ]
    result = subprocess.run(
        ["git", "status", "--porcelain", *frozen_files],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", f"frozen contract files touched: {result.stdout}"


# Guardian / CHDD / Simulator authority: rules and formulas unmodified.
#
# This used to compare `git status --porcelain` for these files, which only
# reported uncommitted dirt: it passed the moment a change was committed and
# said nothing about what the change did. Two lineage-metadata extensions to
# these contracts have since been authorised deliberately, so the guard now
# protects the thing that actually matters -- that no constraint rule, official
# limit or economic formula moved -- while allowing identity metadata to be
# added.

WAVE1_BASE = "dc4dfc090ad8f4aa1d231cad986e02f24f77b727"

# Файлы, чьё правило было пересмотрено НАМЕРЕННО, с новой базой сравнения.
# Пересмотр остаётся ровно таким же событием, как и раньше: он не проходит
# молча, его приходится записать здесь и назвать причину. Ослабления нет --
# от новой базы файл защищён так же строго, как остальные от Wave1.
#
# `chdd/trajectory.py`: строка входа ЧДД датировалась НАЧАЛОМ интервала, тогда
# как все её значения читаются на КОНЦЕ. Дисконтирование годовое, поэтому
# интервал через границу года попадал не в свой год, и годовая добыча
# расходилась с официальной (+26.7% в 2007, -8.9% в 2015) при верных
# суммарных объёмах. База сдвинута на исправление; замороженной осталась
# именно та раскладка, которая воспроизводит официальный расчёт.
REBASED_CONTRACTS = {
    "src/aios/chdd/trajectory.py": "bc899ea6082076a19e98f3e39ea2b6b84b87d22b",
}

# Names whose addition is an authorised lineage extension, not a rule change.
LINEAGE_ADDITIONS = frozenset(
    {
        "state_card_digest",  # ConstraintReport: which StateCard was judged
        "input_digest",  # ChddResult: which trajectory was priced
        "digest",  # ChddInput: canonical identity of that trajectory
        "request_digest",  # SimulationResult: which request produced it
        "simulation_key",
        "is_request_bound",
        "bound_to",
        "canonical_digest",
    }
)

AUTHORITATIVE_FILES = (
    "src/aios/agents/constraint_guardian.py",
    "src/aios/chdd/runner.py",
    "src/aios/chdd/trajectory.py",
    "src/aios/chdd/__init__.py",
    "src/aios/evaluation/simulator.py",
    "src/aios/evaluation/simulation.py",
)


def _base_source(relative_path: str) -> str:
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        ["git", "show", f"{REBASED_CONTRACTS.get(relative_path, WAVE1_BASE)}:{relative_path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _numeric_constants(tree: ast.Module) -> dict[str, object]:
    """Module-level scalar constants: official limits, thresholds, versions."""
    values: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value.value
    return values


def _defaults_and_bodies(tree: ast.Module) -> dict[str, str]:
    """Every function/method body, keyed by qualified name."""
    bodies: dict[str, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                bodies[f"{prefix}{child.name}"] = ast.dump(
                    ast.Module(body=child.body, type_ignores=[])
                )

    visit(tree, "")
    return bodies


@pytest.mark.parametrize("relative_path", AUTHORITATIVE_FILES)
def test_authoritative_rules_and_formulas_are_unchanged(relative_path: str) -> None:
    """Official values and computation bodies must match Wave1 exactly.

    Lineage metadata may be added; a rule, a limit or a formula may not move.
    """
    repo_root = Path(__file__).resolve().parents[1]
    base_tree = ast.parse(_base_source(relative_path))
    head_tree = _tree(repo_root / relative_path)

    base_constants = _numeric_constants(base_tree)
    head_constants = _numeric_constants(head_tree)
    for name, value in base_constants.items():
        assert name in head_constants, f"{relative_path}: constant {name} was removed"
        assert head_constants[name] == value, (
            f"{relative_path}: official value {name} changed from {value!r} "
            f"to {head_constants[name]!r}"
        )

    base_bodies = _defaults_and_bodies(base_tree)
    head_bodies = _defaults_and_bodies(head_tree)
    for name, body in base_bodies.items():
        assert name in head_bodies, f"{relative_path}: {name}() was removed"
        if body == head_bodies[name]:
            continue
        # A changed body is allowed only where the difference is lineage.
        changed = head_bodies[name]
        assert any(addition in changed for addition in LINEAGE_ADDITIONS), (
            f"{relative_path}: {name}() changed for reasons other than lineage metadata"
        )

    added = set(head_bodies) - set(base_bodies)
    for name in added:
        leaf = name.rsplit(".", maxsplit=1)[-1]
        assert leaf in LINEAGE_ADDITIONS, (
            f"{relative_path}: {name}() was added and is not an authorised lineage extension"
        )


def test_guardian_official_limit_keys_are_untouched() -> None:
    """The three official realized constraints, by name and by limit source."""
    from aios.agents.constraint_guardian import (
        INJECTOR_BHP_MAX,
        PRODUCER_BHP_MIN,
        WLPR_MAX,
        ConstraintGuardian,
    )

    assert (PRODUCER_BHP_MIN, INJECTOR_BHP_MAX, WLPR_MAX) == (
        "producer_bhp_min",
        "injector_bhp_max",
        "wlpr_max",
    )
    guardian = ConstraintGuardian()
    assert guardian.bhp_near_margin_bar == 0.5
    assert guardian.wlpr_near_margin_m3_day == 5.0


def test_chdd_still_delegates_every_number_to_the_official_calculator() -> None:
    """No economic arithmetic may migrate into this repository."""
    repo_root = Path(__file__).resolve().parents[1]
    tree = _tree(repo_root / "src/aios/chdd/runner.py")
    assert "compute_calculation" in _called_names(tree)


# Team-zone isolation (section 36).
def test_does_not_touch_team_or_orchestration_zones() -> None:
    import subprocess

    repo_root = Path(__file__).resolve().parents[1]
    team_zones = [
        "src/aios/mas/specialists/injection",
        "src/aios/mas/specialists/production",
        "src/aios/mas/orchestration",
    ]
    result = subprocess.run(
        ["git", "status", "--porcelain", *team_zones],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == ""
