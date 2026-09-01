# Runtime / Execution / Geology -- Марк

## MISSION

Владелец адаптера между resolved numerical controls (`MagnitudeCandidate`) и
физическим прогоном симулятора. Не UI/graphics. Формализует границу
исполнения без изобретения новых доменных типов там, где они уже есть.

## INPUT

`aios.mas.magnitude.MagnitudeCandidate` -- уже содержит
`producers: tuple[ProducerControl, ...]` и
`injectors: tuple[InjectorControl, ...]` (существующие authoritative типы
`aios.schedule`), плюс `effective_date`, `resolver_version`,
`source_intent_digests`.

## OUTPUT

`aios.evaluation.SimulationResult` -- существующий, только физический
результат + provenance. **Не добавлять** `npv`/`chdd`/`official_npv`/
`objective_value` поля в него -- это чужая authority (CHDD, central
integration).

## OWNED FUTURE PATHS

Конкретные actual areas должны быть определены discovery по текущему
repository на его собственной feature-ветке (`feature/runtime-execution-001`),
но зона ответственности заморожена:

- `SimulatorRuntime` integration (адаптер поверх существующего
  `aios.evaluation.Simulator`/`SimulatorRuntime` -- не новый protocol);
- `ScheduleCompiler` (MagnitudeCandidate -> Schedule/SimulationRequest);
- full-run/restart execution;
- restart reconciliation;
- runtime provenance;
- `GeologyCard` extraction из static deck;
- `ResponseMap` builder -- **отдельная, более поздняя задача** по отдельной
  спецификации, не начинать сейчас.

## READ-ONLY DEPENDENCIES

`aios.mas.magnitude.MagnitudeCandidate` (не меняется -- это frozen boundary
контракт), `aios.schedule.{ProducerControl,InjectorControl,natural_key}`,
`aios.evaluation.{SimulationRequest,SimulationResult,Simulator}`.

## PIPELINE STAGES

```
MagnitudeCandidate
   -> schedule/runtime conversion (ScheduleCompiler)
   -> aios.evaluation.SimulationRequest (существующий тип, не дублируется)
   -> existing Simulator / SimulatorRuntime
   -> aios.evaluation.SimulationResult (физический, без CHDD)
```

## FORBIDDEN AUTHORITY

- Не владеет Injection/Production Agent.
- Не является Arbiter.
- Не пишет numerical-правила MagnitudeResolver (получает уже resolved
  `MagnitudeCandidate` как готовый вход).
- Не пишет семантику Guardian.
- Не пишет формулы CHDD.
- Не занимается LLM.
- Не занимается UI.

## HOLD execution

HOLD означает: предыдущие canonical numerical controls переносятся вперёд
**точно**, без пересчёта magnitude. Использовать
`aios.mas.invariants.require_hold_carry_forward` /
`canonical_control_snapshot` для проверки этого на runtime-стороне -- не
писать вторую копию canonicalization-логики. Existing restart acceleration
остаётся условным ускорением поиска/оценки; итоговый claimed результат --
всё равно чистый полный прогон.

## Geology

`FIPFAULT` block membership -- prior/static partition evidence. **Не**
кодировать как cross-block hydraulic connectivity = 0 -- ни в
`GeologyCard`-экстракторе, ни где-либо ещё. См. `docs/team/organizer-constraints.md`,
раздел Geology.

## FIRST DELIVERABLE

**`RUNTIME-EXECUTION-001`**

Цель: формализовать production runtime adapter boundary
(`MagnitudeCandidate -> schedule compilation -> SimulationRequest ->
existing Simulator execution -> physical SimulationResult + provenance`).

## DEFINITION OF DONE

- никакого нового `Simulator` protocol (используется существующий
  `aios.evaluation.Simulator`);
- никакого нового Schedule control type (используются существующие
  `ProducerControl`/`InjectorControl`);
- HOLD -- точный carry-forward (`require_hold_carry_forward` проходит);
- schedule compiler выдаёт детерминированное complete schedule
  представление;
- restart/full mode -- явные, различимые режимы;
- restart -- только acceleration, не заменяет authoritative clean full run;
- ошибки симулятора типизированы и трассируемы;
- в `SimulationResult` нет CHDD;
- семантика Guardian не переписана.

`GeologyCard` extractor -- следующая под-задача либо параллельная
под-задача после runtime boundary, по результатам conflict-free discovery
на его собственной ветке.

## TEST EXPECTATIONS

- unit-тест на `ScheduleCompiler` (MagnitudeCandidate -> детерминированное
  schedule-представление, order-independent для одинаковых по значению
  наборов controls);
- тест на HOLD exact carry-forward через
  `aios.mas.invariants.require_hold_carry_forward`;
- тест на типизированную ошибку при сбое симулятора;
- тест, что `SimulationResult` не содержит CHDD/NPV-подобных полей.

## HANDOFF TO NEXT COMPONENT

`SimulationResult` уходит к Realized Constraint Guardian (существующий,
central integration/предыдущая инфраструктура) и затем к CHDD -- обе стадии
Марк не реализует и не переписывает их семантику.
