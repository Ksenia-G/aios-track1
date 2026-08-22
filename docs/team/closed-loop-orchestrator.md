# Closed-Loop Orchestrator -- Лиза Д.

## MISSION

Владелец end-to-end pipeline целиком -- не только logging/memory. Оркестратор
координирует вызовы specialist agents, Arbiter, MagnitudeResolver,
PredictiveSafetyScreen, SimulatorRuntime, Guardian и CHDD в правильном
порядке на каждом decision step. Он не реализует их внутреннюю логику --
работает против **frozen interfaces** (`aios.mas.protocols`) и test-only
stubs (`tests/support/mas_team_stubs.py`) до тех пор, пока настоящие
specialist agents не готовы.

## INPUT

Текущее состояние (`StateCard`) на начало decision step + конфигурация
запуска (variant/`llm_enabled`/simulation budget и т.п.).

## OUTPUT

Commit-кандидат на следующий decision step (после Guardian + CHDD) +
переход к следующему состоянию. Финальный claimed NPV -- **только** после
чистого полного прогона симулятора + CHDD; restart/chained tiers (если
появятся) -- ускорение поиска/оценки, не authority для итогового числа.

## OWNED FUTURE PATHS

`src/aios/mas/orchestration/` -- ещё не существует, создаётся на
`feature/closed-loop-orchestrator-001`.

## PIPELINE STAGES

```
текущее состояние
   |
   v
построить specialist envelopes (ProductionEnvelope, InjectionEnvelope)
   |
   v
Production Controller Round 1   Injection Controller Round 1   (независимо)
   |
   v
deterministic conflict predicate (не реализован этой задачей)
   |
   v
опционально ОДИН Round 2 (не более одного обмена)
   |
   v
Arbiter -> ArbiterDecision
   |
   v
MagnitudeResolver -> tuple[MagnitudeCandidate, ...]
   |
   v
PreActionValidator
   |
   v
PredictiveSafetyScreen -- только annotation, cardinality/identity сохранены
   |
   v
simulation candidate floor guard (require_simulation_floor)
   |
   v
SimulatorRuntime
   |
   v
Realized Constraint Guardian
   |
   v
CHDD
   |
   v
deterministic commit selection
   |
   v
DecisionTrace / Memory append
   |
   v
следующее состояние -- повторить
```

## FORBIDDEN AUTHORITY

Оркестратор координирует, но не подменяет authority других компонентов:

- не пишет внутреннюю логику Injection/Production Controller;
- не пишет Arbiter reasoning;
- не пишет numerical-правила MagnitudeResolver;
- не пишет семантику Guardian;
- не пишет формулы CHDD;
- не пишет физику симулятора.

## FIRST DELIVERABLE

**`ORCHESTRATOR-001`**

Цель: deterministic closed-loop pipeline skeleton на frozen interfaces и
`tests/support` stubs. Настоящие LLM specialists не нужны для первого task.

Минимальный путь:

```
State -> specialist R1 calls (stub) -> Arbiter (stub) -> MagnitudeResolver (stub)
       -> simulation floor guard -> fake simulator -> fake/stable Guardian interface
       -> fake CHDD -> deterministic selection -> next step
```

Не реализовывать весь business logic внутри orchestration layer --
он вызывает чужие интерфейсы, не подменяет их.

## DEFINITION OF DONE

- минимум 2 decision steps подряд;
- HOLD-кандидат доживает до конца каждого step (`require_simulation_floor`);
- deterministic baseline-кандидат доживает до конца каждого step;
- каждый resolved candidate реально прогоняется через fake runtime;
- commit-кандидатом может стать только simulated + Guardian-passed
  кандидат;
- детерминированный tie-break при равенстве;
- traceable ID у каждого кандидата на всех стадиях;
- `llm_enabled=True`/`False` дают одну и ту же топологию pipeline вне
  specialist-ветки (см. `require_ablation_equivalence`, T5).

`MemoryStore`/`DecisionTrace` могут быть отдельной следующей задачей, если
не нужны для минимального skeleton.

## TEST EXPECTATIONS

- unit-тест на порядок вызовов pipeline stages;
- тест, что HOLD/baseline не теряются на всём пути (используя
  `aios.mas.invariants.require_simulation_floor`);
- тест на identity-preservation через fake annotation stage
  (`require_candidate_preservation`);
- тест на fair ablation topology (`require_ablation_equivalence`);
- минимум 2-step прогон полностью на fakes/stubs, без реального симулятора,
  LLM или CHDD.

## HANDOFF TO NEXT COMPONENT

Как только Production/Injection Controller готовы (не stub), Orchestrator
переключает их на настоящую реализацию через тот же `Agent[TInput, TOutput]`
интерфейс -- без изменения своей внутренней структуры. Аналогично для
Arbiter/MagnitudeResolver/SimulatorRuntime после их готовности.
