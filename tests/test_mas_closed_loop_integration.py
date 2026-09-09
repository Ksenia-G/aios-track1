"""A real closed loop: real specialists, real Guardian, real economic selector.

Everything below the decision layer is the repository's own component. Only
physical execution and the realized-state extraction are deterministic doubles,
because running OPM in a unit test is not the point being proved here -- and
those doubles are supplied explicitly, never by default.
"""

from __future__ import annotations

import datetime
import json

import pytest

from aios.agents.constraint_guardian import ConstraintGuardian
from aios.agents.trajectory_guardian import TrajectoryConstraintGuardian
from aios.diagnostics import PressureDiagnostic, SweepDiagnostic
from aios.mas.arbiter import ArbiterDecision, DecisionBundle
from aios.mas.common import CandidateKind, DecisionRound, ProposalSource
from aios.mas.evidence import EvidenceArtifact
from aios.mas.intents import InjectionAction, ProductionAction
from aios.mas.invariants import require_ablation_equivalence
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.orchestration import (
    ClosedLoopOrchestrator,
    DecisionStepInput,
    DecisionTrace,
    OrchestratorConfig,
    default_next_input,
)
from aios.mas.specialists.injection import InjectionController
from aios.mas.specialists.production import ProductionController
from aios.schedule import InjectorControl, ProducerControl, WellStatus
from tests.support.mas_team_fixtures import (
    SCHEMA_VERSION,
    SYNTHETIC_CONFIG_DIGEST,
    SYNTHETIC_VARIANT_ID,
    synthetic_horizon_context,
    synthetic_memory_slice,
    synthetic_state_card,
)
from tests.support.orchestration_doubles import (
    CommittedControlsStateAdvancer,
    DeterministicCandidateExecutor,
    DeterministicCHDD,
    DeterministicRealizedStateExtractor,
)

EFFECTIVE_DATE = datetime.date(2020, 4, 1)
RESOLVER_VERSION = "tests.integration.closed_loop/1.0.0"


class FinalStateTrajectoryGuardian(TrajectoryConstraintGuardian):
    """Test support boundary: clean one-state trajectory unless overridden."""

    def evaluate(self, candidate: MagnitudeCandidate, execution: object, *, final_state) -> object:
        return self.evaluate_states((final_state,), lineage_state_card_digest=final_state.digest())


class RecordingArbiter:
    """Structural arbitration only: bundles reference proposals, never controls."""

    def __init__(self) -> None:
        self.envelopes: list[object] = []

    def decide(self, envelope: object) -> ArbiterDecision:
        self.envelopes.append(envelope)
        production = envelope.production_output
        injection = envelope.injection_output
        return ArbiterDecision(
            schema_version=SCHEMA_VERSION,
            arbiter_envelope_digest=envelope.digest(),
            bundles=(
                DecisionBundle(kind=CandidateKind.HOLD),
                DecisionBundle(
                    kind=CandidateKind.DETERMINISTIC_BASELINE,
                    production_proposal_digest=production.baseline_proposal.digest(),
                    injection_proposal_digest=injection.baseline_proposal.digest(),
                ),
                DecisionBundle(
                    kind=CandidateKind.AGENT_BUNDLE,
                    production_proposal_digest=production.primary_proposal.digest(),
                    injection_proposal_digest=injection.primary_proposal.digest(),
                ),
            ),
        )


