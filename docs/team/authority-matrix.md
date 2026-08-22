# Authority matrix

Кто что может делать -- в терминах уже замороженных контрактов
(`docs/architecture/mas-v1.1-contracts.md`) и системных инвариантов T1--T7
(`src/aios/mas/invariants.py`). Ничего из перечисленного ниже как "будущий
компонент" не реализовано в этом репозитории -- это frozen authority map для
того, что предстоит построить.

| Component | Owner | Can read StateCard/evidence | Can propose structure (Intent/Proposal) | Can produce numerical setpoints | Can run the simulator | Can reject a realized-constraint candidate | Can compute final CHDD | Can commit final decision |
|---|---|---|---|---|---|---|---|---|
| Production Controller (specialist) | Вшивцева | YES | YES | **NO** | NO | NO | NO | NO |
| Injection Controller (specialist) | Ксения | YES | YES | **NO** | NO | NO | NO | NO |
| Arbiter | central integration | YES (structural outputs) | NO (consumes, doesn't create Intent) | **NO** | NO | NO | NO | NO |
| MagnitudeResolver | central integration | YES (ArbiterDecision) | NO | **YES** (единственная разрешённая точка) | NO | NO | NO | NO |
| PredictiveSafetyScreen | Вшивцева (будущий task) | YES | NO | NO | NO | **NO** (только annotate/rank, не veto/filter) | NO | NO |
| SimulatorRuntime (execution layer) | Марк | YES (MagnitudeCandidate) | NO | NO | **YES** | NO | NO | NO |
| Realized Constraint Guardian | central integration (существующий, не переносится в этой задаче) | YES | NO | NO | NO | **YES** | NO | NO |
| CHDD | central integration | YES (SimulationResult) | NO | NO | NO | NO | **YES** | NO |
| Orchestrator | Доценко | координирует все стадии | NO | NO | NO (вызывает SimulatorRuntime) | NO (вызывает Guardian) | NO (вызывает CHDD) | вызывает deterministic selection, сам authority не подменяет |
| Final deterministic selection | central integration | -- | -- | -- | -- | -- | -- | выбирает **только** среди simulated + Guardian-passed кандидатов по authoritative CHDD |

## Ключевые правила, из которых собрана таблица

- **NO SETPOINTS CROSS.** Ни Production/Injection Controller, ни Arbiter не
  могут произвести `ProducerControl`/`InjectorControl` или любое
  prescriptive numerical поле. Единственная разрешённая точка --
  `MagnitudeCandidate` (`aios.mas.magnitude`), после `MagnitudeResolver`.
- **SIMULATION FLOOR.** На каждом decision step среди resolved candidates
  обязаны присутствовать `CandidateKind.HOLD` и
  `CandidateKind.DETERMINISTIC_BASELINE` (`require_simulation_floor`,
  `aios.mas.invariants`). Это не теорема про NPV -- только гарантия
  присутствия.
- **SAFETY SCREEN IS NOT A FILTER.** PredictiveSafetyScreen annotate/ranks,
  но не имеет права удалить, добавить или задублировать candidate
  (`require_candidate_preservation`).
- **CHDD authority.** Только CHDD считает финальный экономический показатель;
  `SimulationResult` остаётся физическим и не содержит `npv`/`chdd`/
  `official_npv`/`objective_value` полей.
- **Final selector.** Коммитится только кандидат, прошедший и полную
  симуляцию, и Realized Constraint Guardian, и оценённый CHDD -- никогда
  кандидат, отфильтрованный по одному только internal search score.
- **Orchestrator == coordinator, не authority.** Доценко пишет pipeline
  wiring и последовательность вызовов; она не переопределяет решения
  Guardian/CHDD/MagnitudeResolver внутри своего кода.
