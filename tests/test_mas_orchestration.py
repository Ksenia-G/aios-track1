"""Closed-loop orchestrator: authority boundaries and a real two-step loop."""

from __future__ import annotations

import datetime

import pytest

from aios.agents.constraint_guardian import ConstraintGuardian
from aios.mas.arbiter import ArbiterDecision, DecisionBundle
from aios.mas.common import CandidateKind, DecisionRound, ProposalSource
from aios.mas.evidence import EvidenceArtifact
from aios.mas.intents import InjectionAction, ProductionAction
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.orchestration import (
    ClosedLoopOrchestrator,
    DecisionStepInput,
    DecisionTrace,
    OrchestrationError,
    OrchestratorConfig,
    default_next_input,
)
from aios.schedule import InjectorControl, ProducerControl, WellStatus
from tests.support.mas_team_fixtures import (
    SCHEMA_VERSION,
    SYNTHETIC_CONFIG_DIGEST,
    SYNTHETIC_VARIANT_ID,
    synthetic_horizon_context,
    synthetic_injection_agent_output,
    synthetic_memory_slice,
    synthetic_production_agent_output,
    synthetic_state_card,
)
from tests.support.orchestration_doubles import (
    CommittedControlsStateAdvancer,
    DeterministicCandidateExecutor,
    DeterministicCHDD,
    DeterministicRealizedStateExtractor,
    RelabellingCandidateExecutor,
    ScriptedCHDD,
    chdd_result,
)

EFFECTIVE_DATE = datetime.date(2020, 4, 1)
RESOLVER_VERSION = "tests.support.orchestration/1.0.0"


# ---------------------------------------------------------------------------
# Minimal deterministic stand-ins for the decision layer.
# ---------------------------------------------------------------------------


class StubProductionAgent:
    def __init__(self, *, llm_enabled: bool = False) -> None:
        self.llm_enabled = llm_enabled
        self.envelopes: list[object] = []

    def propose(self, envelope: object) -> object:
        self.envelopes.append(envelope)
        return synthetic_production_agent_output()


class StubInjectionAgent:
    def __init__(self, *, llm_enabled: bool = False) -> None:
        self.llm_enabled = llm_enabled
        self.envelopes: list[object] = []

    def propose(self, envelope: object) -> object:
        self.envelopes.append(envelope)
        return synthetic_injection_agent_output()


class StubArbiter:
    def __init__(self, *, round2_request: object = None) -> None:
        self.round2_request = round2_request
        self.calls = 0

    def decide(self, envelope: object) -> ArbiterDecision:
        self.calls += 1
        request = self.round2_request if self.calls == 1 else None
        return ArbiterDecision(
            schema_version=SCHEMA_VERSION,
            arbiter_envelope_digest=envelope.digest(),
            bundles=(
                DecisionBundle(kind=CandidateKind.HOLD),
                DecisionBundle(
                    kind=CandidateKind.DETERMINISTIC_BASELINE,
                    production_proposal_digest=synthetic_production_agent_output().baseline_proposal.digest(),
                    injection_proposal_digest=synthetic_injection_agent_output().baseline_proposal.digest(),
                ),
                DecisionBundle(
                    kind=CandidateKind.AGENT_BUNDLE,
                    production_proposal_digest=synthetic_production_agent_output().primary_proposal.digest(),
                    injection_proposal_digest=synthetic_injection_agent_output().primary_proposal.digest(),
                ),
            ),
            round2_request=request,
        )


def _producer(rate: float, well: str = "SYN-P1") -> ProducerControl:
    return ProducerControl(well=well, status=WellStatus.OPEN, mode="LRAT", liquid_rate=rate)


def _injector(rate: float, well: str = "SYN-I1") -> InjectorControl:
    return InjectorControl(
        well=well, phase="WATER", status=WellStatus.OPEN, mode="RATE", surface_rate=rate
    )


def _candidate(
    kind: CandidateKind,
    *,
    producer_rate: float,
    injector_rate: float,
    effective_date: datetime.date = EFFECTIVE_DATE,
) -> MagnitudeCandidate:
    return MagnitudeCandidate(
        candidate_kind=kind,
        bundle_digest=f"{kind}-bundle".ljust(64, "0"),
        effective_date=effective_date,
        producers=(_producer(producer_rate),),
        injectors=(_injector(injector_rate),),
        source_intent_digests=("d" * 64,),
        resolver_version=RESOLVER_VERSION,
    )


