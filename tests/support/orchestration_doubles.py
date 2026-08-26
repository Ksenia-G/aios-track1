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

from aios.evaluation.canonical import digest
from aios.evaluation.simulation import (
    SimulationFailure,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorIdentity,
)
from aios.execution import ExecutionMode, RuntimeExecution, RuntimeProvenance
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.orchestration import EconomicResult
from aios.state import StateCard, WellRole

SIMULATOR_IDENTITY = SimulatorIdentity(
    simulator_id="tests.support.orchestration_doubles",
    simulator_version="1.0.0",
)


def _provenance(
    candidate_digest: str, *, bundle_digest: str, resolver_version: str
) -> RuntimeProvenance:
    return RuntimeProvenance(
        execution_mode=ExecutionMode.FULL,
        simulation_key=digest({"candidate_digest": candidate_digest}),
        candidate_digest=candidate_digest,
        candidate_kind="",
        bundle_digest=bundle_digest,
        resolver_version=resolver_version,
        compiler_id="tests.support.orchestration_doubles",
        compiler_version="1.0.0",
        schedule_sha256=digest({"schedule": candidate_digest}),
        baseline_control_digest=digest({"baseline": candidate_digest}),
        action_control_digest=digest({"action": candidate_digest}),
        simulator_identity_json=SIMULATOR_IDENTITY.to_json(),
    )


def _execution(
    candidate: MagnitudeCandidate,
    result: SimulationResult,
    *,
    stamped_digest: str | None = None,
) -> RuntimeExecution:
    canonical = candidate.canonical()
    candidate_digest = canonical.digest()
    request = SimulationRequest(
        payload={"candidate_digest": candidate_digest},
        seed=None,
        model_digest=None,
        scenario_digest=canonical.bundle_digest,
        schedule_digest=digest({"schedule": candidate_digest}),
    )
    return RuntimeExecution(
        request=request,
        result=result,
        provenance=_provenance(
            stamped_digest if stamped_digest is not None else candidate_digest,
            bundle_digest=canonical.bundle_digest,
            resolver_version=canonical.resolver_version,
        ),
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
        if candidate_digest in self.failing_digests:
            result = SimulationResult.create(
                status=SimulationStatus.CRASHED,
                failure=SimulationFailure(
                    message="deterministic double: candidate marked as failing",
                    details={"stage": "double.execute"},
                ),
            )
        else:
            result = SimulationResult.create(
                status=SimulationStatus.SUCCESS,
                metadata={"candidate_digest": candidate_digest},
            )
        return _execution(candidate, result)


@dataclass
class MiswiredCandidateExecutor:
    """Returns a run that belongs to a different candidate.

    Models a swapped or wrongly wired runtime. Nothing downstream can notice
    this by looking at the `SimulationResult` alone, which is precisely why the
    orchestrator has to check runtime provenance.
    """

    foreign_digest: str

    def execute(self, candidate: MagnitudeCandidate, *, run_label: str) -> RuntimeExecution:
        result = SimulationResult.create(
            status=SimulationStatus.SUCCESS,
            metadata={"candidate_digest": self.foreign_digest},
        )
        return _execution(candidate, result, stamped_digest=self.foreign_digest)


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


@dataclass(frozen=True, slots=True)
class DeterministicCHDD:
    """Reproducible economics derived from realized rates, never from a hash.

    A hash-derived pseudo-value looks like an economic ranking and is not one.
    """

    def evaluate(
        self, candidate: MagnitudeCandidate, execution: RuntimeExecution
    ) -> EconomicResult:
        canonical = candidate.canonical()
        produced = sum(control.liquid_rate or 0.0 for control in canonical.producers)
        injected = sum(control.surface_rate or 0.0 for control in canonical.injectors)
        return EconomicResult(
            value=produced - 0.25 * injected,
            details={"produced": produced, "injected": injected},
        )


@dataclass(frozen=True, slots=True)
class ScriptedCHDD:
    """Returns a prepared value per candidate digest, for ordering tests."""

    values: dict[str, float]

    def evaluate(
        self, candidate: MagnitudeCandidate, execution: RuntimeExecution
    ) -> EconomicResult:
        return EconomicResult(value=self.values[candidate.canonical().digest()])


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
    "MiswiredCandidateExecutor",
    "ScriptedCHDD",
]
