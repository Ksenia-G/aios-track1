# Runtime / Execution зона (RUNTIME-EXECUTION-001)

Граница зоны Hybrid MAS v1.1:

```
MagnitudeCandidate          (frozen contract, aios.mas.magnitude)
        ↓  aios.execution.compiler
Schedule / wells_schedule.inc
        ↓  SimulationRequest (существующий тип)
aios.execution.simulator_runtime.SimulatorRuntime
        ↓  существующий протокол aios.evaluation.Simulator
OpmFlowSimulator -> OPM Flow (docker из спецификации кейса)
        ↓
SimulationResult            (существующий тип, физика-only) + RuntimeProvenance
        ↓
RealizedConstraintGuardian -> CHDD -> deterministic selector
```

Зона отвечает на вопрос «как корректно исполнить кандидата и получить
физический результат». Она не решает, какой кандидат лучше: magnitude-правила
живут в MagnitudeResolver (выше), выбор победителя -- в deterministic selector
(ниже).

## Модули

| Модуль | Ответственность |
| --- | --- |
| `compiler.py` | `compile_candidate_schedule`: кандидат + срез состояния + опорная сетка кейса -> детерминированное complete schedule; провенанс компиляции (SHA-256 текста, canonical control digests) |
| `execution.py` | Полный прогон (`run_full_opm`), restart-цепочка (`run_restart_chain`), нормализованный физический документ сводки, reconciliation |
| `simulator_adapter.py` | `OpmFlowSimulator` -- единственная реализация протокола `Simulator` над реальным движком; типизированные исходы SUCCESS/TIMEOUT/CRASHED/INVALID_INPUT |
| `provenance.py` | `ExecutionMode` (FULL/RESTART), `RuntimeProvenance` -- след каждого исполнения без wall-clock в identity |
| `simulator_runtime.py` | Фасад: компиляция -> request -> симулятор -> результат+провенанс; батч-гарант `ensure_simulation_floor` |

## Инварианты

* **HOLD exact carry-forward.** HOLD-кандидат обязан буквально переносить
  предыдущие canonical controls; проверка делегируется готовому инварианту
  `require_hold_carry_forward` (без float tolerance). Нарушение --
  `MasInvariantError`.
* **Complete schedule semantics.** На дату действия выписан весь фонд
  (изменённые и неизменённые скважины явно); при более поздней дате действия
  первый шаг окна явно несёт carry-forward состояния; остальные шаги пустые --
  управление действует до следующего явного изменения по семантике расписания
  симулятора. Файл читается симулятором без ручной правки.
* **Determinism.** Один и тот же кандидат даёт один и тот же SHA-256
  расписания и один и тот же simulation key независимо от порядка входных
  кортежей и машины.
* **Никакой экономики внутри runtime.** Артефакт результата --
  нормализованный физический документ сводки
  (`format: aios.execution.opm_summary`). NPV/ЧДД считаются ниже по конвейеру;
  поле в результате появиться не может.
* **Провал не притворяется успехом.** Отказ симулятора возвращает
  типизированный результат со статусом CRASHED/TIMEOUT/INVALID_INPUT и
  заполненным `SimulationFailure`; исключение адаптера перехватывается фасадом
  и фиксируется как CRASHED c `stage=simulator.run`.
* **Restart -- только ускорение.** Окончательным авторитетом для заявляемого
  NPV остаётся чистый полный прогон (`ExecutionMode.FULL`). Каждое
  restart-исполнение несёт в провенансе спецификацию перезапуска и отчёт
  `reconcile_summaries` (дефолтный допуск `DEFAULT_RECONCILIATION_TOLERANCE`
  -- малый численный epsilon). Соответствие состояний устанавливается строго
  по дате из INTEHEAD; механика колоды -- в `aios.opm.restart`.

## Что НЕ входит в зону

Injection/Production Controller, Arbiter, MagnitudeResolver decision rules,
Constraint Guardian semantics, формулы CHDD, LLM, closed-loop orchestration,
UI. Runtime исполняет кандидата, а не оценивает его инженерную ценность.