class StubMagnitudeResolver:
    """Turns structural bundles into numerical candidates.

    This is the first place in the pipeline where numerical control exists,
    which is exactly where the architecture puts it.
    """

    def __init__(
        self,
        *,
        hold_controls: tuple[float, float] = (120.0, 150.0),
        agent_controls: tuple[float, float] = (200.0, 150.0),
        baseline_controls: tuple[float, float] = (150.0, 150.0),
        effective_date: datetime.date = EFFECTIVE_DATE,
    ) -> None:
        self.hold_controls = hold_controls
        self.agent_controls = agent_controls
        self.baseline_controls = baseline_controls
        self.effective_date = effective_date

    def resolve(self, decision: ArbiterDecision) -> tuple[MagnitudeCandidate, ...]:
        by_kind = {
            CandidateKind.HOLD: self.hold_controls,
            CandidateKind.DETERMINISTIC_BASELINE: self.baseline_controls,
            CandidateKind.AGENT_BUNDLE: self.agent_controls,
        }
        return tuple(
            _candidate(
                bundle.kind,
                producer_rate=by_kind[bundle.kind][0],
                injector_rate=by_kind[bundle.kind][1],
                effective_date=self.effective_date,
            )
            for bundle in decision.bundles
        )


def _config(**overrides: object) -> OrchestratorConfig:
    fields: dict[str, object] = {
        "variant_id": SYNTHETIC_VARIANT_ID,
        "llm_enabled": False,
        "simulation_budget": 8,
        "resolver_version": RESOLVER_VERSION,
        "guardian_config_digest": SYNTHETIC_CONFIG_DIGEST,
        "chdd_config_digest": SYNTHETIC_CONFIG_DIGEST,
    }
    fields.update(overrides)
    return OrchestratorConfig(**fields)  # type: ignore[arg-type]


def _step_input(**overrides: object) -> DecisionStepInput:
    fields: dict[str, object] = {
        "state": synthetic_state_card(),
        "horizon_context": synthetic_horizon_context(),
        "memory_slice": synthetic_memory_slice(),
        "production_allowed_actions": (ProductionAction.HOLD, ProductionAction.REDUCE_OFFTAKE),
        "injection_allowed_actions": (InjectionAction.HOLD, InjectionAction.REALLOCATE_INJECTION),
    }
    fields.update(overrides)
    return DecisionStepInput(**fields)  # type: ignore[arg-type]


def _orchestrator(**overrides: object) -> ClosedLoopOrchestrator:
    base_state = synthetic_state_card()
    extractor = DeterministicRealizedStateExtractor(base_state=base_state)
    fields: dict[str, object] = {
        "config": _config(),
        "production_agent": StubProductionAgent(),
        "injection_agent": StubInjectionAgent(),
        "arbiter": StubArbiter(),
        "magnitude_resolver": StubMagnitudeResolver(),
        "candidate_executor": DeterministicCandidateExecutor(),
        "realized_state_extractor": extractor,
        "guardian": ConstraintGuardian(),
        "chdd": DeterministicCHDD(),
        "state_advancer": CommittedControlsStateAdvancer(extractor=extractor),
    }
    fields.update(overrides)
    return ClosedLoopOrchestrator(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# O1 -- no silent fake production defaults.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    ["candidate_executor", "realized_state_extractor", "guardian", "chdd", "state_advancer"],
)
def test_missing_authoritative_dependency_fails_closed(missing: str) -> None:
    """A caller who forgets physics, legality or economics gets an error.

    Not a fake success, a fake pass, a hash-derived value and a committed
    candidate.
    """
    with pytest.raises(OrchestrationError, match=missing):
        _orchestrator(**{missing: None})


def test_every_boundary_must_be_supplied_explicitly() -> None:
    with pytest.raises(TypeError):
        ClosedLoopOrchestrator(  # type: ignore[call-arg]
            config=_config(),
            production_agent=StubProductionAgent(),
            injection_agent=StubInjectionAgent(),
            arbiter=StubArbiter(),
            magnitude_resolver=StubMagnitudeResolver(),
        )


