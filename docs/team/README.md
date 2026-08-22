# Hybrid MAS v1.1 -- точка входа для команды

## 1. Что строим

Замкнутый контур управления заводнением, где LLM-специалисты предлагают
структурные гипотезы (Intent), а детерминированное ядро остаётся
единственным владельцем численных setpoint'ов, физики, легальности и
итогового выбора. Формула:

> LLM sets the ceiling. Determinism sets the floor.

## 2. Архитектурная цепочка

```text
StateCard
   |
   +---------------------+
   |                     |
ProductionEnvelope   InjectionEnvelope
   |                     |
Production Controller  Injection Controller
   |                     |
ProductionProposal    InjectionProposal
   |                     |
   +----------+----------+
              |
       ArbiterEnvelope
              |
           Arbiter
              |
       ArbiterDecision
              |
       MagnitudeResolver
              |
       MagnitudeCandidate
              |
 existing SimulationRequest -> existing Simulator -> existing SimulationResult
              |
       +------+------+
       |             |
 Realized Guardian   CHDD
       |             |
       +------+------+
              |
 deterministic selection / commit
```

Главная граница -- **NO SETPOINTS CROSS**: до `MagnitudeCandidate` ни один
тип в этой цепочке не может нести `ProducerControl`/`InjectorControl` или
любое prescriptive numerical поле. Подробно: `docs/architecture/mas-v1.1-contracts.md`.

## 3. Что уже готово

- **Frozen MAS contracts** (`src/aios/mas/`): `common`, `evidence`,
  `intents`, `proposals`, `envelopes`, `arbiter`, `magnitude`, `protocols`.
- **Системные инварианты T1--T7** (`src/aios/mas/invariants.py`) --
  deterministic guard layer: anti-anchoring, no-setpoints-cross authority
  audit, simulation floor, candidate preservation, fair LLM ablation, HOLD
  carry-forward, canonical control identity.
- **Канонические diagnostic services** (`src/aios/diagnostics/`):
  `PressureDiagnostic`/`SweepDiagnostic` -- behavior-preserving canonical
  имена поверх legacy `PressureAgent`/`SweepAgent`.
- **StateCard** (`src/aios/state/`) -- стабильный typed вход для будущих
  агентов.
- **Constraint Guardian** (`src/aios/agents/constraint_guardian.py`) --
  существующая deterministic authority по official realized ограничениям.
- **EXP001--004** -- завершённая экспериментальная линия (historical
  lineage), физика/экономика не переписываются.
- **Test-only foundation** (`tests/support/`) -- deterministic factories и
  stubs, чтобы начать писать тесты до готовности настоящих компонентов.

## 4. Что ЕЩЁ НЕ реализовано

Явно, чтобы не было иллюзий при первом чтении кода:

- Production/Injection Controller (specialist agents) -- **не реализованы**.
- Arbiter -- **не реализован** (central integration owner).
- MagnitudeResolver -- **не реализован** (central integration owner,
  numerical intensity mapping тоже).
- PredictiveSafetyScreen -- **не реализован** (резерв за Вшивцевой).
- Closed-loop Orchestrator -- **не реализован** (резерв за Доценко).
- Runtime execution adapter (ScheduleCompiler/SimulatorRuntime integration
  поверх MagnitudeCandidate) -- **не реализован** (резерв за Марком).
- LLM provider/PromptBuilder/GroundingAuditor/ProposalCompiler -- **не
  реализованы** ни для одной роли.
- CompensationPolicy port, economic pre-screen -- central integration,
  **не реализованы**.

## 5. Ссылки на role-документы

| Роль | Документ |
|---|---|
| Ксения -- Injection Controller | [injection-controller.md](injection-controller.md) |
| Елизавета Вшивцева -- Production Controller (+ будущий PredictiveSafetyScreen) | [production-controller.md](production-controller.md) |
| Елизавета Доценко -- Closed-Loop Orchestrator | [closed-loop-orchestrator.md](closed-loop-orchestrator.md) |
| Марк -- Runtime / Execution / Geology | [runtime-execution.md](runtime-execution.md) |

Общие документы:

- [authority-matrix.md](authority-matrix.md) -- кто что может делать.
- [organizer-constraints.md](organizer-constraints.md) -- CONFIRMED vs
  UNRESOLVED организаторские правила.
- [branching-and-handoff.md](branching-and-handoff.md) -- откуда стартовать,
  как называть ветки, что такое frozen shared contracts.
- [`docs/architecture/mas-v1.1-contracts.md`](../architecture/mas-v1.1-contracts.md) --
  полная спецификация contracts + инвариантов T1--T7.
- [`docs/architecture/mas-v1.1-diagnostics.md`](../architecture/mas-v1.1-diagnostics.md) --
  Pressure/Sweep diagnostic services.

## 6. Общий workflow

1. Дождаться `TEAM_FOUNDATION_SHA` от captain (после независимого QG на
   `integration/team-foundation-v1.1`) -- см. `branching-and-handoff.md`.
2. Создать свою feature-ветку от этого SHA.
3. Прочитать свой role-документ полностью -- INPUT/OUTPUT/OWNED FUTURE
   PATHS/FORBIDDEN AUTHORITY уже зафиксированы, самостоятельно
   проектировать API не нужно.
4. Использовать `tests/support/mas_team_fixtures.py` и
   `tests/support/mas_team_stubs.py`, чтобы начать писать тесты до
   готовности зависимостей.
5. Не трогать frozen shared contracts (`src/aios/mas/{__init__,common,
   intents,envelopes,proposals,arbiter,magnitude,invariants,protocols}.py`)
   без approval integration owner.
6. PR -> review -> integration. Прямых push в `main` нет.
