"""Trajectory-wide deterministic Guardian over already-simulated OPM physics.

This module is post-simulation eligibility logic. It never creates controls,
never calls an LLM, and never becomes specialist evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from aios.agents.constraint_guardian import FAIL, ConstraintGuardian, ConstraintReport
from aios.cases.contract import Case
from aios.evaluation.canonical import JsonValue, digest
from aios.opm.summary import SummaryData
from aios.state import GeologyContext, StateCard
from aios.state.extractor import StateExtractor
from aios.state.state_card import WellControlSnapshot

TRAJECTORY_GUARDIAN_VERSION = "aios.agents.trajectory_guardian/1.0.0"
PASS = "PASS"
FAIL_VERDICT = "FAIL"


@dataclass(frozen=True, slots=True)
class TrajectoryConstraintViolation:
    """First-class audit row for one failed realized trajectory check."""

    date: str
    well_name: str
    constraint: str
    metric: str
    measured_value: float
    limit: float
    required_bound: str
    margin: float
    reason: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "date": self.date,
            "well_name": self.well_name,
            "constraint": self.constraint,
            "metric": self.metric,
            "measured_value": self.measured_value,
            "limit": self.limit,
            "required_bound": self.required_bound,
            "margin": self.margin,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TrajectoryConstraintSummary:
    """Aggregate result over every checked report state."""

    report_states_checked: int
    total_checks: int
    failed_checks: int
    first_checked_date: str | None
    last_checked_date: str | None
    first_failing_date: str | None

    @property
    def failed(self) -> int:
        """Compatibility with EconomicSelector's Guardian failure check."""
        return self.failed_checks

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "report_states_checked": self.report_states_checked,
            "total_checks": self.total_checks,
            "failed_checks": self.failed_checks,
            "first_checked_date": self.first_checked_date,
            "last_checked_date": self.last_checked_date,
            "first_failing_date": self.first_failing_date,
        }


@dataclass(frozen=True, slots=True)
class TrajectoryConstraintReport:
    """Trajectory-wide Guardian verdict bound to one simulated candidate run."""

    metadata: Mapping[str, JsonValue]
    summary: TrajectoryConstraintSummary
    violations: tuple[TrajectoryConstraintViolation, ...]
    state_reports: tuple[ConstraintReport, ...]

    @property
    def verdict(self) -> str:
        return PASS if self.summary.failed_checks == 0 else FAIL_VERDICT

    @property
    def first_violation(self) -> TrajectoryConstraintViolation | None:
        return self.violations[0] if self.violations else None

    @property
    def official_checks(self) -> tuple[object, ...]:
        return tuple(check for report in self.state_reports for check in report.official_checks)

    @property
    def state_card_digest(self) -> str | None:
        value = self.metadata.get("state_card_digest")
        return str(value) if value else None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "metadata": dict(self.metadata),
            "verdict": self.verdict,
            "summary": self.summary.to_json(),
            "violations": [violation.to_json() for violation in self.violations],
        }


@dataclass(frozen=True, slots=True)
class TrajectoryConstraintGuardian:
    """Evaluate official realized constraints on every report state in the horizon."""

    state_guardian: ConstraintGuardian = field(default_factory=ConstraintGuardian)
    extractor: StateExtractor = field(default_factory=StateExtractor)
    evaluation_version: str = TRAJECTORY_GUARDIAN_VERSION

    def evaluate_states(
        self,
        states: tuple[StateCard, ...],
        *,
        lineage_state_card_digest: str | None = None,
    ) -> TrajectoryConstraintReport:
        if not states:
            summary = TrajectoryConstraintSummary(
                report_states_checked=0,
                total_checks=0,
                failed_checks=1,
                first_checked_date=None,
                last_checked_date=None,
                first_failing_date=None,
            )
            metadata: dict[str, JsonValue] = {
                "evaluation_version": self.evaluation_version,
                "state_guardian_version": self.state_guardian.evaluation_version,
                "trajectory_verdict": FAIL_VERDICT,
                "trajectory_failure_reason": "NO_REPORT_STATES_CHECKED",
                "post_simulation_eligibility_only": True,
            }
            if lineage_state_card_digest is not None:
                metadata["state_card_digest"] = lineage_state_card_digest
            return TrajectoryConstraintReport(
                metadata=metadata,
                summary=summary,
                violations=(),
                state_reports=(),
            )

        reports = tuple(self.state_guardian.evaluate(state) for state in states)
        violations = tuple(
            _trajectory_violation(state.metadata.date, check)
            for state, report in zip(states, reports, strict=True)
            for check in report.official_checks
            if check.status == FAIL
        )
        dates = tuple(state.metadata.date.isoformat() for state in states)
        summary = TrajectoryConstraintSummary(
            report_states_checked=len(states),
            total_checks=sum(report.summary.total_checks for report in reports),
            failed_checks=len(violations),
            first_checked_date=dates[0] if dates else None,
            last_checked_date=dates[-1] if dates else None,
            first_failing_date=violations[0].date if violations else None,
        )
        metadata: dict[str, JsonValue] = {
            "evaluation_version": self.evaluation_version,
            "state_guardian_version": self.state_guardian.evaluation_version,
            "trajectory_verdict": PASS if summary.failed_checks == 0 else FAIL_VERDICT,
            "post_simulation_eligibility_only": True,
        }
        if lineage_state_card_digest is not None:
            metadata["state_card_digest"] = lineage_state_card_digest
        if dates:
            metadata["checked_report_dates_digest"] = digest({"dates": list(dates)})
        return TrajectoryConstraintReport(
            metadata=metadata,
            summary=summary,
            violations=violations,
            state_reports=reports,
        )

    def evaluate_summary(
        self,
        *,
        summary: SummaryData,
        case: Case,
        run_id: str,
        simulator_digest: str,
        schedule_hash: str,
        well_controls: Mapping[str, WellControlSnapshot],
        geology: GeologyContext | None = None,
        lineage_state_card_digest: str | None = None,
    ) -> TrajectoryConstraintReport:
        states = tuple(
            self.extractor.extract(
                summary=summary,
                case=case,
                run_id=run_id,
                simulator_digest=simulator_digest,
                schedule_hash=schedule_hash,
                well_controls=well_controls,
                geology=geology,
                step=index,
            )
            for index, stamp in enumerate(summary.dates)
            if case.management_start_date <= stamp.date() <= case.management_end_date
        )
        return self.evaluate_states(states, lineage_state_card_digest=lineage_state_card_digest)


def _trajectory_violation(report_date: date, check: Any) -> TrajectoryConstraintViolation:
    metric = "WLPR" if check.constraint == "wlpr_max" else "WBHP"
    operator = "<=" if check.constraint in ("injector_bhp_max", "wlpr_max") else ">="
    return TrajectoryConstraintViolation(
        date=report_date.isoformat(),
        well_name=check.well_name,
        constraint=check.constraint,
        metric=metric,
        measured_value=check.measured_value,
        limit=check.limit,
        required_bound=f"{metric} {operator} {check.limit:g}",
        margin=check.margin,
        reason=check.reason,
    )


__all__ = [
    "TRAJECTORY_GUARDIAN_VERSION",
    "TrajectoryConstraintGuardian",
    "TrajectoryConstraintReport",
    "TrajectoryConstraintSummary",
    "TrajectoryConstraintViolation",
]