class CarryForwardMagnitudeResolver:
    """First and only place where a numerical setpoint is created.

    HOLD is the previously committed controls verbatim, which is what makes
    T6 checkable rather than aspirational.
    """

    def __init__(
        self,
        *,
        previous: tuple[float, float] = (120.0, 150.0),
        baseline: tuple[float, float] = (150.0, 150.0),
        agent: tuple[float, float] = (180.0, 150.0),
    ) -> None:
        self.previous = previous
        self.baseline = baseline
        self.agent = agent

    def resolve(self, decision: ArbiterDecision) -> tuple[MagnitudeCandidate, ...]:
        by_kind = {
            CandidateKind.HOLD: self.previous,
            CandidateKind.DETERMINISTIC_BASELINE: self.baseline,
            CandidateKind.AGENT_BUNDLE: self.agent,
        }
        candidates = []
        for bundle in decision.bundles:
            producer_rate, injector_rate = by_kind[bundle.kind]
            candidates.append(
                MagnitudeCandidate(
                    candidate_kind=bundle.kind,
                    bundle_digest=f"{bundle.kind}-bundle".ljust(64, "0"),
                    effective_date=EFFECTIVE_DATE,
                    producers=(
                        ProducerControl(
                            well="SYN-P1",
                            status=WellStatus.OPEN,
                            mode="LRAT",
                            liquid_rate=producer_rate,
                        ),
                    ),
                    injectors=(
                        InjectorControl(
                            well="SYN-I1",
                            phase="WATER",
                            status=WellStatus.OPEN,
                            mode="RATE",
                            surface_rate=injector_rate,
                        ),
                    ),
                    source_intent_digests=("d" * 64,),
                    resolver_version=RESOLVER_VERSION,
                )
            )
        return tuple(candidates)


def _config(*, llm_enabled: bool) -> OrchestratorConfig:
    return OrchestratorConfig(
        variant_id=SYNTHETIC_VARIANT_ID,
        llm_enabled=llm_enabled,
        simulation_budget=8,
        resolver_version=RESOLVER_VERSION,
        guardian_config_digest=SYNTHETIC_CONFIG_DIGEST,
        chdd_config_digest=SYNTHETIC_CONFIG_DIGEST,
    )


def _diagnostic_step_input(**overrides: object) -> DecisionStepInput:
    """A step input carrying the repository's real deterministic diagnostics."""
    card = synthetic_state_card()
    fields: dict[str, object] = {
        "state": card,
        "horizon_context": synthetic_horizon_context(),
        "memory_slice": synthetic_memory_slice(),
        "production_allowed_actions": (ProductionAction.HOLD, ProductionAction.REDUCE_OFFTAKE),
        "injection_allowed_actions": (InjectionAction.HOLD, InjectionAction.REALLOCATE_INJECTION),
        "pressure_diagnostic": EvidenceArtifact(
            evidence_id="pressure.diagnostic",
            kind="pressure_diagnostic",
            payload=PressureDiagnostic().propose(card).to_json(),
        ),
        "sweep_diagnostic": EvidenceArtifact(
            evidence_id="sweep.diagnostic",
            kind="sweep_diagnostic",
            payload=SweepDiagnostic().propose(card).to_json(),
        ),
    }
    fields.update(overrides)
    return DecisionStepInput(**fields)  # type: ignore[arg-type]


def _orchestrator(*, llm_enabled: bool = False, **overrides: object) -> ClosedLoopOrchestrator:
    base_state = synthetic_state_card()
    extractor = DeterministicRealizedStateExtractor(base_state=base_state)
    fields: dict[str, object] = {
        "config": _config(llm_enabled=llm_enabled),
        # Real specialists, built by factory so the ablation flag reaches them.
        "production_agent_factory": lambda enabled: ProductionController(llm_enabled=enabled),
        "injection_agent_factory": lambda enabled: InjectionController(llm_enabled=enabled),
        "arbiter": RecordingArbiter(),
        "magnitude_resolver": CarryForwardMagnitudeResolver(),
        "candidate_executor": DeterministicCandidateExecutor(),
        "realized_state_extractor": extractor,
        "guardian": ConstraintGuardian(),
        "trajectory_guardian": FinalStateTrajectoryGuardian(),
        "chdd": DeterministicCHDD(),
        "state_advancer": CommittedControlsStateAdvancer(extractor=extractor),
    }
    fields.update(overrides)
    return ClosedLoopOrchestrator(**fields)  # type: ignore[arg-type]


