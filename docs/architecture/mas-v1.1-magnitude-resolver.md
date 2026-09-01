# DeterministicMagnitudeResolver -- первая prescriptive граница (MAGNITUDE-RESOLVER-001)

## 1. Authority boundary

До `aios.mas.magnitude_resolution` вся система (specialists, Arbiter)
работает только со structural intents/proposals/bundles, ссылаясь друг на
друга по digest. `MagnitudeResolver` -- ПЕРВАЯ и ЕДИНСТВЕННАЯ граница, где
`ProducerControl`/`InjectorControl` реально появляются:

```text
ProductionAgentOutput ─┐
                       ├─> ArbiterDecision
InjectionAgentOutput ──┘
                              |
                     DeterministicMagnitudeResolver
                              |
                     MagnitudeCandidate
                              |
                     PreActionValidator (не реализован здесь)
                              |
                     Simulation (не реализована здесь)
```

## 2. Почему Resolver -- единственный numerical prescriptive источник

`NO SETPOINTS CROSS UNTIL MAGNITUDE RESOLVER` -- это уже гарантировано
frozen-контрактами (`aios.mas.intents`/`proposals`/`envelopes`/`arbiter` не
содержат ни одного numerical control field, проверено structural- и
AST-тестами предыдущих задач). `aios.mas.magnitude_resolution` -- единственный
модуль пакета `aios.mas` (кроме самого `magnitude.py`), которому разрешено
импортировать `ProducerControl`/`InjectorControl`.

## 3. Frozen input/output contracts

`DeterministicMagnitudeResolver.resolve(decision: ArbiterDecision) ->
tuple[MagnitudeCandidate, ...]` реализует уже существующий frozen
`aios.mas.protocols.MagnitudeResolver` -- сигнатура `resolve()` не менялась
и не должна меняться. `ArbiterDecision`/`DecisionBundle` НЕ несут: сами
Intent-объекты (только их digest), previous numerical controls,
deterministic baseline numeric controls. Эта информация приходит через
explicit `MagnitudeContext`, переданный в конструктор -- тем же способом,
каким `DeterministicArbiter` получает `conflict_policy` (ARBITER-001), а не
через изменение frozen envelope/arbiter схемы.

```python
MagnitudeContext(
    effective_date: date,
    arbiter_envelope: ArbiterEnvelope,   # для dereference intent digest -> Intent
    previous_producers/injectors,        # текущий commit'нутый numeric state (для HOLD)
    baseline_producers/injectors,        # deterministic baseline numeric snapshot
)
```

Ни `magnitude.py`, ни `arbiter.py`, ни любой другой frozen contract файл
этой задачей не изменены.

## 4. HOLD -- точный carry-forward

`CandidateKind.HOLD` резолвится в `context.previous_producers`/
`previous_injectors` буквально, без empirical policy и без пересчёта.

## 5. Baseline -- точное сохранение

`CandidateKind.DETERMINISTIC_BASELINE` резолвится в
`context.baseline_producers`/`baseline_injectors` напрямую -- baseline
structural intents НЕ прогоняются через empirical profile, даже если они
формально доступны через `arbiter_envelope`.

## 6. Model Y empirical profile

`aios.mas.magnitude_resolution.profile.ModelYEmpiricalMagnitudeProfile`
(`model_y_empirical_v1`) -- единственный источник числовых anchors для
`AGENT_BUNDLE`. Ни один anchor не изобретён: каждый -- реально
протестированная точка `EXP-003`/`EXP-004` grid.

## 7. Точный provenance экспериментов

| Механизм | Формула | Источник |
|---|---|---|
| Producer uplift | `target = min(previous_rate * factor, ceiling)` | `aios.experiments.exp004.ProducerUplift.target()`, `experiments/exp004/scenarios.csv` (family `B_PRODUCER`) |
| Injection scale | `target = previous_rate * factor`, все injectors | `aios.experiments.exp004.InjectionScale`, family `A_INJECTION` |
| Injection transfer | `donor -= f*donor_rate; receiver += f*donor_rate` | `aios.experiments.exp002.TransferSpec` / `Exp004Policy.plan()` TRANSFER mode; фракции -- из `EXP003_D_COMP_ADD_42_100`'s собственного transfer set |
| Ceiling | `500.0` | `aios.cases.contract.TRACK1_WLPR_HARD_MAX` (единственный источник, не продублирован) |

Naming convention экспериментов: код после подчёркивания = `factor*100`
(`EXP004_B_PROD_110` -> factor 1.10).

## 8. Почему EXP-003-подобное evidence -- nominal reference

`EXP003_D_COMP_ADD_42_100` -- лучший EXP-003 кандидат: экономически лучше
baseline, `ROBUST_INSIDE` по всем исследованным K1 compensation
interpretations, и требует МЕНЬШЕ water inventory (K1B: 185 908 м³), чем
даже сам baseline (227 410 м³). Его собственные 3 различных transfer
fraction (0.35 / 0.825 / 0.975) -- единственная протестированная grid
реаллокации закачки в репозитории, поэтому именно они -- SMALL/MODERATE/
AGGRESSIVE anchors для `REALLOCATE_INJECTION`.

## 9. Почему EXP-004-подобное evidence -- aggressive/exploration reference

`EXP004_C_JOINT_P500_I600` (producer factor 5.00, injection factor 6.00) --
самый высокий исторический NPV, но K1B показал: required initial water
inventory ≈ 599 813 м³, в 2.64× больше baseline и в 3.23× больше
EXP-003 best. Поэтому его producer factor (5.00) используется только как
AGGRESSIVE exploration anchor для `INCREASE_OFFTAKE` -- НЕ как unconditional
nominal default. Выбор magnitude anchor -- это empirical search design, не
переоткрытие water compliance внутри resolver'а.