# ---------------------------------------------------------------------------
# O4 -- candidate to simulation provenance.
# ---------------------------------------------------------------------------


def test_foreign_execution_relabelled_as_ours_is_rejected() -> None:
    """The whole run belongs to another candidate; only the label says otherwise.

    Trusting `provenance.candidate_digest` alone accepted foreign physics all
    the way through Guardian, CHDD and commit.
    """
    foreign = _candidate(CandidateKind.AGENT_BUNDLE, producer_rate=900.0, injector_rate=1.0)
    orchestrator = _orchestrator(candidate_executor=RelabellingCandidateExecutor(foreign=foreign))
    with pytest.raises(OrchestrationError, match="does not belong to the executed candidate"):
        orchestrator.run_decision_step(_step_input())


def test_every_committed_candidate_is_bound_to_its_own_run() -> None:
    executor = DeterministicCandidateExecutor()
    orchestrator = _orchestrator(candidate_executor=executor)
    result = orchestrator.run_decision_step(_step_input())

    assert result.committed_candidate_digest in executor.executed
    assert result.committed_candidate.digest() == result.committed_candidate_digest
    # Every reported candidate actually reached execution.
    assert {record.candidate_digest for record in result.candidates} == set(executor.executed)


def test_duplicate_candidate_digests_are_refused() -> None:
    class DuplicatingResolver(StubMagnitudeResolver):
        def resolve(self, decision: ArbiterDecision) -> tuple[MagnitudeCandidate, ...]:
            resolved = super().resolve(decision)
            return (*resolved, resolved[-1])

    orchestrator = _orchestrator(magnitude_resolver=DuplicatingResolver())
    with pytest.raises(OrchestrationError, match="duplicate candidate digest"):
        orchestrator.run_decision_step(_step_input())


# ---------------------------------------------------------------------------
# O3 / O8 -- the real Guardian contract decides realized legality.
# ---------------------------------------------------------------------------


def test_real_guardian_is_injectable_and_authoritative() -> None:
    """The repository's own ConstraintGuardian is used as-is, unwrapped."""
    orchestrator = _orchestrator(guardian=ConstraintGuardian())
    result = orchestrator.run_decision_step(_step_input())
    assert result.committed_candidate_digest


def test_guardian_failed_candidate_is_never_committed() -> None:
    """A realized WLPR above the official limit must lose, not merely rank low.

    The agent bundle realizes WLPR 900 against an official wlpr_max of 500.
    """
    orchestrator = _orchestrator(
        magnitude_resolver=StubMagnitudeResolver(agent_controls=(900.0, 150.0))
    )
    result = orchestrator.run_decision_step(_step_input())

    verdicts = {record.candidate_digest: record for record in result.candidates}
    agent = next(
        record
        for record in verdicts.values()
        if record.candidate_kind == str(CandidateKind.AGENT_BUNDLE)
    )
    assert agent.guardian is not None and agent.guardian.passed is False
    assert any("wlpr_max" in violation for violation in agent.guardian.violations)
    assert result.committed_candidate_digest != agent.candidate_digest
    # An illegal candidate is never economically scored at all.
    assert agent.npv_mln_rub is None


def test_failed_simulation_cannot_become_a_pass() -> None:
    resolver = StubMagnitudeResolver()
    decision = StubArbiter().decide(_ArbiterEnvelopeStub())
    agent_digest = next(
        candidate.digest()
        for candidate in resolver.resolve(decision)
        if candidate.candidate_kind is CandidateKind.AGENT_BUNDLE
    )
    orchestrator = _orchestrator(
        magnitude_resolver=resolver,
        candidate_executor=DeterministicCandidateExecutor(
            failing_digests=frozenset({agent_digest})
        ),
    )
    result = orchestrator.run_decision_step(_step_input())

    failed = next(record for record in result.candidates if record.candidate_digest == agent_digest)
    assert failed.simulation_status != "SUCCESS"
    assert failed.guardian is not None and failed.guardian.passed is False
    assert result.committed_candidate_digest != agent_digest


