"""MagnitudeCandidate -- первая граница, где numerical setpoints легальны.

До этого модуля во всём пакете aios.mas запрещены LRAT=..., RATE=...,
BHP=..., surface_rate=...: LLM и специалисты работают только со структурными
intents/proposals/bundles. Здесь эта граница физически заканчивается: вместо
нового ProducerSetpoint/InjectorSetpoint используются уже существующие
authoritative типы aios.schedule.ProducerControl/InjectorControl. Ни правила
MagnitudeResolver (SMALL/MODERATE/AGGRESSIVE -> конкретная ставка), ни расчёт
compensation, ни сам выбор setpoints здесь не реализованы -- это отдельная
задача.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from aios.evaluation.canonical import JsonValue, digest
from aios.mas.common import CandidateKind
from aios.schedule import InjectorControl, ProducerControl, natural_key


class MagnitudeCandidateError(ValueError):
    """MagnitudeCandidate contract нарушен."""


def _require_text(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MagnitudeCandidateError(f"{where}: expected non-empty string")
    return value


def producer_control_to_json(control: ProducerControl) -> dict[str, JsonValue]:
    return {
        "well": control.well,
        "status": str(control.status),
        "mode": control.mode,
        "liquid_rate": control.liquid_rate,
        "bhp_limit": control.bhp_limit,
    }


def injector_control_to_json(control: InjectorControl) -> dict[str, JsonValue]:
    return {
        "well": control.well,
        "phase": control.phase,
        "status": str(control.status),
        "mode": control.mode,
        "surface_rate": control.surface_rate,
        "bhp_limit": control.bhp_limit,
    }


@dataclass(frozen=True, slots=True)
class MagnitudeCandidate:
    """Один прогоняемый кандидат: structural bundle + фактические controls.

    `producers`/`injectors` -- уже существующий authoritative numerical
    control boundary (aios.schedule.ProducerControl/InjectorControl), не
    новый параллельный тип.
    """

    candidate_kind: CandidateKind
    bundle_digest: str
    effective_date: date

    producers: tuple[ProducerControl, ...] = ()
    injectors: tuple[InjectorControl, ...] = ()

    source_intent_digests: tuple[str, ...] = ()
    resolver_version: str = ""

    def __post_init__(self) -> None:
        CandidateKind(self.candidate_kind)
        _require_text(self.bundle_digest, "magnitude_candidate.bundle_digest")
        _require_text(self.resolver_version, "magnitude_candidate.resolver_version")

        producer_wells = {control.well for control in self.producers}
        injector_wells = {control.well for control in self.injectors}
        overlap = producer_wells & injector_wells
        if overlap:
            raise MagnitudeCandidateError(
                f"magnitude_candidate: well(s) {sorted(overlap)} appear as both "
                "producer and injector in the same candidate"
            )

    def canonical(self) -> MagnitudeCandidate:
        """Убрать дословные повторы, упорядочить controls по natural_key.

        Тот же подход, что и у ScheduleStep.canonical(): не изобретаем новую
        несовместимую сортировку для тех же типов.
        """
        return MagnitudeCandidate(
            candidate_kind=self.candidate_kind,
            bundle_digest=self.bundle_digest,
            effective_date=self.effective_date,
            producers=tuple(
                sorted(dict.fromkeys(self.producers), key=lambda c: natural_key(c.well))
            ),
            injectors=tuple(
                sorted(dict.fromkeys(self.injectors), key=lambda c: natural_key(c.well))
            ),
            source_intent_digests=tuple(sorted(set(self.source_intent_digests))),
            resolver_version=self.resolver_version,
        )

    def to_json(self) -> dict[str, JsonValue]:
        canonical = self.canonical()
        return {
            "candidate_kind": str(canonical.candidate_kind),
            "bundle_digest": canonical.bundle_digest,
            "effective_date": canonical.effective_date.isoformat(),
            "producers": [producer_control_to_json(control) for control in canonical.producers],
            "injectors": [injector_control_to_json(control) for control in canonical.injectors],
            "source_intent_digests": list(canonical.source_intent_digests),
            "resolver_version": canonical.resolver_version,
        }

    def digest(self) -> str:
        return digest(self.to_json())
