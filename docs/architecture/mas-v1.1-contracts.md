# Hybrid MAS v1.1 -- frozen contract layer

## Назначение

Этот документ описывает `aios.mas` -- типизированный контрактный слой между
LLM-специалистами и детерминированным ядром проекта. Это фундамент, а не
реализация: здесь нет ни одного работающего агента, промпта, LLM-вызова или
orchestrator. Их подключение -- отдельные задачи, идущие поверх этих
контрактов.

Главный принцип сформулирован одной формулой:

> LLM sets the ceiling. Determinism sets the floor.

LLM предлагает гипотезы -- структурные намерения о том, что стоило бы
сделать. Детерминированное ядро остаётся единственным владельцем численных
setpoint'ов, физики, легальности управления и итогового выбора кандидата для
прогона.

## 1. Цепочка: specialists -> Arbiter -> MagnitudeResolver -> simulator

```text
StateCard
   |
   +---------------------+
   |                     |
ProductionEnvelope   InjectionEnvelope
   |                     |
Production Agent     Injection Agent      <- не реализованы в этой задаче
   |                     |
ProductionProposal  InjectionProposal
   |                     |
   +----------+----------+
              |
       ArbiterEnvelope
              |
           Arbiter                        <- не реализован в этой задаче
              |
       ArbiterDecision
              |
       MagnitudeResolver                  <- не реализован в этой задаче
              |
       MagnitudeCandidate
              |
 aios.evaluation.SimulationRequest        <- уже существует, не дублируется
              |
 aios.evaluation.Simulator / runtime      <- уже существует, не дублируется
              |
 aios.evaluation.SimulationResult         <- уже существует, только физика
              |
       +------+------+
       |             |
 Realized Guardian   CHDD                  <- существующие authorities, не тронуты
       |             |
       +------+------+
              |
 детерминированный выбор / commit
```

Production и Injection специалисты работают параллельно на Round 1 и не
видят предложений друг друга. Arbiter -- второй эшелон: он получает уже
готовые `ProductionAgentOutput`/`InjectionAgentOutput`, а не запускается
одновременно со специалистами.

## 2. NO SETPOINTS CROSS

Это главная граница пакета. До `aios.mas.magnitude` во всей цепочке
запрещены прескриптивные численные controls: `LRAT=...`, `RATE=...`,
`BHP=...`, `surface_rate=...`. Агент видит численные данные только как
evidence (WBHP, WLPR, WOPR, WWCT, pressure, margins) через
`EvidenceArtifact`, но не может выразить их как команду.

Специалисты выражают намерение структурно: `ProductionIntent`/
`InjectionIntent` содержат действие (`ProductionAction`/`InjectionAction`),
направление (`Direction`), качественную интенсивность (`Intensity`:
`SMALL`/`MODERATE`/`AGGRESSIVE`) -- без единого числового значения ни у
одного enum. Перевод "AGGRESSIVE" в конкретный процент или ставку -- задача
`MagnitudeResolver`, которого этот пакет не реализует.

`MagnitudeCandidate` (`aios.mas.magnitude`) -- первая и единственная точка
контрактного слоя, где появляются реальные численные controls, и она
использует уже существующие `aios.schedule.ProducerControl`/
`InjectorControl`, а не новый параллельный тип. Ни один другой модуль
`aios.mas` не импортирует эти типы -- это проверяется структурным тестом
(`tests/test_mas_magnitude.py`), а не текстовым grep по содержимому.

Producer -> injector конверсия отсутствует как класс действий и намеренно не
добавлена: единственный noop -- `HOLD`.

## 3. Baseline -- отдельный объект, вне envelope специалиста

`ProductionEnvelope` и `InjectionEnvelope` структурно не содержат поля
baseline. `PromptBuilder` следующей задачи получает именно Envelope, поэтому
baseline физически не может протечь в prompt специалиста через официальный
контракт -- это защита от anchoring на детерминированную линию поведения при
выработке primary-предложения.

Baseline и primary предложения живут раздельно, в `ProductionAgentOutput`/
`InjectionAgentOutput`: `baseline_proposal.source` обязан быть
`DETERMINISTIC_BASELINE`, а `primary_proposal` может быть LLM-источником
(`LLM_LIVE`/`LLM_LOCAL`/`LLM_REPLAY`) или тоже детерминированным
(`DETERMINISTIC_FALLBACK`) -- это нужно для `llm_enabled=False` ablation.
Primary и baseline всегда ссылаются на один и тот же `envelope_digest`.