class _ArbiterEnvelopeStub:
    def digest(self) -> str:
        return "e" * 64


def test_no_guardian_passed_candidate_is_a_closed_failure() -> None:
    orchestrator = _orchestrator(
        magnitude_resolver=StubMagnitudeResolver(
            hold_controls=(900.0, 150.0),
            baseline_controls=(900.0, 150.0),
            agent_controls=(900.0, 150.0),
        )
    )
    with pytest.raises(OrchestrationError, match="no candidate is simulated, Guardian-passed"):
        orchestrator.run_decision_step(_step_input())


# ---------------------------------------------------------------------------
# O7 -- economics must be finite and deterministically ordered.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_economics_never_wins_selection(bad: float) -> None:
    """A NaN is not a bad candidate but an incomparable one.

    `max()` never replaces a NaN leader and never yields to it, so a NaN
    reaching the ordering would silently beat objectively better candidates.
    It has to be refused as ineligible before ranking.
    """
    resolver = StubMagnitudeResolver()
    decision = StubArbiter().decide(_ArbiterEnvelopeStub())
    digests = [candidate.digest() for candidate in resolver.resolve(decision)]
    poisoned = digests[0]

    class NonFiniteCHDD:
        def evaluate(self, candidate: MagnitudeCandidate, execution: object) -> object:
            from aios.evaluation.canonical import digest as _digest
            from aios.mas.orchestration.boundaries import ChddEvaluation

            input_digest = _digest(
                {
                    "run": execution.provenance.candidate_digest,
                    "result": execution.result.result_digest,
                }
            )
            value = bad if candidate.digest() == poisoned else 0.9
            return ChddEvaluation(
                input_digest=input_digest,
                result=chdd_result(value, input_digest=input_digest),
            )

    orchestrator = _orchestrator(magnitude_resolver=resolver, chdd=NonFiniteCHDD())
    result = orchestrator.run_decision_step(_step_input())

    assert result.committed_candidate_digest != poisoned
    rejected = {
        rejection.candidate_identity: str(rejection.reason)
        for rejection in result.selection.rejected
    }
    assert rejected[poisoned] == "NON_FINITE_CHDD_VALUE"


def test_selection_is_the_economic_argmax_with_deterministic_tie_break() -> None:
    resolver = StubMagnitudeResolver()
    decision = StubArbiter().decide(_ArbiterEnvelopeStub())
    digests = [candidate.digest() for candidate in resolver.resolve(decision)]

    orchestrator = _orchestrator(
        magnitude_resolver=resolver,
        chdd=ScriptedCHDD(values=dict.fromkeys(digests, 5.0)),
    )
    first = orchestrator.run_decision_step(_step_input())
    second = _orchestrator(
        magnitude_resolver=StubMagnitudeResolver(),
        chdd=ScriptedCHDD(values=dict.fromkeys(digests, 5.0)),
    ).run_decision_step(_step_input())

    assert first.committed_candidate_digest == min(digests)
    assert first.committed_candidate_digest == second.committed_candidate_digest


def test_chdd_failure_is_a_closed_failure() -> None:
    class ExplodingCHDD:
        def evaluate(self, candidate: MagnitudeCandidate, execution: object) -> object:
            raise RuntimeError("economics backend down")

    orchestrator = _orchestrator(chdd=ExplodingCHDD())
    with pytest.raises(RuntimeError, match="economics backend down"):
        orchestrator.run_decision_step(_step_input())


# ---------------------------------------------------------------------------
# O5 -- diagnostics reach both specialists.
# ---------------------------------------------------------------------------


