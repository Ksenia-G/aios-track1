"""Вариант MAS: настоящий замкнутый контур на той же физике и экономике.

MAS OFF и MAS ON отличаются ровно одним -- состоянием когнитивной ветки
специалистов. Горизонт, кейс, стартовое состояние, бюджет симуляций, профиль
магнитуд, Guardian, ЧДД и топология решения общие: иначе сравнение измеряло бы
подмену условий, а не работу LLM.

Здесь нет ни одного рукописного кандидата: управления рождаются там, где им
положено -- на границе MagnitudeResolver, после структурного решения Arbiter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from aios.benchmark.adapters import BenchmarkContext
from aios.benchmark.contracts import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkError,
    BenchmarkOutcome,
    BenchmarkRunManifest,
    CognitiveConfig,
    EvaluationLineage,
    RunKind,
    VariantFamily,
)
from aios.evaluation.canonical import digest
from aios.mas.arbitration import DeterministicArbiter
from aios.mas.common import ProposalSource
from aios.mas.envelopes import ArbiterEnvelope
from aios.mas.evidence import EvidenceArtifact, HorizonContext, MemorySlice
from aios.mas.magnitude_resolution import (
    DeterministicMagnitudeResolver,
    MagnitudeContext,
    supported_injection_actions,
    supported_production_actions,
)
from aios.mas.orchestration import (
    ClosedLoopOrchestrator,
    DecisionStepInput,
    DecisionTrace,
    OrchestratorConfig,
    build_real_closed_loop,
)
from aios.mas.specialists.injection import InjectionController
from aios.mas.specialists.production import ProductionController

#: Действия, доступные специалистам в бенчмарке. Одинаковы для OFF и ON.
#:
#: Список не задан вручную: он выводится из того, что детерминированный
#: резолвер при активном профиле действительно умеет превратить в числа.
#: Предлагать модели действие без якоря -- значит гарантированно потерять её
#: решение между Arbiter и численным разрешением, что и произошло со всеми
#: пятью живыми Production-предложениями в MAS-PERF-001.
PRODUCTION_ACTIONS = supported_production_actions()
INJECTION_ACTIONS = supported_injection_actions()


@dataclass
class _EnvelopeCapturingArbiter:
    """Арбитр, запоминающий свой последний envelope.

    Резолверу магнитуд нужен именно тот ArbiterEnvelope, по которому принято
    решение, а протокол `resolve(decision)` его не передаёт. Перехват на этом
    шве -- интеграционная деталь, а не изменение логики арбитража: решение
    принимает настоящий DeterministicArbiter.
    """

    inner: DeterministicArbiter = field(default_factory=DeterministicArbiter)
    last_envelope: ArbiterEnvelope | None = None

    def decide(self, envelope: ArbiterEnvelope):
        self.last_envelope = envelope
        return self.inner.decide(envelope)


@dataclass
class _ContextualMagnitudeResolver:
    """Резолвер, собирающий контекст магнитуд для текущего шага."""

    arbiter: _EnvelopeCapturingArbiter
    effective_date: object
    baseline_producers: tuple
    baseline_injectors: tuple
    previous_producers: tuple = ()
    previous_injectors: tuple = ()

    def resolve(self, decision):
        envelope = self.arbiter.last_envelope
        if envelope is None:
            raise BenchmarkError("magnitude resolver ran before the arbiter produced an envelope")
        context = MagnitudeContext(
            effective_date=self.effective_date,
            arbiter_envelope=envelope,
            previous_producers=self.previous_producers or self.baseline_producers,
            previous_injectors=self.previous_injectors or self.baseline_injectors,
            baseline_producers=self.baseline_producers,
            baseline_injectors=self.baseline_injectors,
        )
        return DeterministicMagnitudeResolver(context=context).resolve(decision)


def _baseline_controls(state):
    """Опорные управления так, как их выводит компилятор расписания.

    Именно этот срез состояния компилятор сравнивает с HOLD-кандидатом при
    проверке T6, поэтому резолвер обязан отталкиваться от него же. Свой,
    похожий, вывод из расписания дал бы формально другой набор -- и точный
    перенос управлений перестал бы быть точным.
    """
    return (
        tuple(producer.as_producer() for producer in state.producers),
        tuple(injector.as_injector() for injector in state.injectors),
    )


def _diagnostics(state_card) -> dict[str, EvidenceArtifact]:
    """Настоящая детерминированная диагностика шага."""
    from aios.diagnostics import PressureDiagnostic, SweepDiagnostic

    return {
        "pressure_diagnostic": EvidenceArtifact(
            evidence_id="benchmark.pressure",
            kind="pressure_diagnostic",
            payload=PressureDiagnostic().propose(state_card).to_json(),
        ),
        "sweep_diagnostic": EvidenceArtifact(
            evidence_id="benchmark.sweep",
            kind="sweep_diagnostic",
            payload=SweepDiagnostic().propose(state_card).to_json(),
        ),
    }


def run_mas_variant(
    context: BenchmarkContext,
    *,
    variant_id: str,
    llm_enabled: bool,
    initial_state_card,
    provider_id: str | None = None,
    model_id: str | None = None,
    run_id: str | None = None,
    production_provider: object | None = None,
    injection_provider: object | None = None,
    simulation_budget: int = 8,
    decision_steps: int = 1,
    notes: tuple[str, ...] = (),
) -> BenchmarkOutcome:
    """Прогнать настоящий замкнутый контур и собрать запись бенчмарка."""
    started = time.monotonic()
    state, baseline, _history, _start = context.baseline_inputs()
    effective_date = context.case.management_start_date

    composition = build_real_closed_loop(
        case=context.case,
        history_text=_history,
        history_start_date=_start,
        initial_state=state,
        baseline=baseline,
        workspace_root=Path(context.workspace_root) / variant_id,
    )

    arbiter = _EnvelopeCapturingArbiter()
    baseline_producers, baseline_injectors = _baseline_controls(state)
    resolver = _ContextualMagnitudeResolver(
        arbiter=arbiter,
        effective_date=effective_date,
        baseline_producers=baseline_producers,
        baseline_injectors=baseline_injectors,
    )

    config = OrchestratorConfig(
        variant_id=variant_id,
        llm_enabled=llm_enabled,
        simulation_budget=simulation_budget,
        resolver_version="aios.mas.magnitude_resolution/1.0.0",
        guardian_config_digest=digest({"guardian": "aios.agents.constraint_guardian/1.0.0"}),
        chdd_config_digest=digest({"chdd": context.case.economics.provenance}),
    )
    trace = DecisionTrace()
    orchestrator = ClosedLoopOrchestrator(
        config=config,
        # Флаг доходит до специалистов через фабрику: топология одна и та же,
        # различается только когнитивная ветка внутри специалиста.
        production_agent_factory=lambda enabled: ProductionController(
            llm_enabled=enabled, provider=production_provider
        ),
        # InjectionController's llm_source is a static constructor knob (the
        # LlmProvider protocol returns bare text, so it cannot carry its own
        # provenance); the live adapter declares its provenance via `.source`
        # (see InjectionLiveProvider), so read it here rather than defaulting
        # a real live completion to the LLM_LOCAL fallback value.
        injection_agent_factory=lambda enabled: InjectionController(
            llm_enabled=enabled,
            llm_provider=injection_provider,
            llm_source=getattr(injection_provider, "source", ProposalSource.LLM_LOCAL),
        ),
        arbiter=arbiter,
        magnitude_resolver=resolver,
        decision_trace=trace,
        **composition.boundaries(),
    )

    diagnostics = _diagnostics(initial_state_card)
    horizon = HorizonContext(
        decision_date=effective_date,
        horizon_end=context.case.management_end_date,
        remaining_steps=decision_steps,
    )
    step_input = DecisionStepInput(
        state=initial_state_card,
        horizon_context=horizon,
        memory_slice=MemorySlice(),
        production_allowed_actions=PRODUCTION_ACTIONS,
        injection_allowed_actions=INJECTION_ACTIONS,
        **diagnostics,
    )

    results = orchestrator.run_steps(step_input, decision_steps)
    final = results[-1]
    selected = final.selection.selected

    simulation_count = sum(len(result.candidates) for result in results)
    lineage = tuple(
        EvaluationLineage(
            schedule_digest=result.committed_candidate.digest(),
            realized_state_digest=result.next_state.digest(),
            chdd_input_digest=(selected.chdd.input_digest if selected.chdd is not None else None),
        )
        for result in results
    )
    committed = final.committed_candidate.canonical()
    guardian_failed = sum(
        1
        for result in results
        for record in result.candidates
        if record.guardian
        and not record.guardian.passed
        and record.candidate_digest == result.committed_candidate_digest
    )

    manifest = BenchmarkRunManifest(
        benchmark_schema_version=BENCHMARK_SCHEMA_VERSION,
        variant_id=variant_id,
        variant_family=VariantFamily.MAS_ON if llm_enabled else VariantFamily.MAS_OFF,
        run_kind=RunKind.CLOSED_LOOP_POLICY,
        code_sha=context.code_sha,
        frame=context.frame(
            initial_state_digest=initial_state_card.digest(),
            chdd_month_count=None,
        ),
        cognitive=CognitiveConfig(
            llm_enabled=llm_enabled,
            provider_id=provider_id,
            model_id=model_id,
            run_id=run_id,
        ),
        lineage=lineage,
        source_reference="aios.mas.orchestration.build_real_closed_loop",
        notes=notes,
    )
    if selected.chdd is None:
        raise BenchmarkError("mas variant: committed candidate carries no CHDD outcome")

    return BenchmarkOutcome(
        manifest=manifest,
        npv_mln_rub=selected.chdd.result.npv_mln_rub,
        guardian_pass=guardian_failed == 0,
        guardian_check_count=sum(
            record.guardian.report["summary"]["total_checks"]
            if isinstance(record.guardian, dict)
            else 0
            for result in results
            for record in result.candidates
            if record.guardian is not None
        ),
        guardian_failed_count=guardian_failed,
        simulation_count=simulation_count,
        control_change_count=len(committed.producers) + len(committed.injectors),
        decision_steps=len(results),
        round2_count=sum(1 for result in results if result.round2_triggered),
        fallback_count=0,
        chdd_result_digest=digest(selected.chdd.result.to_json()),
        wall_duration_seconds=round(time.monotonic() - started, 3),
        extra={
            "committed_candidate_digest": final.committed_candidate_digest,
            "selection_rule": final.selection.selection_rule_version,
            "rejected": [str(item.reason) for item in final.selection.rejected],
        },
    )


__all__ = ["INJECTION_ACTIONS", "PRODUCTION_ACTIONS", "run_mas_variant"]