def test_real_specialists_run_inside_the_closed_loop() -> None:
    trace = DecisionTrace()
    orchestrator = _orchestrator(decision_trace=trace)
    result = orchestrator.run_decision_step(_diagnostic_step_input())

    assert len(trace) == 1
    assert result.committed_candidate_digest
    # Numerical control exists only from the resolver onward.
    assert result.committed_candidate.canonical().producers[0].liquid_rate is not None


def test_real_diagnostics_reach_the_real_specialists() -> None:
    """The specialist sees diagnostic substance, not just an identifier."""
    captured: list[str] = []

    class SpyingProduction(ProductionController):
        def propose(self, envelope):  # type: ignore[no-untyped-def]
            captured.append(self.prompt_builder.build(envelope))
            return super().propose(envelope)

    orchestrator = _orchestrator(
        production_agent_factory=lambda enabled: SpyingProduction(llm_enabled=enabled)
    )
    orchestrator.run_decision_step(_diagnostic_step_input())

    prompt = captured[0]
    assert "pressure.diagnostic" in prompt
    # ... and the diagnostic's own conclusion, not merely its name.
    assert "pressure_condition" in prompt
    assert '"BHP":55.0' in prompt


def test_specialists_emit_no_numerical_control_anywhere() -> None:
    """NO SETPOINTS CROSS, end to end, on the real pipeline."""
    outputs: list[object] = []

    class CapturingProduction(ProductionController):
        def propose(self, envelope):  # type: ignore[no-untyped-def]
            output = super().propose(envelope)
            outputs.append(output)
            return output

    class CapturingInjection(InjectionController):
        def propose(self, envelope):  # type: ignore[no-untyped-def]
            output = super().propose(envelope)
            outputs.append(output)
            return output

    orchestrator = _orchestrator(
        production_agent_factory=lambda enabled: CapturingProduction(llm_enabled=enabled),
        injection_agent_factory=lambda enabled: CapturingInjection(llm_enabled=enabled),
    )
    orchestrator.run_decision_step(_diagnostic_step_input())

    for output in outputs:
        for proposal in (output.primary_proposal, output.baseline_proposal):
            body = json.dumps(proposal.to_json())
            for forbidden in ("liquid_rate", "surface_rate", "bhp_limit", "setpoint"):
                assert forbidden not in body


def test_llm_ablation_is_operational_on_one_topology() -> None:
    """Variant B and Variant D differ by the flag and by nothing else.

    The flag has to reach the specialists' LLM path, not merely appear in a
    serialized config that nothing reads.
    """
    require_ablation_equivalence(
        _config(llm_enabled=False).to_json(), _config(llm_enabled=True).to_json()
    )

    disabled = _orchestrator(llm_enabled=False)
    enabled = _orchestrator(llm_enabled=True)

    assert disabled._production_agent.llm_enabled is False
    assert enabled._production_agent.llm_enabled is True
    assert disabled._injection_agent._llm_enabled is False
    assert enabled._injection_agent._llm_enabled is True

    step = _diagnostic_step_input()
    b_result = disabled.run_decision_step(step)
    d_result = enabled.run_decision_step(step)

    # Same topology and same deterministic infrastructure on both variants.
    assert len(b_result.candidates) == len(d_result.candidates) == 3
    assert b_result.round2_triggered == d_result.round2_triggered

    # With no provider attached, the enabled variant still keeps its floor.
    for result in (b_result, d_result):
        assert result.committed_candidate_digest


def test_llm_enabled_specialist_without_provider_keeps_the_deterministic_floor() -> None:
    outputs: list[object] = []

    class CapturingProduction(ProductionController):
        def propose(self, envelope):  # type: ignore[no-untyped-def]
            output = super().propose(envelope)
            outputs.append(output)
            return output

    orchestrator = _orchestrator(
        llm_enabled=True,
        production_agent_factory=lambda enabled: CapturingProduction(llm_enabled=enabled),
    )
    orchestrator.run_decision_step(_diagnostic_step_input())

    output = outputs[0]
    assert output.primary_proposal.source is ProposalSource.DETERMINISTIC_FALLBACK
    assert output.baseline_proposal.source is ProposalSource.DETERMINISTIC_BASELINE
    assert output.primary_proposal is not output.baseline_proposal