def test_shared_diagnostics_reach_both_specialist_envelopes() -> None:
    pressure = EvidenceArtifact(evidence_id="SYN-pressure-1", kind="pressure_diagnostic")
    sweep = EvidenceArtifact(evidence_id="SYN-sweep-1", kind="sweep_diagnostic")
    geology = EvidenceArtifact(evidence_id="SYN-geology-1", kind="geology_card")
    response = EvidenceArtifact(evidence_id="SYN-response-1", kind="response_map")
    compensation = EvidenceArtifact(evidence_id="SYN-comp-1", kind="compensation_state")
    economics = EvidenceArtifact(evidence_id="SYN-econ-1", kind="economic_context")

    production = StubProductionAgent()
    injection = StubInjectionAgent()
    orchestrator = _orchestrator(production_agent=production, injection_agent=injection)
    orchestrator.run_decision_step(
        _step_input(
            pressure_diagnostic=pressure,
            sweep_diagnostic=sweep,
            geology_card=geology,
            response_map=response,
            compensation_state=compensation,
            economic_context=economics,
        )
    )

    production_envelope = production.envelopes[0]
    assert production_envelope.pressure_diagnostic is pressure
    assert production_envelope.sweep_diagnostic is sweep
    assert production_envelope.geology_card is geology
    assert production_envelope.response_map is response
    assert production_envelope.compensation_state is compensation
    assert production_envelope.economic_context is economics
    assert production_envelope.state_card is not None
    assert production_envelope.horizon_context is not None
    assert production_envelope.memory_slice is not None

    injection_envelope = injection.envelopes[0]
    assert injection_envelope.pressure_diagnostic is pressure
    assert injection_envelope.sweep_diagnostic is sweep
    assert injection_envelope.geology_card is geology
    assert injection_envelope.response_map is response
    assert injection_envelope.compensation_state is compensation
    # Economic context is contractually Production-only.
    assert not hasattr(injection_envelope, "economic_context")


# ---------------------------------------------------------------------------
# O6 / T5 -- a real operational LLM ablation on one topology.
# ---------------------------------------------------------------------------


def test_llm_flag_reaches_the_specialists_through_the_factory() -> None:
    built: list[bool] = []

    def production_factory(llm_enabled: bool) -> StubProductionAgent:
        built.append(llm_enabled)
        return StubProductionAgent(llm_enabled=llm_enabled)

    def injection_factory(llm_enabled: bool) -> StubInjectionAgent:
        return StubInjectionAgent(llm_enabled=llm_enabled)

    disabled = _orchestrator(
        config=_config(llm_enabled=False),
        production_agent=None,
        injection_agent=None,
        production_agent_factory=production_factory,
        injection_agent_factory=injection_factory,
    )
    enabled = _orchestrator(
        config=_config(llm_enabled=True),
        production_agent=None,
        injection_agent=None,
        production_agent_factory=production_factory,
        injection_agent_factory=injection_factory,
    )

    assert built == [False, True]
    assert disabled._production_agent.llm_enabled is False
    assert enabled._production_agent.llm_enabled is True
    assert disabled._injection_agent.llm_enabled is False
    assert enabled._injection_agent.llm_enabled is True


def test_ablation_keeps_one_topology_and_one_config_difference() -> None:
    from aios.mas.invariants import require_ablation_equivalence

    variant_b = _config(llm_enabled=False)
    variant_d = _config(llm_enabled=True)
    require_ablation_equivalence(variant_b.to_json(), variant_d.to_json())

    def production_factory(llm_enabled: bool) -> StubProductionAgent:
        return StubProductionAgent(llm_enabled=llm_enabled)

    def injection_factory(llm_enabled: bool) -> StubInjectionAgent:
        return StubInjectionAgent(llm_enabled=llm_enabled)

    results = []
    for config in (variant_b, variant_d):
        orchestrator = _orchestrator(
            config=config,
            production_agent=None,
            injection_agent=None,
            production_agent_factory=production_factory,
            injection_agent_factory=injection_factory,
        )
        results.append(orchestrator.run_decision_step(_step_input()))

    # Same pipeline, same stages, same deterministic infrastructure.
    assert [len(result.candidates) for result in results] == [3, 3]
    assert results[0].round2_triggered == results[1].round2_triggered


def test_agent_and_factory_are_mutually_exclusive() -> None:
    with pytest.raises(OrchestrationError, match="not both"):
        _orchestrator(
            production_agent=StubProductionAgent(),
            production_agent_factory=lambda enabled: StubProductionAgent(),
        )


# ---------------------------------------------------------------------------
# T3 / T4 / T6.
# ---------------------------------------------------------------------------


