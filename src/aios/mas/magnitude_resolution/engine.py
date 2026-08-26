"""DeterministicMagnitudeResolver -- первая и единственная prescriptive
numerical boundary Hybrid MAS v1.1 (MAGNITUDE-RESOLVER-001).

До этого модуля вся система (specialists, Arbiter) работает только со
structural intents/proposals/bundles, ссылаясь друг на друга по digest.
`ArbiterDecision`/`DecisionBundle` НЕ несут ни сами Intent-объекты (только
их digest), ни previous numerical controls, ни deterministic baseline
numeric controls -- эта информация приходит через explicit `MagnitudeContext`,
а не через изменение frozen `aios.mas.arbiter`/`aios.mas.envelopes` схемы.

NO SETPOINTS CROSS UNTIL MAGNITUDE RESOLVER: до этой точки ни один
`ProducerControl`/`InjectorControl` не существует. Здесь эта граница
заканчивается для structural `AGENT_BUNDLE` decision bundles; `HOLD` и
`DETERMINISTIC_BASELINE` вообще не проходят через empirical policy -- они
разрешаются в точные, заранее известные numeric snapshots.

Этот модуль НЕ вызывает симулятор, НЕ читает Guardian/SimulationResult, НЕ
считает CHDD/NPV, НЕ придумывает official compensation/water-source formula
и НЕ использует произвольные проценты -- см.
`docs/architecture/mas-v1.1-magnitude-resolver.md`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date

from aios.cases.contract import TRACK1_WLPR_HARD_MAX
from aios.evaluation.canonical import digest
from aios.mas.arbiter import ArbiterDecision, DecisionBundle
from aios.mas.common import CandidateKind, Direction
from aios.mas.envelopes import ArbiterEnvelope
from aios.mas.intents import InjectionAction, InjectionIntent, ProductionAction, ProductionIntent
from aios.mas.invariants import canonical_control_digest
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.magnitude_resolution.profile import ModelYEmpiricalMagnitudeProfile
from aios.schedule import InjectorControl, ProducerControl, WellStatus, natural_key

RESOLVER_VERSION = "aios.mas.magnitude_resolution/1.0.0"


class MagnitudeResolutionError(ValueError):
    """Structural bundle нельзя детерминированно разрешить в numerical controls.

    НЕ означает "молча стать HOLD" или "молча стать baseline" -- вызывающий
    код обязан либо пропустить этот конкретный AGENT_BUNDLE (floor выживает
    отдельно), либо прервать резолюцию, если ошибка возникла на floor.
    """


@dataclass(frozen=True, slots=True)
class MagnitudeContext:
    """Official context, которого нет во frozen `ArbiterDecision`.

    `ArbiterDecision`/`DecisionBundle` ссылаются на proposals/intents только
    по digest -- сами объекты, previous numerical controls и deterministic
    baseline numeric controls сюда не входят. Это не пробел контракта,
    который нужно чинить: Arbiter не обязан знать numeric state, а
    MagnitudeResolver -- единственный, кому это нужно, поэтому именно он
    получает этот контекст явно, тем же способом, каким `DeterministicArbiter`
    получает `conflict_policy` через конструктор, а не через `decide()`.
    """

    effective_date: date
    arbiter_envelope: ArbiterEnvelope
    previous_producers: tuple[ProducerControl, ...]
    previous_injectors: tuple[InjectorControl, ...]
    baseline_producers: tuple[ProducerControl, ...]
    baseline_injectors: tuple[InjectorControl, ...]


def _bundle_digest(bundle: DecisionBundle) -> str:
    """DecisionBundle не выставляет собственный `.digest()` -- дайджест
    считается тем же canonical-инфраструктурой, что и везде в проекте."""
    return digest(bundle.to_json())


def _build_intent_lookup(
    envelope: ArbiterEnvelope,
) -> dict[str, ProductionIntent | InjectionIntent]:
    lookup: dict[str, ProductionIntent | InjectionIntent] = {}
    for proposal in (
        envelope.production_output.primary_proposal,
        envelope.production_output.baseline_proposal,
    ):
        for intent in proposal.intents:
            lookup[intent.digest()] = intent
    for proposal in (
        envelope.injection_output.primary_proposal,
        envelope.injection_output.baseline_proposal,
    ):
        for intent in proposal.intents:
            lookup[intent.digest()] = intent
    return lookup


def _canonical_tuple(
    controls_by_well: Mapping[str, ProducerControl | InjectorControl],
) -> tuple:
    return tuple(controls_by_well[well] for well in sorted(controls_by_well, key=natural_key))


class DeterministicMagnitudeResolver:
    """Deterministic core MagnitudeResolver. LLM/cognitive resolver -- future work.

    Реализует frozen `aios.mas.protocols.MagnitudeResolver`: `resolve()`
    принимает только `ArbiterDecision`, весь остальной context передаётся в
    конструктор -- сигнатура метода не меняется.
    """

    def __init__(
        self,
        *,
        context: MagnitudeContext,
        profile: ModelYEmpiricalMagnitudeProfile | None = None,
    ) -> None:
        self._context = context
        self._profile = profile or ModelYEmpiricalMagnitudeProfile()
        self._resolver_version = f"{RESOLVER_VERSION}+{self._profile.version}"
        self._intent_lookup = _build_intent_lookup(context.arbiter_envelope)

    def resolve(self, decision: ArbiterDecision) -> tuple[MagnitudeCandidate, ...]:
        resolved: list[MagnitudeCandidate] = []
        for bundle in decision.bundles:
            try:
                candidate = self._resolve_bundle(bundle)
            except MagnitudeResolutionError:
                if bundle.kind in (CandidateKind.HOLD, CandidateKind.DETERMINISTIC_BASELINE):
                    raise
                continue
            resolved.append(candidate)
        return self._dedup_preserving_floor(resolved)

    # -- bundle-level resolution ------------------------------------------

    def _resolve_bundle(self, bundle: DecisionBundle) -> MagnitudeCandidate:
        if bundle.kind is CandidateKind.HOLD:
            return self._build_candidate(
                bundle,
                self._context.previous_producers,
                self._context.previous_injectors,
                source_intent_digests=(),
            )
        if bundle.kind is CandidateKind.DETERMINISTIC_BASELINE:
            return self._build_candidate(
                bundle,
                self._context.baseline_producers,
                self._context.baseline_injectors,
                source_intent_digests=self._bundle_intent_digests(bundle),
            )
        return self._resolve_agent_bundle(bundle)

    def _bundle_intent_digests(self, bundle: DecisionBundle) -> tuple[str, ...]:
        return tuple(
            sorted(set(bundle.production_intent_digests) | set(bundle.injection_intent_digests))
        )

    def _resolve_agent_bundle(self, bundle: DecisionBundle) -> MagnitudeCandidate:
        producers_by_well: dict[str, ProducerControl] = {
            c.well: c for c in self._context.previous_producers
        }
        injectors_by_well: dict[str, InjectorControl] = {
            c.well: c for c in self._context.previous_injectors
        }
        touched_producers: set[str] = set()
        touched_injectors: set[str] = set()

        for intent_digest in bundle.production_intent_digests:
            intent = self._intent_lookup.get(intent_digest)
            if intent is None or not isinstance(intent, ProductionIntent):
                raise MagnitudeResolutionError(
                    f"production intent digest {intent_digest!r} not found in context"
                )
            self._apply_production_intent(intent, producers_by_well, touched_producers)

        for intent_digest in bundle.injection_intent_digests:
            intent = self._intent_lookup.get(intent_digest)
            if intent is None or not isinstance(intent, InjectionIntent):
                raise MagnitudeResolutionError(
                    f"injection intent digest {intent_digest!r} not found in context"
                )
            self._apply_injection_intent(intent, injectors_by_well, touched_injectors)

        return self._build_candidate(
            bundle,
            _canonical_tuple(producers_by_well),
            _canonical_tuple(injectors_by_well),
            source_intent_digests=self._bundle_intent_digests(bundle),
        )

    # -- production action handlers ----------------------------------------

    def _apply_production_intent(
        self,
        intent: ProductionIntent,
        controls_by_well: dict[str, ProducerControl],
        touched: set[str],
    ) -> None:
        action = ProductionAction(intent.action)
        if action is ProductionAction.HOLD:
            return
        if action is ProductionAction.INCREASE_OFFTAKE:
            anchor = self._profile.producer_uplift_factor(intent.intensity)
            if anchor is None:
                raise MagnitudeResolutionError(
                    f"INCREASE_OFFTAKE: no empirical anchor for intensity {intent.intensity}"
                )
            for well in intent.target_wells:
                previous = self._require_untouched_control(
                    well, controls_by_well, touched, "production"
                )
                previous_rate = previous.liquid_rate or 0.0
                new_rate = min(previous_rate * anchor.value, TRACK1_WLPR_HARD_MAX)
                controls_by_well[well] = replace(previous, liquid_rate=new_rate)
            return
        if action is ProductionAction.SHUT_IN:
            for well in intent.target_wells:
                previous = self._require_untouched_control(
                    well, controls_by_well, touched, "production"
                )
                controls_by_well[well] = replace(
                    previous, status=WellStatus.SHUT, liquid_rate=None, bhp_limit=None
                )
            return
        if action is ProductionAction.REOPEN:
            baseline_by_well = {c.well: c for c in self._context.baseline_producers}
            for well in intent.target_wells:
                self._require_untouched(well, touched, "production")
                source = baseline_by_well.get(well)
                if source is None or source.status is not WellStatus.OPEN:
                    raise MagnitudeResolutionError(
                        f"REOPEN: no canonical OPEN baseline control for well {well!r}"
                    )
                controls_by_well[well] = source
                touched.add(well)
            return
        raise MagnitudeResolutionError(
            f"unsupported ProductionAction for MAGNITUDE-RESOLVER-001: {action}"
        )

    # -- injection action handlers ------------------------------------------

    def _apply_injection_intent(
        self,
        intent: InjectionIntent,
        controls_by_well: dict[str, InjectorControl],
        touched: set[str],
    ) -> None:
        action = InjectionAction(intent.action)
        if action is InjectionAction.HOLD:
            return
        if action is InjectionAction.SCALE_TOTAL_INJECTION:
            direction_up = intent.direction is Direction.UP
            anchor = self._profile.injection_scale_factor(
                intent.intensity, direction_up=direction_up
            )
            if anchor is None:
                raise MagnitudeResolutionError(
                    f"SCALE_TOTAL_INJECTION: no empirical anchor for intensity {intent.intensity}"
                )
            for well in list(controls_by_well.keys()):
                previous = self._require_untouched_control(
                    well, controls_by_well, touched, "injection"
                )
                new_rate = max((previous.surface_rate or 0.0) * anchor.value, 0.0)
                controls_by_well[well] = replace(previous, surface_rate=new_rate)
            return
        if action is InjectionAction.SUPPORT_REGION:
            if not intent.recipient_wells:
                raise MagnitudeResolutionError(
                    "SUPPORT_REGION: MAGNITUDE-RESOLVER-001 requires explicit recipient_wells "
                    "(region-only targeting needs GeologyContext, not available to the resolver)"
                )
            anchor = self._profile.injection_scale_factor(intent.intensity, direction_up=True)
            if anchor is None:
                raise MagnitudeResolutionError(
                    f"SUPPORT_REGION: no empirical anchor for intensity {intent.intensity}"
                )
            for well in intent.recipient_wells:
                previous = self._require_untouched_control(
                    well, controls_by_well, touched, "injection"
                )
                new_rate = max((previous.surface_rate or 0.0) * anchor.value, 0.0)
                controls_by_well[well] = replace(previous, surface_rate=new_rate)
            return
        if action is InjectionAction.REDUCE_SUPPORT_PATH:
            if not intent.donor_wells:
                raise MagnitudeResolutionError(
                    "REDUCE_SUPPORT_PATH: MAGNITUDE-RESOLVER-001 requires explicit donor_wells "
                    "(region-only targeting needs GeologyContext, not available to the resolver)"
                )
            anchor = self._profile.injection_scale_factor(intent.intensity, direction_up=False)
            if anchor is None:
                raise MagnitudeResolutionError(
                    f"REDUCE_SUPPORT_PATH: no empirical anchor for intensity {intent.intensity}"
                )
            for well in intent.donor_wells:
                previous = self._require_untouched_control(
                    well, controls_by_well, touched, "injection"
                )
                new_rate = max((previous.surface_rate or 0.0) * anchor.value, 0.0)
                controls_by_well[well] = replace(previous, surface_rate=new_rate)
            return
        if action is InjectionAction.REALLOCATE_INJECTION:
            if len(intent.donor_wells) != 1 or len(intent.recipient_wells) != 1:
                raise MagnitudeResolutionError(
                    "REALLOCATE_INJECTION: MAGNITUDE-RESOLVER-001 only supports exactly one "
                    "donor and one recipient well (matches the only tested transfer pattern)"
                )
            anchor = self._profile.reallocation_transfer_fraction(intent.intensity)
            if anchor is None:
                raise MagnitudeResolutionError(
                    f"REALLOCATE_INJECTION: no empirical anchor for intensity {intent.intensity}"
                )
            donor_well = intent.donor_wells[0]
            recipient_well = intent.recipient_wells[0]
            donor_previous = self._require_untouched_control(
                donor_well, controls_by_well, touched, "injection"
            )
            recipient_previous = self._require_untouched_control(
                recipient_well, controls_by_well, touched, "injection"
            )
            donor_rate = donor_previous.surface_rate or 0.0
            delta = anchor.value * donor_rate
            controls_by_well[donor_well] = replace(
                donor_previous, surface_rate=max(donor_rate - delta, 0.0)
            )
            controls_by_well[recipient_well] = replace(
                recipient_previous,
                surface_rate=max((recipient_previous.surface_rate or 0.0) + delta, 0.0),
            )
            return
        raise MagnitudeResolutionError(
            f"unsupported InjectionAction for MAGNITUDE-RESOLVER-001: {action}"
        )

    # -- shared helpers ------------------------------------------------------

    def _require_untouched(self, well: str, touched: set[str], label: str) -> None:
        if well in touched:
            raise MagnitudeResolutionError(
                f"{label}: conflicting intents both target well {well!r} in the same bundle"
            )
        touched.add(well)

    def _require_untouched_control(
        self,
        well: str,
        controls_by_well: Mapping[str, ProducerControl | InjectorControl],
        touched: set[str],
        label: str,
    ):
        self._require_untouched(well, touched, label)
        control = controls_by_well.get(well)
        if control is None:
            raise MagnitudeResolutionError(f"{label}: unknown target well {well!r}")
        return control

    def _build_candidate(
        self,
        bundle: DecisionBundle,
        producers: tuple[ProducerControl, ...],
        injectors: tuple[InjectorControl, ...],
        *,
        source_intent_digests: tuple[str, ...],
    ) -> MagnitudeCandidate:
        return MagnitudeCandidate(
            candidate_kind=bundle.kind,
            bundle_digest=_bundle_digest(bundle),
            effective_date=self._context.effective_date,
            producers=producers,
            injectors=injectors,
            source_intent_digests=source_intent_digests,
            resolver_version=self._resolver_version,
        )

    def _dedup_preserving_floor(
        self, candidates: list[MagnitudeCandidate]
    ) -> tuple[MagnitudeCandidate, ...]:
        floor = [
            c
            for c in candidates
            if c.candidate_kind in (CandidateKind.HOLD, CandidateKind.DETERMINISTIC_BASELINE)
        ]
        others = [c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE]

        output: list[MagnitudeCandidate] = []
        seen_digests: set[str] = set()
        for candidate in floor:
            output.append(candidate)
            seen_digests.add(canonical_control_digest(candidate.producers, candidate.injectors))
        for candidate in others:
            key = canonical_control_digest(candidate.producers, candidate.injectors)
            if key in seen_digests:
                continue
            seen_digests.add(key)
            output.append(candidate)
        return tuple(output)
