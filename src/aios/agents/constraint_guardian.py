"""Deterministic Constraint Guardian over StateCard only."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from aios.evaluation.canonical import JsonValue
from aios.state import StateCard, WellRole, WellStateSnapshot
from aios.state.schemas import optional_number

CONSTRAINT_GUARDIAN_VERSION = "aios.agents.constraint_guardian/1.0.0"

PRODUCER_BHP_MIN = "producer_bhp_min"
INJECTOR_BHP_MAX = "injector_bhp_max"
WLPR_MAX = "wlpr_max"

PASS = "PASS"
FAIL = "FAIL"


class ConstraintEvaluationError(ValueError):
    """StateCard lacks data required for official constraint evaluation."""


@dataclass(frozen=True, slots=True)
class ConstraintCheck:
    """One official realized-constraint check for one well."""

    well_name: str
    constraint: str
    measured_value: float
    limit: float
    margin: float
    status: str
    near_binding: bool
    reason: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "well_name": self.well_name,
            "constraint": self.constraint,
            "measured_value": self.measured_value,
            "limit": self.limit,
            "margin": self.margin,
            "status": self.status,
            "near_binding": self.near_binding,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ConstraintSummary:
    """Aggregate official constraint status."""

    total_checks: int
    passed: int
    failed: int
    near_binding: int

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "total_checks": self.total_checks,
            "passed": self.passed,
            "failed": self.failed,
            "near_binding": self.near_binding,
        }


@dataclass(frozen=True, slots=True)
class ConstraintReport:
    """Constraint Guardian output consumed by future MAS orchestration."""

    metadata: Mapping[str, str]
    official_checks: tuple[ConstraintCheck, ...]
    summary: ConstraintSummary
    violations: tuple[ConstraintCheck, ...]
    near_binding: tuple[ConstraintCheck, ...]

    @property
    def state_card_digest(self) -> str | None:
        """Digest of the StateCard this verdict was actually computed from.

        The guardian has always recorded it; exposing it lets a consumer check
        that a report belongs to the state it is being used to judge, instead
        of trusting the label of whoever passed it along. A report without it
        is not authoritative.
        """
        value = self.metadata.get("state_card_digest")
        return value or None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "metadata": dict(self.metadata),
            "official_checks": [check.to_json() for check in self.official_checks],
            "summary": self.summary.to_json(),
            "violations": [check.to_json() for check in self.violations],
            "near_binding": [check.to_json() for check in self.near_binding],
        }


@dataclass(frozen=True, slots=True)
class ConstraintGuardian:
    """Authoritative deterministic legality layer over StateCard.

    The guardian evaluates realized official physical constraints only. It does
    not read simulator artifacts, inspect search bounds as official rules,
    optimize, rank, or suggest actions.
    """

    bhp_near_margin_bar: float = 0.5
    wlpr_near_margin_m3_day: float = 5.0
    evaluation_version: str = CONSTRAINT_GUARDIAN_VERSION

    def __post_init__(self) -> None:
        optional_number(self.bhp_near_margin_bar, "constraint_guardian.bhp_near_margin_bar")
        optional_number(self.wlpr_near_margin_m3_day, "constraint_guardian.wlpr_near_margin_m3_day")
        if self.bhp_near_margin_bar < 0:
            raise ValueError("constraint_guardian.bhp_near_margin_bar must be non-negative")
        if self.wlpr_near_margin_m3_day < 0:
            raise ValueError("constraint_guardian.wlpr_near_margin_m3_day must be non-negative")

    def evaluate(self, state_card: StateCard) -> ConstraintReport:
        """Evaluate official realized constraints from StateCard only."""

        official = state_card.constraints.official
        producer_bhp_min = _required_limit(official, PRODUCER_BHP_MIN)
        injector_bhp_max = _required_limit(official, INJECTOR_BHP_MAX)
        wlpr_max = _required_limit(official, WLPR_MAX)

        checks: list[ConstraintCheck] = []
        for well in state_card.wells:
            if well.role is WellRole.PRODUCER:
                checks.append(
                    _min_check(
                        well=well,
                        constraint=PRODUCER_BHP_MIN,
                        measured=_required_value(well.bhp, well.well_id, "WBHP"),
                        limit=producer_bhp_min,
                        near_margin=self.bhp_near_margin_bar,
                        fail_reason="Producer BHP below official limit.",
                    )
                )
                checks.append(
                    _max_check(
                        well=well,
                        constraint=WLPR_MAX,
                        measured=_required_value(well.wlpr, well.well_id, "WLPR"),
                        limit=wlpr_max,
                        near_margin=self.wlpr_near_margin_m3_day,
                        fail_reason="Producer WLPR above official limit.",
                    )
                )
            elif well.role is WellRole.INJECTOR:
                checks.append(
                    _max_check(
                        well=well,
                        constraint=INJECTOR_BHP_MAX,
                        measured=_required_value(well.bhp, well.well_id, "WBHP"),
                        limit=injector_bhp_max,
                        near_margin=self.bhp_near_margin_bar,
                        fail_reason="Injector BHP above official limit.",
                    )
                )
            else:
                raise ConstraintEvaluationError(f"{well.well_id}: missing or unsupported well role")

        violations = tuple(check for check in checks if check.status == FAIL)
        near_binding = tuple(check for check in checks if check.near_binding)
        passed = len(checks) - len(violations)
        return ConstraintReport(
            metadata={
                "state_card_digest": state_card.digest(),
                "evaluation_version": self.evaluation_version,
            },
            official_checks=tuple(checks),
            summary=ConstraintSummary(
                total_checks=len(checks),
                passed=passed,
                failed=len(violations),
                near_binding=len(near_binding),
            ),
            violations=violations,
            near_binding=near_binding,
        )


def _required_limit(official: Mapping[str, Any], constraint: str) -> float:
    try:
        value = official[constraint]
    except KeyError:
        raise ConstraintEvaluationError(f"official constraint {constraint!r} is missing") from None
    if value is None:
        raise ConstraintEvaluationError(f"official constraint {constraint!r} has no limit")
    number = optional_number(value, f"constraints.official.{constraint}")
    assert number is not None
    return number


def _required_value(value: float | None, well_name: str, vector: str) -> float:
    if value is None:
        raise ConstraintEvaluationError(f"{well_name}: missing {vector}")
    number = optional_number(value, f"well.{well_name}.{vector}")
    assert number is not None
    return number


def _min_check(
    *,
    well: WellStateSnapshot,
    constraint: str,
    measured: float,
    limit: float,
    near_margin: float,
    fail_reason: str,
) -> ConstraintCheck:
    margin = measured - limit
    status = PASS if margin >= 0 else FAIL
    return ConstraintCheck(
        well_name=well.well_id,
        constraint=constraint,
        measured_value=measured,
        limit=limit,
        margin=margin,
        status=status,
        near_binding=status == PASS and margin <= near_margin,
        reason="Official constraint passed." if status == PASS else fail_reason,
    )


def _max_check(
    *,
    well: WellStateSnapshot,
    constraint: str,
    measured: float,
    limit: float,
    near_margin: float,
    fail_reason: str,
) -> ConstraintCheck:
    margin = limit - measured
    status = PASS if margin >= 0 else FAIL
    return ConstraintCheck(
        well_name=well.well_id,
        constraint=constraint,
        measured_value=measured,
        limit=limit,
        margin=margin,
        status=status,
        near_binding=status == PASS and margin <= near_margin,
        reason="Official constraint passed." if status == PASS else fail_reason,
    )