def test_hold_and_baseline_always_reach_simulation(  # T3
) -> None:
    executor = DeterministicCandidateExecutor()
    orchestrator = _orchestrator(candidate_executor=executor)
    result = orchestrator.run_decision_step(_step_input())

    kinds = {record.candidate_kind for record in result.candidates}
    assert str(CandidateKind.HOLD) in kinds
    assert str(CandidateKind.DETERMINISTIC_BASELINE) in kinds
    assert len(executor.executed) == 3


def test_missing_simulation_floor_is_refused() -> None:  # T3
    class FloorlessResolver(StubMagnitudeResolver):
        def resolve(self, decision: ArbiterDecision) -> tuple[MagnitudeCandidate, ...]:
            return tuple(
                candidate
                for candidate in super().resolve(decision)
                if candidate.candidate_kind is CandidateKind.AGENT_BUNDLE
            )

    orchestrator = _orchestrator(magnitude_resolver=FloorlessResolver())
    with pytest.raises(Exception, match="simulation_floor"):
        orchestrator.run_decision_step(_step_input())


@pytest.mark.parametrize("mode", ["drop", "add", "duplicate"])
def test_safety_screen_cannot_change_candidate_identity(mode: str) -> None:  # T4
    class MutatingScreen:
        def annotate(
            self, candidates: tuple[MagnitudeCandidate, ...]
        ) -> tuple[MagnitudeCandidate, ...]:
            if mode == "drop":
                return candidates[:-1]
            if mode == "add":
                return (
                    *candidates,
                    _candidate(CandidateKind.AGENT_BUNDLE, producer_rate=1.0, injector_rate=1.0),
                )
            return (*candidates, candidates[0])

    orchestrator = _orchestrator(safety_screen=MutatingScreen())
    with pytest.raises(Exception, match="candidate_preservation"):
        orchestrator.run_decision_step(_step_input())


def test_hold_must_be_an_exact_carry_forward_on_later_steps() -> None:  # T6
    orchestrator = _orchestrator()
    first = orchestrator.run_decision_step(_step_input())

    # The next step resolves HOLD to different numbers than were committed.
    drifting = _orchestrator(magnitude_resolver=StubMagnitudeResolver(hold_controls=(999.0, 999.0)))
    with pytest.raises(Exception, match="hold_carry_forward"):
        drifting.run_decision_step(_step_input(previous_committed=first.committed_candidate))


def test_first_step_has_no_previous_commit_to_carry_forward() -> None:  # T6
    """Step one legitimately has nothing to compare against."""
    step = _step_input()
    assert step.previous_committed is None
    result = _orchestrator().run_decision_step(step)
    assert result.committed_candidate_digest


# ---------------------------------------------------------------------------
# O8 -- a failed step stays auditable.
# ---------------------------------------------------------------------------


def test_failed_step_leaves_a_deterministic_failure_record() -> None:
    trace = DecisionTrace()
    foreign = _candidate(CandidateKind.AGENT_BUNDLE, producer_rate=900.0, injector_rate=1.0)
    orchestrator = _orchestrator(
        candidate_executor=RelabellingCandidateExecutor(foreign=foreign),
        decision_trace=trace,
    )
    with pytest.raises(OrchestrationError):
        orchestrator.run_decision_step(_step_input())

    assert len(trace) == 0  # the commit log stays append-only and clean
    assert len(trace.failures) == 1
    failure = trace.failures[0]
    assert failure.stage == "runtime_identity"
    assert failure.state_card_digest
    assert "identity" in failure.reason
    assert trace.failures_to_json()[0]["stage"] == "runtime_identity"


def test_successful_step_is_appended_to_the_trace() -> None:
    trace = DecisionTrace()
    orchestrator = _orchestrator(decision_trace=trace)
    orchestrator.run_decision_step(_step_input())
    assert len(trace) == 1
    assert trace.failures == ()


# ---------------------------------------------------------------------------
# Round 2 ordering.
# ---------------------------------------------------------------------------


