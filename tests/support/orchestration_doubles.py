"""Deterministic test doubles for the closed-loop orchestrator boundaries.

TEST SUPPORT ONLY -- no module under `src/aios` may import this (TF14/TF15).
These live here, and not beside the orchestrator, on purpose: a fake executor
that reports SUCCESS, a fake Guardian that passes everything and a fake CHDD
that scores a hash are exactly the components that must never be reachable by
default from production wiring.

The executor is a deterministic stand-in for expensive physical execution, not
a stand-in for the runtime contract: it returns a real `RuntimeExecution` whose
`RuntimeProvenance.candidate_digest` is bound to the candidate that was
executed, so the orchestrator's provenance check is exercised for real.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, replace

from aios.chdd.runner import ChddResult
from aios.evaluation.canonical import digest
from aios.evaluation.simulation import (
    SimulationFailure,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorIdentity,
    compute_request_digest,
    compute_simulation_key,
)
from aios.execution import ExecutionMode, RuntimeExecution, RuntimeProvenance
from aios.mas.magnitude import MagnitudeCandidate
from aios.state import StateCard, WellRole

SIMULATOR_IDENTITY = SimulatorIdentity(
    simulator_id="tests.support.orchestration_doubles",
    simulator_version="1.0.0",
)


def schedule_digest_for(candidate_digest: str) -> str:
    """Stand-in for the compiled schedule identity of a candidate."""
    return digest({"schedule": candidate_digest})


def build_request(candidate: MagnitudeCandidate) -> SimulationRequest:
    """Build a request the way the real SimulatorRuntime builds one.

    The real runtime puts the candidate's own identity into the request, which
    is what lets a foreign request be recognised regardless of what a
    provenance record claims. A double that omitted this would make the
    orchestrator's identity check untestable.
    """
    canonical = candidate.canonical()
    candidate_digest = canonical.digest()
    return SimulationRequest(
        payload={
            "candidate_digest": candidate_digest,
            "candidate_kind": str(canonical.candidate_kind),
            "effective_date": canonical.effective_date.isoformat(),
            "compiler_id": "tests.support.orchestration_doubles",
            "compiler_version": "1.0.0",
        },
        seed=None,
        model_digest=None,
        scenario_digest=canonical.bundle_digest,
        schedule_digest=schedule_digest_for(candidate_digest),
    )


def request_binding(request: SimulationRequest) -> dict[str, str]:
    """The request lineage a real runtime stamps onto every result it returns."""
    return {
        "request_digest": compute_request_digest(request),
        "simulation_key": compute_simulation_key(request, SIMULATOR_IDENTITY),
    }


def build_provenance(
    candidate: MagnitudeCandidate,
    request: SimulationRequest,
    *,
    stamped_digest: str | None = None,
    stamped_schedule_digest: str | None = None,
    simulation_key: str | None = None,
) -> RuntimeProvenance:
    """Provenance derived from the request, as the real runtime derives it."""
    canonical = candidate.canonical()
    candidate_digest = canonical.digest()
    return RuntimeProvenance(
        execution_mode=ExecutionMode.FULL,
        simulation_key=(
            simulation_key
            if simulation_key is not None
            else compute_simulation_key(request, SIMULATOR_IDENTITY)
        ),
        candidate_digest=(stamped_digest if stamped_digest is not None else candidate_digest),
        candidate_kind=str(canonical.candidate_kind),
        bundle_digest=canonical.bundle_digest,
        resolver_version=canonical.resolver_version,
        compiler_id="tests.support.orchestration_doubles",
        compiler_version="1.0.0",
        schedule_sha256=(
            stamped_schedule_digest
            if stamped_schedule_digest is not None
            else request.schedule_digest
        ),
        baseline_control_digest=digest({"baseline": candidate_digest}),
        action_control_digest=digest({"action": candidate_digest}),
        simulator_identity_json=SIMULATOR_IDENTITY.to_json(),
    )


def _execution(candidate: MagnitudeCandidate, result: SimulationResult) -> RuntimeExecution:
    request = build_request(candidate)
    return RuntimeExecution(
        request=request,
        result=result,
        provenance=build_provenance(candidate, request),
    )


@dataclass
class DeterministicCandidateExecutor:
    """Executes a candidate into a reproducible physical result.

    Every run is recorded, so a test can assert which candidates actually
    reached execution rather than trusting the pipeline's own report.
    """

    failing_digests: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        self.executed: list[str] = []
        self.run_labels: list[str] = []

    def execute(self, candidate: MagnitudeCandidate, *, run_label: str) -> RuntimeExecution:
        candidate_digest = candidate.canonical().digest()
        self.executed.append(candidate_digest)
        self.run_labels.append(run_label)
        request = build_request(candidate)
        binding = request_binding(request)
        if candidate_digest in self.failing_digests:
            result = SimulationResult.create(
                status=SimulationStatus.CRASHED,
                failure=SimulationFailure(
                    message="deterministic double: candidate marked as failing",
                    details={"stage": "double.execute"},
                ),
                **binding,
            )
        else:
            result = SimulationResult.create(
                status=SimulationStatus.SUCCESS,
                metadata={"candidate_digest": candidate_digest},
                **binding,
            )
        return RuntimeExecution(
            request=request,
            result=result,
            provenance=build_provenance(candidate, request),
        )


@dataclass
class RelabellingCandidateExecutor:
    """Returns candidate `foreign`'s whole execution, relabelled as the request.

    This is the sharpest form of the attack: the request and the physical
    result both belong to another candidate, and only the provenance label is
    rewritten to name the candidate that was actually asked for. An
    orchestrator that trusts that single field accepts foreign physics.
    """

    foreign: MagnitudeCandidate

    def execute(self, candidate: MagnitudeCandidate, *, run_label: str) -> RuntimeExecution:
        foreign_request = build_request(self.foreign)
        foreign_result = SimulationResult.create(
            status=SimulationStatus.SUCCESS,
            metadata={"candidate_digest": self.foreign.canonical().digest()},
            **request_binding(foreign_request),
        )
        return RuntimeExecution(
            request=foreign_request,
            result=foreign_result,
            provenance=build_provenance(
                self.foreign,
                foreign_request,
                stamped_digest=candidate.canonical().digest(),
            ),
        )


@dataclass(frozen=True, slots=True)
class DeterministicRealizedStateExtractor:
    """Derives realized well state from the controls the candidate committed.

    Deliberately monotone and simple: a higher liquid rate realizes a higher
    WLPR and a lower producer BHP, a higher injection rate realizes a higher
    injector BHP. That is enough for the real ConstraintGuardian's official
    limits to actually bind, so a Guardian failure in a test is a real verdict
    about realized state rather than a flag someone set by hand.
    """

    base_state: StateCard

    def extract(self, candidate: MagnitudeCandidate, execution: RuntimeExecution) -> StateCard:
        canonical = candidate.canonical()
        producers = {control.well: control for control in canonical.producers}
        injectors = {control.well: control for control in canonical.injectors}
        state: StateCard = self.base_state
        wells = []
        for well in state.wells:
            if well.role is WellRole.PRODUCER and well.well_id in producers:
                rate = producers[well.well_id].liquid_rate or 0.0
                wells.append(
                    replace(
                        well,
                        wlpr=rate,
                        bhp=max(300.0 - 0.5 * rate, 0.0),
                        current_target=rate,
                    )
                )
            elif well.role is WellRole.INJECTOR and well.well_id in injectors:
                rate = injectors[well.well_id].surface_rate or 0.0
                wells.append(
                    replace(
                        well,
                        wwir=rate,
                        bhp=100.0 + 0.5 * rate,
                        current_target=rate,
                    )
                )
            else:
                wells.append(well)
        return replace(state, wells=tuple(wells))


def chdd_result(npv_mln_rub: float, *, provenance: str = "tests.support") -> ChddResult:
    """Minimal well-formed ChddResult carrying a given NPV."""
    return ChddResult(
        calculator_version="tests.support.orchestration_doubles/1.0.0",
        provenance=provenance,
        npv_mln_rub=npv_mln_rub,
        profitability_index=1.0,
        economic_month_count=1,
        months_by_year={"2020": 1},
        yearly_profile=(),
        start_date="2020-04-01",
        last_economic_month="2020-04",
        source_max_date="2020-04-30",
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


@dataclass(frozen=True, slots=True)
class DeterministicCHDD:
    """Reproducible economics derived from realized rates, never from a hash.

    A hash-derived pseudo-value looks like an economic ranking and is not one.
    """

    def evaluate(self, candidate: MagnitudeCandidate, execution: RuntimeExecution) -> ChddResult:
        canonical = candidate.canonical()
        produced = sum(control.liquid_rate or 0.0 for control in canonical.producers)
        injected = sum(control.surface_rate or 0.0 for control in canonical.injectors)
        return chdd_result(produced - 0.25 * injected)


@dataclass(frozen=True, slots=True)
class ScriptedCHDD:
    """Returns a prepared NPV per candidate digest, for ordering tests."""

    values: dict[str, float]

    def evaluate(self, candidate: MagnitudeCandidate, execution: RuntimeExecution) -> ChddResult:
        return chdd_result(self.values[candidate.canonical().digest()])


@dataclass(frozen=True, slots=True)
class CommittedControlsStateAdvancer:
    """Advances the date and carries the committed controls into next state.

    The next step therefore starts from what was actually committed, which is
    what makes a two-step run a closed loop instead of metadata-only motion.
    """

    extractor: DeterministicRealizedStateExtractor
    step_days: int = 30

    def advance(
        self,
        state: StateCard,
        candidate: MagnitudeCandidate,
        execution: RuntimeExecution,
    ) -> StateCard:
        realized = self.extractor.extract(candidate, execution)
        next_date = state.metadata.date + datetime.timedelta(days=self.step_days)
        metadata = replace(
            state.metadata,
            date=next_date,
            run_id=f"{state.metadata.run_id}+1",
        )
        return replace(realized, metadata=metadata)


__all__ = [
    "SIMULATOR_IDENTITY",
    "CommittedControlsStateAdvancer",
    "DeterministicCHDD",
    "DeterministicCandidateExecutor",
    "DeterministicRealizedStateExtractor",
    "RelabellingCandidateExecutor",
    "ScriptedCHDD",
    "chdd_result",
]
