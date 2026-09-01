# EconomicSelector -- argmax authoritative CHDD (ECONOMIC-SELECTION-001)

## 1. Место в конвейере

`EconomicSelector` -- не optimizer, не simulator и не реализация CHDD. Он
работает СТРОГО после уже выполненных этапов:

```text
MagnitudeCandidate ──> Simulator ──> SimulationResult
                                          |
                          ┌───────────────┼───────────────┐
                          v                               v
              ConstraintGuardian.evaluate         (StateCard extraction,
              (StateCard) -> ConstraintReport       не реализовано здесь)
                          |                               |
                          └───────────────┬───────────────┘
                                          v
                                run_chdd(...) -> ChddResult
                                          |
                                          v
                              EconomicSelector.select(...)
                                          |
                                          v
                              EconomicSelectionResult (winner + audit)
```

Каждая из трёх входных стрелок -- уже существующий authoritative тип
(`aios.evaluation.simulation.SimulationResult`,
`aios.agents.constraint_guardian.ConstraintReport`,
`aios.chdd.ChddResult`). Ни один из них не меняется этой задачей.

## 2. Закон commit-eligibility

```text
commit_eligible(candidate) iff:
    существует SimulationResult, идентичность которого подтверждена;
    simulation.status поддерживает objective evaluation (SUCCESS);
    существует ConstraintReport, идентичность которого подтверждена;
    Guardian verdict = PASS (report.summary.failed == 0);
    существует ChddResult, идентичность которого подтверждена;
    npv_mln_rub конечен.
```

Ни для HOLD, ни для DETERMINISTIC_BASELINE, ни для AGENT_BUNDLE исключений
нет -- все проходят один и тот же закон (section 10/22).

## 3. Authority симуляции

`EconomicSelector` никогда не запускает и не перезапускает симулятор. Он
читает уже произведённый `SimulationResult.status`/`.supports_objective_evaluation`
(`aios.evaluation.simulation`, не изменён). Отказ симуляции -- это
`SIMULATION_FAILED`, а не низкий NPV: кандидат исключается из сравнения, а
не штрафуется числом.

## 4. Authority realized Guardian

`aios.agents.constraint_guardian.ConstraintGuardian` -- единственный
источник PASS/FAIL по официальным реализованным ограничениям (producer
WBHP >= 50, injector WBHP <= 300, producer WLPR <= 500 -- те же
`PRODUCER_BHP_MIN`/`INJECTOR_BHP_MAX`/`WLPR_MAX`, что уже используются в
`src/aios/agents/constraint_guardian.py`). Selector не пересчитывает эти
ограничения и не читает `StateCard` напрямую (проверено AST-тестом A7) --
он потребляет уже готовый `ConstraintReport` и определяет PASS как
`report.summary.failed == 0`.

## 5. Authority CHDD

`aios.chdd.ChddResult` (через `run_chdd`/`parse_chdd_result`) -- официальный
расчёт. Формулы (`price`, `opex`, `capex`, `discount_rate`, погодовое
дисконтирование) не переписываются и не дублируются нигде в
`aios.mas.selection` (проверено текстовым и AST-сканированием, A8). Если
`ChddResult` отсутствует -- кандидат НЕ commit-eligible; approximation
запрещена.

## 6. Никакого пересчёта экономики

Единственная операция selector'а над `ChddResult` -- чтение
`.npv_mln_rub` и проверка его конечности (`math.isfinite`). Годовой профиль
(`yearly_profile`) никогда не суммируется независимо и никогда не участвует
в сравнении лексикографически (section 28) -- сравнивается только заявленный
`totalChddM`/`npv_mln_rub`.

## 7. Точное правило argmax

```text
winner = argmax { chdd.result.npv_mln_rub : candidate in commit_eligible_set }
```

Без взвешенного скора, без штрафа за воду, без штрафа за compensation, без
uncertainty penalty, без bonus за exploration, без предпочтения baseline.

## 8. Детерминированный tie-break

При точном числовом равенстве `npv_mln_rub` побеждает кандидат с
лексикографически МЕНЬШИМ каноническим `candidate.digest()`
(`MagnitudeCandidate.digest()` -- уже существующий frozen-контрактный метод,
section 17). Ascending order, без исключений для HOLD/baseline/agent --
подтверждено поведенческими тестами E15/E17 и A16-A18: тот же candidate_kind
никогда не меняет исход при равном NPV.

## 9. Отсутствие предпочтения по типу кандидата

`candidate_kind` сохраняется в `CandidateEvaluation`/`EligibilityRejection`
только для аудита. Он никогда не участвует ни в одном условии ветвления
или сравнения внутри `economic.py` (проверено AST-тестом A15: ни одного
`if`/`Compare`, читающего `candidate_kind`). HOLD, DETERMINISTIC_BASELINE и
AGENT_BUNDLE после прохождения eligibility рассматриваются абсолютно
одинаково (section 22).

## 10. Равное обращение HOLD/baseline/agent

Если `baseline.npv > agent.npv` -- baseline побеждает. Если `HOLD.npv`
максимален -- побеждает HOLD. Selector не знает, что "agent -- умный", и не
защищает baseline от проигрыша.

## 11. Поведение при отказе симуляции