def test_round1_is_isolated_and_round2_carries_the_peer() -> None:
    production = StubProductionAgent()
    injection = StubInjectionAgent()

    def always_conflict(production_output: object, injection_output: object) -> bool:
        return True

    orchestrator = _orchestrator(
        production_agent=production,
        injection_agent=injection,
        conflict_predicate=always_conflict,
    )
    result = orchestrator.run_decision_step(_step_input())

    assert result.round2_triggered is True
    assert production.envelopes[0].round is DecisionRound.ROUND_1
    assert production.envelopes[0].peer_proposals == ()
    assert production.envelopes[1].round is DecisionRound.ROUND_2
    assert production.envelopes[1].peer_proposals[0].role.value == "INJECTION"
    assert injection.envelopes[1].peer_proposals[0].role.value == "PRODUCTION"


def test_at_most_one_round2_per_step() -> None:
    from aios.mas.arbiter import Round2Request
    from aios.mas.common import AgentRole

    def always_conflict(production_output: object, injection_output: object) -> bool:
        return True

    request = Round2Request(
        requested_roles=(AgentRole.PRODUCTION,),
        conflict_code="SYN-CONFLICT",
        proposal_digests=(synthetic_production_agent_output().primary_proposal.digest(),),
    )
    orchestrator = _orchestrator(
        arbiter=StubArbiter(round2_request=request),
        conflict_predicate=always_conflict,
    )
    with pytest.raises(OrchestrationError, match="at most one Round 2"):
        orchestrator.run_decision_step(_step_input())


# ---------------------------------------------------------------------------
# A real two-step closed loop.
# ---------------------------------------------------------------------------


def test_two_sequential_steps_form_a_real_closed_loop() -> None:
    base_state = synthetic_state_card()
    extractor = DeterministicRealizedStateExtractor(base_state=base_state)
    executor = DeterministicCandidateExecutor()
    trace = DecisionTrace()
    orchestrator = _orchestrator(
        candidate_executor=executor,
        realized_state_extractor=extractor,
        state_advancer=CommittedControlsStateAdvancer(extractor=extractor),
        decision_trace=trace,
    )

    first = orchestrator.run_decision_step(_step_input())

    # A HOLD on the next step is by definition the previously committed
    # controls, so the resolver carries them forward exactly (T6).
    committed = first.committed_candidate.canonical()
    orchestrator._magnitude_resolver = StubMagnitudeResolver(
        hold_controls=(
            committed.producers[0].liquid_rate or 0.0,
            committed.injectors[0].surface_rate or 0.0,
        )
    )
    second_input = default_next_input(_step_input(), first)
    second = orchestrator.run_decision_step(second_input)

    # Two steps really ran, and every candidate of both was executed.
    assert len(trace) == 2
    assert len(executor.executed) == 6

    # State genuinely moved: the date advanced and realized well state now
    # reflects what was actually committed, not a copy of the starting card.
    assert second_input.state.metadata.date > _step_input().state.metadata.date
    committed_producer = first.committed_candidate.canonical().producers[0]
    realized = {well.well_id: well for well in second_input.state.wells}
    assert realized["SYN-P1"].wlpr == committed_producer.liquid_rate
    assert second_input.state.digest() != base_state.digest()

    # The horizon shrank by exactly one step.
    assert second_input.horizon_context.remaining_steps == (
        _step_input().horizon_context.remaining_steps - 1
    )

    # Both committed candidates are provenance-bound and Guardian-passed.
    for result in (first, second):
        record = next(
            item
            for item in result.candidates
            if item.candidate_digest == result.committed_candidate_digest
        )
        assert record.guardian is not None and record.guardian.passed
        assert record.simulation_status == "SUCCESS"
        assert record.npv_mln_rub is not None


def test_committed_controls_are_the_only_numerical_authority() -> None:
    """Numerical control appears first at the resolver, never in a proposal."""
    production = StubProductionAgent()
    orchestrator = _orchestrator(production_agent=production)
    result = orchestrator.run_decision_step(_step_input())

    proposal = production.envelopes and synthetic_production_agent_output().primary_proposal
    assert proposal.source in {
        ProposalSource.DETERMINISTIC_FALLBACK,
        ProposalSource.DETERMINISTIC_BASELINE,
        ProposalSource.LLM_LOCAL,
    }
    for intent in proposal.intents:
        payload = intent.to_json()
        assert "liquid_rate" not in payload
        assert "surface_rate" not in payload

    # ... while the committed candidate does carry real numbers.
    assert result.committed_candidate.canonical().producers[0].liquid_rate is not None
