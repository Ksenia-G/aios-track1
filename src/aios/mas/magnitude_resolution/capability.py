"""Какие структурные действия текущий профиль умеет превратить в числа.

Специалисту незачем предлагать действие, которое детерминированный резолвер
всё равно не материализует: LLM тратит на него решение, Arbiter собирает
bundle, и всё это отбрасывается перед численным разрешением. Именно так
терялись все пять живых Production-предложений в MAS-PERF-001.

Это маска исполнимости, а не удаление доменного понятия. Замороженный
`ProductionAction` не меняется: `REDUCE_OFFTAKE` остаётся частью словаря и
контракта, он просто не предлагается как исполнимый, пока у него нет
evidence-backed якоря. Появится якорь -- маска пропустит его обратно сама,
без правки словаря.

Поддержка выводится из того, что резолвер и профиль действительно умеют, а
не из отдельного списка, который пришлось бы синхронизировать вручную.
"""

from __future__ import annotations

from dataclasses import dataclass

from aios.mas.common import Intensity
from aios.mas.intents import InjectionAction, ProductionAction
from aios.mas.magnitude_resolution.profile import ModelYEmpiricalMagnitudeProfile

#: Интенсивности, на которых действие обязано иметь якорь, чтобы считаться
#: исполнимым. NONE исключён намеренно: он законен только у HOLD/SHUT_IN/REOPEN,
#: которым числовой якорь не нужен вовсе.
_MAGNITUDE_INTENSITIES = (Intensity.SMALL, Intensity.MODERATE, Intensity.AGGRESSIVE)

#: Действия, которые резолвер выполняет без обращения к числовому якорю:
#: они переносят или гасят уже существующие управления, а не масштабируют их.
_PRODUCTION_ANCHORLESS = frozenset(
    {ProductionAction.HOLD, ProductionAction.SHUT_IN, ProductionAction.REOPEN}
)
_INJECTION_ANCHORLESS = frozenset({InjectionAction.HOLD})


@dataclass(frozen=True, slots=True)
class ActionCapability:
    """Исполнимость одного действия и причина, если его нет."""

    action: str
    executable: bool
    reason: str

    def to_json(self) -> dict[str, object]:
        return {"action": self.action, "executable": self.executable, "reason": self.reason}


def _anchored_intensities(lookup) -> tuple[Intensity, ...]:
    return tuple(level for level in _MAGNITUDE_INTENSITIES if lookup(level) is not None)


def production_capabilities(
    profile: ModelYEmpiricalMagnitudeProfile,
) -> tuple[ActionCapability, ...]:
    """Исполнимость каждого ProductionAction при данном профиле."""
    capabilities: list[ActionCapability] = []
    for action in ProductionAction:
        if action in _PRODUCTION_ANCHORLESS:
            capabilities.append(
                ActionCapability(str(action), True, "resolver carries or clears existing controls")
            )
            continue
        if action is ProductionAction.INCREASE_OFFTAKE:
            anchored = _anchored_intensities(profile.producer_uplift_factor)
            capabilities.append(
                ActionCapability(
                    str(action),
                    bool(anchored),
                    (
                        f"empirical uplift anchors for {[str(i) for i in anchored]}"
                        if anchored
                        else "no empirical uplift anchor"
                    ),
                )
            )
            continue
        if action is ProductionAction.REDUCE_OFFTAKE:
            anchored = _anchored_intensities(getattr(profile, "producer_reduction_factor", _none))
            capabilities.append(
                ActionCapability(
                    str(action),
                    bool(anchored),
                    (
                        f"empirical reduction anchors for {[str(i) for i in anchored]}"
                        if anchored
                        else (
                            "no empirical reduction anchor: EXP-004 calibrated the producer "
                            "family upward only (B_PROD_110..500); a reduction magnitude would "
                            "be an invented assumption"
                        )
                    ),
                )
            )
            continue
        capabilities.append(
            ActionCapability(str(action), False, "no resolver branch in MAGNITUDE-RESOLVER-001")
        )
    return tuple(capabilities)


def injection_capabilities(
    profile: ModelYEmpiricalMagnitudeProfile,
) -> tuple[ActionCapability, ...]:
    """Исполнимость каждого InjectionAction при данном профиле."""
    capabilities: list[ActionCapability] = []
    for action in InjectionAction:
        if action in _INJECTION_ANCHORLESS:
            capabilities.append(ActionCapability(str(action), True, "no magnitude required"))
            continue
        if action in {
            InjectionAction.SCALE_TOTAL_INJECTION,
            InjectionAction.SUPPORT_REGION,
            InjectionAction.REDUCE_SUPPORT_PATH,
        }:
            up = action is not InjectionAction.REDUCE_SUPPORT_PATH
            anchored = _anchored_intensities(
                lambda level, up=up: profile.injection_scale_factor(level, direction_up=up)
            )
            capabilities.append(
                ActionCapability(
                    str(action),
                    bool(anchored),
                    (
                        f"empirical injection-scale anchors for {[str(i) for i in anchored]}"
                        if anchored
                        else "no empirical injection-scale anchor"
                    ),
                )
            )
            continue
        if action is InjectionAction.REALLOCATE_INJECTION:
            anchored = _anchored_intensities(profile.reallocation_transfer_fraction)
            capabilities.append(
                ActionCapability(
                    str(action),
                    bool(anchored),
                    (
                        f"empirical transfer-fraction anchors for {[str(i) for i in anchored]}"
                        if anchored
                        else "no empirical transfer-fraction anchor"
                    ),
                )
            )
            continue
        capabilities.append(
            ActionCapability(str(action), False, "no resolver branch in MAGNITUDE-RESOLVER-001")
        )
    return tuple(capabilities)


def _none(_intensity: Intensity) -> None:
    """Профиль без якоря сокращения: действие остаётся неисполнимым."""
    return


def supported_production_actions(
    profile: ModelYEmpiricalMagnitudeProfile | None = None,
) -> tuple[ProductionAction, ...]:
    """Исполнимые Production-действия. HOLD присутствует всегда."""
    resolved = profile or ModelYEmpiricalMagnitudeProfile()
    executable = {item.action for item in production_capabilities(resolved) if item.executable}
    return tuple(action for action in ProductionAction if str(action) in executable)


def supported_injection_actions(
    profile: ModelYEmpiricalMagnitudeProfile | None = None,
) -> tuple[InjectionAction, ...]:
    """Исполнимые Injection-действия. HOLD присутствует всегда."""
    resolved = profile or ModelYEmpiricalMagnitudeProfile()
    executable = {item.action for item in injection_capabilities(resolved) if item.executable}
    return tuple(action for action in InjectionAction if str(action) in executable)


__all__ = [
    "ActionCapability",
    "injection_capabilities",
    "production_capabilities",
    "supported_injection_actions",
    "supported_production_actions",
]