## 10. Никаких произвольных процентов

`SMALL=10%`/`MODERATE=25%`/`AGGRESSIVE=50%` в духе эстетической шкалы здесь
не существует. Каждый anchor протестирован в `tests/test_magnitude_resolver_profile.py`
(P1-P11) на принадлежность реально протестированному grid.

## 11. Без numerical extrapolation за пределы протестированного grid в MR-001

Ни один anchor не выходит за диапазон фактически запущенных сценариев
(`experiments/exp004/scenarios.csv`). Интерполяция между grid points не
выполняется -- используются только сами tested точки.

## 12. Action coverage matrix

### ProductionAction

| Action | Статус | Механизм |
|---|---|---|
| `HOLD` | точный carry-forward | `context.previous_producers` |
| `INCREASE_OFFTAKE` | SUPPORTED_WITH_EMPIRICAL_POLICY | `ProducerUplift`-style factor, ceiling = `TRACK1_WLPR_HARD_MAX` |
| `REDUCE_OFFTAKE` | **UNSUPPORTED_WITHOUT_NEW_ASSUMPTION** | EXP-001..004 никогда не тестировали producer-side уменьшение (только uplift); fail-closed |
| `SHUT_IN` | SUPPORTED_STRUCTURALLY_WITH_EXISTING_CONTROL_SEMANTICS | `status=SHUT`, `liquid_rate=None`, тот же `replace(template, ...)` паттерн, что и в `exp004.py` |
| `REOPEN` | SUPPORTED_STRUCTURALLY_WITH_EXISTING_CONTROL_SEMANTICS | canonical source = `context.baseline_producers` (не guessed rate); fail-closed, если baseline для этой скважины не OPEN |
| `REBALANCE_WITHIN_BLOCK` | **UNSUPPORTED_WITHOUT_NEW_ASSUMPTION** | нет established equal-split policy для нескольких target_wells; fail-closed |

### InjectionAction

| Action | Статус | Механизм |
|---|---|---|
| `HOLD` | точный carry-forward | `context.previous_injectors` |
| `SCALE_TOTAL_INJECTION` | SUPPORTED_WITH_EMPIRICAL_POLICY | `InjectionScale`-style factor, все injectors |
| `SUPPORT_REGION` | SUPPORTED, только если `recipient_wells` явно задан | тот же factor-механизм, scoped к `recipient_wells`; region-only targeting требует `GeologyContext`, недоступного resolver'у -- fail-closed |
| `REDUCE_SUPPORT_PATH` | SUPPORTED, только если `donor_wells` явно задан | symmetric, DOWN factors; region-only -- fail-closed |
| `REALLOCATE_INJECTION` | SUPPORTED только для ровно одного donor и одного recipient | transfer fraction anchors; multi-well donor/recipient -- fail-closed (нет доказанного split) |
| `REBALANCE_ACROSS_BLOCKS` | **UNSUPPORTED_WITHOUT_NEW_ASSUMPTION** | multi-well donor/recipient без установленной split policy; fail-closed |

## 13. Fail-closed для unsupported actions

Unresolvable `AGENT_BUNDLE` не превращается ни в HOLD, ни в baseline, ни в
угаданную ставку -- он просто исключается из возвращаемого набора
кандидатов (`MagnitudeResolutionError` перехватывается только для
`AGENT_BUNDLE`; для `HOLD`/`DETERMINISTIC_BASELINE` та же ошибка
пробрасывается наружу, потому что floor обязан резолвиться всегда).

## 14. Границы: никакого симулятора, Guardian, CHDD

`aios.mas.magnitude_resolution` не импортирует `Simulator`/
`SimulationResult`/`ConstraintGuardian`/`NpvEvaluator`/`aios.chdd`
(AST-проверено, `tests/test_magnitude_resolver_authority.py`).

## 15. Никакой official compensation/water rule

K1 (`ROBUST_INSIDE`/`SENSITIVE_TO_DEFINITION`/...) и K1B
(`required_initial_inventory`) -- evidence, использованное только для
ВЫБОРА empirical anchors (EXP-003 nominal, EXP-004 aggressive), а не как
runtime gate внутри resolver'а. Ни одно условие вида "если water_inventory >
X -- reject" здесь не закодировано.

## 16. Детерминизм

Один и тот же `ArbiterDecision` + `MagnitudeContext` + profile всегда дают
byte-идентичный набор `MagnitudeCandidate.digest()` -- canonical ordering
через уже существующие `natural_key`/`canonical_control_digest`
(`aios.mas.invariants`), без random/wall-clock/dict-порядка.

## 17. Оставшаяся будущая работа

- Cognitive/provider (LLM) resolver -- отдельная задача.
- `PreActionValidator`, `PredictiveSafetyScreen` -- ниже по цепочке, не
  реализованы здесь.
- Region-based (`GeologyContext`-aware) разрешение `SUPPORT_REGION`/
  `REDUCE_SUPPORT_PATH` без явного well list.
- Established split policy для `REBALANCE_WITHIN_BLOCK`/
  `REBALANCE_ACROSS_BLOCKS`/multi-well `REALLOCATE_INJECTION`.
- Evidence-backed anchor для `REDUCE_OFFTAKE`, если появится тестовый grid.

Этот resolver -- не финальный/глобально оптимальный MagnitudeResolver.
Simulation, Realized Constraint Guardian и CHDD остаются downstream
authorities.