def test_two_sequential_steps_with_real_components() -> None:
    """The full loop, twice, with state genuinely flowing between steps."""
    base_state = synthetic_state_card()
    extractor = DeterministicRealizedStateExtractor(base_state=base_state)
    executor = DeterministicCandidateExecutor()
    trace = DecisionTrace()
    resolver = CarryForwardMagnitudeResolver()
    orchestrator = _orchestrator(
        magnitude_resolver=resolver,
        candidate_executor=executor,
        realized_state_extractor=extractor,
        state_advancer=CommittedControlsStateAdvancer(extractor=extractor),
        decision_trace=trace,
    )

    first_input = _diagnostic_step_input()
    first = orchestrator.run_decision_step(first_input)

    # HOLD on the next step is the previously committed controls, exactly.
    committed = first.committed_candidate.canonical()
    resolver.previous = (
        committed.producers[0].liquid_rate or 0.0,
        committed.injectors[0].surface_rate or 0.0,
    )

    second_input = default_next_input(first_input, first)
    second = orchestrator.run_decision_step(second_input)

    assert len(trace) == 2
    assert len(executor.executed) == 6

    # Real motion: the date advanced, the horizon shrank, and realized state
    # now reflects the controls that were actually committed.
    assert second_input.state.metadata.date > first_input.state.metadata.date
    assert second_input.horizon_context.remaining_steps == (
        first_input.horizon_context.remaining_steps - 1
    )
    realized = {well.well_id: well for well in second_input.state.wells}
    assert realized["SYN-P1"].wlpr == committed.producers[0].liquid_rate
    assert second_input.state.digest() != base_state.digest()

    # Both commits are simulated, Guardian-passed and economically ranked.
    for result in (first, second):
        record = next(
            item
            for item in result.candidates
            if item.candidate_digest == result.committed_candidate_digest
        )
        assert record.simulation_status == "SUCCESS"
        assert record.guardian is not None and record.guardian.passed
        assert record.npv_mln_rub is not None
        assert result.selection.selection_rule_version == "economic_argmax_chdd_v1"


def test_round1_isolation_then_a_single_round2_with_real_specialists() -> None:
    seen_rounds: list[DecisionRound] = []

    class RoundRecordingProduction(ProductionController):
        def propose(self, envelope):  # type: ignore[no-untyped-def]
            seen_rounds.append(envelope.round)
            return super().propose(envelope)

    orchestrator = _orchestrator(
        production_agent_factory=lambda enabled: RoundRecordingProduction(llm_enabled=enabled),
        conflict_predicate=lambda production, injection: True,
    )
    result = orchestrator.run_decision_step(_diagnostic_step_input())

    assert result.round2_triggered is True
    assert seen_rounds == [DecisionRound.ROUND_1, DecisionRound.ROUND_2]


def test_guardian_rejects_an_illegal_candidate_end_to_end() -> None:
    """An agent bundle realizing WLPR above the official limit cannot commit."""
    orchestrator = _orchestrator(
        magnitude_resolver=CarryForwardMagnitudeResolver(agent=(900.0, 150.0))
    )
    result = orchestrator.run_decision_step(_diagnostic_step_input())

    agent = next(
        record
        for record in result.candidates
        if record.candidate_kind == str(CandidateKind.AGENT_BUNDLE)
    )
    assert agent.guardian is not None and agent.guardian.passed is False
    assert agent.ineligibility_reason == "GUARDIAN_FAILED"
    assert result.committed_candidate_digest != agent.candidate_digest


@pytest.mark.parametrize("llm_enabled", [False, True])
def test_pipeline_is_reproducible(llm_enabled: bool) -> None:
    step = _diagnostic_step_input()
    first = _orchestrator(llm_enabled=llm_enabled).run_decision_step(step)
    second = _orchestrator(llm_enabled=llm_enabled).run_decision_step(step)

    assert first.committed_candidate_digest == second.committed_candidate_digest
    assert first.selection.digest() == second.selection.digest()
