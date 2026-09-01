# Организаторские ограничения: CONFIRMED vs UNRESOLVED

Этот документ -- единственный источник правды о том, что из конкурсных
правил подтверждено, а что всё ещё не определено. Он не заменяет `cases/*.json`
и код (`src/aios/cases/contract.py`, `src/aios/state`, `src/aios/agents`) --
если документ и код расходятся, доверяй коду и открой issue/обсуждение,
а не правь один без другого.

## CONFIRMED

- Producer realized `WBHP >= 50 bar` (`producer_bhp_min` в `cases/model_y_validation.json`,
  используется как `official.producer_bhp_min` в `StateCard.constraints` и
  проверяется `ConstraintGuardian`).
- Injector realized `WBHP <= 300 bar` (`injector_bhp_max`, тот же путь).
- Producer realized **`WLPR <= 500 m3/day`** -- это `wlpr_max`/`producer_wlpr_max`,
  проверенный лабораторно инвариант (`OFFICIAL-003B`, правило S,
  `TRACK1_WLPR_HARD_MAX` в `src/aios/cases/contract.py`): при нарушении
  официальный калькулятор падает целиком.
- Test compensation corridor: `0.85`--`1.15`.
- Тестовая закачка использует produced/associated water.
- В тесте есть total- и per-well/address-ограничения.
- `wells_schedule.inc` должен быть complete с самого начала.
- Изменённые и неизменённые режимы скважин обязаны быть явно представлены
  так, как того требует schedule симулятора.
- Конверсия producer -> injector запрещена
  (`allow_producer_to_injector: false` в конфиге кейса; `aios.mas.intents`
  не содержит такого действия ни для Production, ни для Injection).
- Управления можно менять помесячно/по выбору, но изменения стоят денег.
- Schedule симулятора должен загружаться без ручной коррекции.
- ЧДД выдаётся в млн ₽ + годовой профиль.
- Статическая геология/траектории/перфорации/координаты изменению не подлежат.

## UNRESOLVED

- Точный числитель/знаменатель официальной формулы compensation.
- Поверхностное vs пластовое определение compensation.
- Мгновенное vs накопленное определение.
- Field vs area определение.
- Точная cadence (частота проверки/пересчёта).
- Детали учёта инвентаря добытой воды.
- Точная схема тестовых ограничений (полный формат, который увидит judge).
- Политика внешнего LLM/API judging.

## КРИТИЧЕСКИ ВАЖНЫЕ РАЗЛИЧЕНИЯ

**Official realized constraint -- это НЕ search bound.**

Правильно:

```
official realized producer WLPR <= 500 m3/day   (Guardian, hard official limit)
```

Неправильно писать это как:

```
LRAT <= 500                                      (это НЕ то же самое утверждение)
```

`producer_lrat_max` (`ConstraintState.search`) -- это внутренний search/control
bound, используемый политикой поиска, а не сама official realized проверка.
Значения могут численно совпасть (например, оба 500.0 в текущем кейсе), но
это два разных authority: `official` проверяется `ConstraintGuardian` против
реализованных траекторий, `search` -- ограничивает пространство поиска
control-значений до симуляции. Не путать их друг с другом ни в коде, ни в
документации.

**Инженерный VRR -- это НЕ официальная формула compensation.**

`aios.experiments.vrr` считает voidage replacement как diagnostic величину
для целей EXP-004 (см. `docs/exp004_vrr_upper_bound.md`). Это инженерная
оценка, а не заявленная организаторами официальная формула compensation --
та остаётся в разделе UNRESOLVED выше. Не превращать одну guessed-формулу в
hard gate где-либо в MAS-контуре.

## Geology

`FIPFAULT` block membership -- prior/evidence partition, а не доказательство
нулевой гидравлической связности между блоками. `REALLOCATE_INJECTION` (одна
region-область) и `REBALANCE_ACROSS_BLOCKS` (разные region-области) в
`aios.mas.intents` -- это семантика surface allocation, не утверждение о
hydraulic isolation. Подробнее: `docs/architecture/mas-v1.1-contracts.md`,
раздел 8.
