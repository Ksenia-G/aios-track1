# Branching and handoff

## Один общий SHA для старта

Команда стартует не с разных старых веток, а с одной точки:

```
TEAM_FOUNDATION_SHA = <exact SHA сообщается captain после финального
                        независимого QG прогона на integration/team-foundation-v1.1>
```

До получения этого SHA от captain не начинать feature-ветку -- иначе четыре
человека снова окажутся на разных базах.

## Рекомендуемые feature-ветки

Все -- от одного и того же `TEAM_FOUNDATION_SHA`:

```
integration/team-foundation-v1.1  (TEAM_FOUNDATION_SHA)
                 |
       +---------+---------+---------------------+
       |                   |                      |
       v                   v                      v
feature/injection-   feature/production-   feature/closed-loop-
controller-001       controller-001        orchestrator-001
                                                    |
                                                    +---- feature/runtime-execution-001
                                                          (тоже от TEAM_FOUNDATION_SHA,
                                                           не от orchestrator-ветки)
```

| Участник | Ветка |
|---|---|
| Ксения | `feature/injection-controller-001` |
| Лиза В. | `feature/production-controller-001` |
| Лиза Д. | `feature/closed-loop-orchestrator-001` |
| Марк | `feature/runtime-execution-001` |

Ни одна из этих веток не создаётся в рамках текущей задачи (`TEAM-READY-001`)
и не пушится на remote -- это только рекомендация по именованию.

## Workflow

```
foundation (TEAM_FOUNDATION_SHA)
   |
   v
своя feature-ветка
   |
   v
локальные тесты (targeted + full pytest + ruff)
   |
   v
Pull Request
   |
   v
review (как минимум captain/integration owner)
   |
   v
integration
```

- Прямые push в `main` запрещены.
- Отдельная `develop`-ветка не создаётся без явной необходимости.
- Каждая feature-ветка живёт в своём собственном write zone (см.
  `authority-matrix.md` и раздел "Frozen shared contracts" ниже) -- слияние
  веток разных участников не должно требовать ручного разрешения конфликтов
  в общих contract-файлах, если каждый писал только в свою зону.

## Frozen shared contracts

Следующие файлы -- **frozen shared contracts**, контролируемые central
integration owner. Никто из четырёх участников не меняет их в своей
feature-ветке без отдельного approval со стороны integration owner:

```
src/aios/mas/__init__.py
src/aios/mas/common.py
src/aios/mas/intents.py
src/aios/mas/envelopes.py
src/aios/mas/proposals.py
src/aios/mas/arbiter.py
src/aios/mas/magnitude.py
src/aios/mas/invariants.py
src/aios/mas/protocols.py
```

Если во время работы над feature-веткой обнаруживается, что frozen contract
чего-то не хватает -- это повод для обсуждения с integration owner и,
возможно, отдельной contract-задачи, а не для самостоятельной правки внутри
feature-ветки.

## Write zones (кратко; полная authority -- в role-документах)

| Участник | Write zone |
|---|---|
| Ксения | `src/aios/mas/specialists/injection/` (будет создан на её ветке) + её тесты |
| Лиза В. | `src/aios/mas/specialists/production/` (будет создан на её ветке) + её тесты |
| Лиза Д. | `src/aios/mas/orchestration/` (будет создан на её ветке) + orchestration-тесты |
| Марк | runtime/execution/geology-зоны, определённые discovery на его ветке + его тесты |

`tests/support/` -- общий test-only слой, созданный этой задачей; любой
участник может добавлять к нему новые deterministic factories/stubs, но не
превращает его в production-зависимость (см. `tests/test_team_foundation.py`,
TF14/TF15).