`SIMULATION_FAILED` -- типизированная причина исключения из
commit-eligible набора, а не `NPV = -inf`. Репозиторий (`aios.evaluation.engine`)
уже придерживается этого же принципа ("Отсутствующий NPV остаётся
отсутствующим: ни 0.0, ни -inf, ни штраф"), и `EconomicSelector` следует
той же дисциплине.

## 12. Поведение при отказе Guardian

`GUARDIAN_FAILED` -- кандидат не commit-eligible независимо от того,
насколько высок его CHDD (E11: "guardian-failed candidate with highest CHDD
still loses"). NPV никогда не переопределяет реализованное нарушение
ограничения (section 13). Аудит сохраняется: `EligibilityRejection` фиксирует
`candidate_identity`/`candidate_kind`/`reason`/`detail` для каждого
отклонённого кандидата, поэтому будущий DecisionTrace может восстановить,
какие кандидаты существовали, какие были симулированы, какие прошли
Guardian, какие получили CHDD, и кто победил (section 12/25).

## 13. Поведение при нулевом eligible-наборе

Если ни один кандидат не commit-eligible, `EconomicSelector.select(...)`
поднимает `NoCommitEligibleCandidateError`. Неявный откат к HOLD, к
baseline или к "наименее плохому" запрещён (section 23): даже HOLD обязан
быть физически и экономически подтверждён для данного шага решения по
частоте архитектуры. Как обрабатывать нулевой eligible-набор -- решает
Orchestrator, а не Selector.

## 14. Защита идентичности/происхождения

Ни `SimulationResult`, ни `ConstraintReport` (agents), ни `ChddResult` не
несут встроенной ссылки друг на друга или на `MagnitudeCandidate` -- каждый
спроектирован независимо. Поэтому `aios.mas.selection.models` вводит три
лёгкие обёртки (`SimulationOutcome`, `GuardianOutcome`, `ChddOutcome`),
каждая из которых explicit заявляет `candidate_identity`
(`MagnitudeCandidate.digest()`, уже существующий канонический метод) и,
начиная с Guardian/CHDD, `simulation_result_digest`. `EconomicSelector`
проверяет эти явные поля друг против друга -- никогда не сопоставляет
записи по позиции в списке (`zip`, индекс) -- см. A14 и поведенческие тесты
E7-E9/E20. Рассинхронизация (кандидат A + Guardian B, либо Guardian A +
CHDD B) детектируется как `*_IDENTITY_MISMATCH` и кандидат отклоняется.

## 15. Дубликаты и несогласованные записи

Если два evaluation-записи заявляют одну и ту же `candidate_identity`:
байт-идентичные -- дедуплицируются детерминированно; несогласованные
(разный SimulationResult/Guardian/CHDD) -- `select()` поднимает
`DuplicateCandidateIdentityError`. Выбор "по порядку в списке" никогда не
применяется (section 19/21).

## 16. Restart/full-run различение -- явное ограничение

В репозитории уже существует явный флаг `execution_mode` (`"full"` и др.) у
`aios.pipeline.validation.PipelineReport`, и отдельно
`RestartRiskPolicy.final_claim_requires_full_run` в `aios.opm.restart` --
но оба относятся к другому, CLI-ориентированному submission-контуру
(`Case -> Schedule -> OPM -> ChddResult`), а не к типам, которые потребляет
этот selector (`SimulationResult`/`ConstraintReport`/`ChddResult` из
`aios.evaluation`/`aios.agents`/`aios.chdd`). Ни один из этих трёх типов не
несёт собственного поля "full run" / "restart-derived". Изобретать такое
поле здесь означало бы фабриковать provenance, которого нет в контракте
(section 14 explicitly запрещает это). Поэтому: **это явное ограничение
текущей реализации**, а не тихое допущение -- ECONOMIC-SELECTION-001 не
различает `SEARCH_ELIGIBLE` и `FINAL_CLAIM_ELIGIBLE` на уровне
`SimulationResult`. Любая система, которой нужно это различие для итоговой
заявки на конкурс, обязана либо расширить authoritative-контракт
(`SimulationResult`/эквивалент) явным полем, либо использовать
`aios.pipeline.validation.PipelineReport.execution_mode` на своём уровне
контура -- но не через angažирование selector'а в эвристику.

## 17. Отсутствие глобальной baseline-теоремы

`EconomicSelector` не утверждает `NPV(MAS) >= NPV(baseline)` глобально. На
одном шаге решения: если HOLD и deterministic baseline были включены выше
по потоку, успешно симулированы, прошли Guardian и получили CHDD, они
участвуют в одном и том же экономическом сравнении наравне с
agent-кандидатами. Это даёт floor кандидата на конкретном шаге, а не
математическую теорему над всем горизонтом closed-loop (section 16).

## 18. K1/K1B не являются целевой функцией

K1 (compensation) и K1B (water source inventory) повлияли на диапазон
поиска `MagnitudeResolver` (SMALL/MODERATE/AGGRESSIVE anchors), но не
модифицируют `EconomicSelector`. Здесь нет `NPV - λ*water_inventory`, нет
`NPV - compensation_risk`, нет Pareto-утилити (section 27) -- ни один такой
токен не встречается в пакете (проверено текстовым сканированием, A10/A11).

## 19. Интеграция с будущим DecisionTrace и оставшаяся ответственность

`EconomicSelectionResult` (селектор, все evaluated/rejected записи, версия
правила) спроектирован так, чтобы DecisionTrace мог позже сериализовать
полный аудит одного шага решения без дополнительной перестройки. Зона
ответственности `EconomicSelector` заканчивается на возврате
`EconomicSelectionResult` или на типизированном отказе
(`NoCommitEligibleCandidateError`/`DuplicateCandidateIdentityError`) --
как обрабатывать эти исходы (переход к следующему шагу, повторная
симуляция, эскалация) решает будущий Orchestrator Лизы Д., который вызывает
`EconomicSelector`, а не наоборот (section 35).