## 4. Arbiter -- второй эшелон

`ArbiterEnvelope` собирает `ProductionAgentOutput`+`InjectionAgentOutput`,
`HorizonContext`, `MemorySlice`, `SimulationBudget` и `state_card_digest`
(не полный `StateCard` повторно). Никаких numerical candidate setpoints и
никаких `ProducerControl`/`InjectorControl` в этом объекте нет: Arbiter
работает со structural bundles, ссылаясь на proposals/intents по digest.

`ArbiterDecision` гарантирует единственное инвариантное свойство: набор
кандидатов на каждом decision step обязан содержать хотя бы один
`CandidateKind.HOLD` и хотя бы один `CandidateKind.DETERMINISTIC_BASELINE`.
Это не теорема о том, что `NPV(MAS) >= NPV(BASELINE)` -- такой теоремы не
существует и не заявляется. Это гарантия, что "ничего не делать" и
"продолжать как раньше" не могут исчезнуть из candidate space.

Опциональный `Round2Request` может запросить второй раунд только у
`PRODUCTION` и/или `INJECTION`; `ARBITER` запросить сам себя не может.
Conflict predicate (что именно вызывает Round 2) и state machine
orchestrator'а здесь не реализованы -- максимум один round-2 exchange будет
закреплён отдельно.

## 5. SimulationResult остаётся физическим

`aios.evaluation.SimulationRequest`/`SimulationResult` не дублируются и не
меняются этой задачей. `SimulationResult` остаётся только физическим
результатом и provenance симуляции; в него не добавлены `official_npv`,
`npv`, `chdd` или `objective_value`. CHDD -- отдельная downstream authority,
считающая экономику по уже полученному `SimulationResult`, а не часть
контракта симуляции.

## 6. Official realized constraints vs search bound

StateCard уже различает official realized constraints (например,
`producer_bhp_min`, `injector_bhp_max`, `wlpr_max` -- физические пределы,
проверяемые Realized Constraint Guardian) и search-границы (например,
`producer_lrat_max` -- внутренний предел поиска, не совпадающий по смыслу с
official `WLPR`). `aios.mas` не вводит вторую копию этих числовых констант:
специалисты видят их как evidence через `StateCard.constraints`, но
authority остаётся у Case constraints и Guardian.

## 7. Compensation -- формула не зафиксирована

Организаторы подтвердили test corridor 0.85--1.15, но точное официальное
определение compensation остаётся нерешённым. `aios.mas.envelopes` несёт
`compensation_state: EvidenceArtifact | None` как evidence-слот, но в
контрактном слое нет функции `official_compensation(...)` и инженерный VRR
нигде не выдаётся за официальную формулу.

## 8. FIPFAULT -- prior, не доказательство нулевой связности

`GeologyContext`/`GeologyCard` -- prior/evidence о принадлежности скважин
блокам и отношениях разломов, не доказательство гидравлической изоляции.
`REALLOCATE_INJECTION` (одна region-область в source и target) и
`REBALANCE_ACROSS_BLOCKS` (разные region-области) -- это семантика surface
allocation, а не утверждение о нулевой межблоковой связности. `ResponseMap`
как динамически выводимое evidence future работы здесь не реализован.

## 9. Pressure/Sweep -- legacy-названные diagnostic services

`src/aios/agents/pressure_agent.py` и `src/aios/agents/sweep_agent.py`
исторически называются agents в коде, но архитектурно в контексте v1.1 они
считаются diagnostic/evidence services: их выход попадает в
`pressure_diagnostic`/`sweep_diagnostic` (`EvidenceArtifact`) внутри
Envelope, а не участвует напрямую в candidate selection. Переименование --
отдельная безопасная задача `MAS-DIAGNOSTICS-001`, вне рамок этой работы; в
этой задаче их код и поведение не менялись.

## Что не входит в этот контрактный слой

Production/Injection agent behavior, PromptBuilder, InputValidator,
EvidenceBuilder, BaselinePolicy, LLM API, ProposalCompiler, GroundingAuditor,
Arbiter reasoning, conflict predicate, MagnitudeResolver rules, numerical
intensity mapping, PreActionValidator, PredictiveSafetyScreen, MPC/Tier0-2,
MemoryStore, DecisionTrace, ResponseMap algorithm, geology extractor,
изменения CHDD/Guardian/Pressure/Sweep, compensation formula, UI.
