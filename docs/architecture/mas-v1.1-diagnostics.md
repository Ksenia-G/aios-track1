# PressureDiagnostic / SweepDiagnostic -- канонические diagnostic services

## 1. Что это

`PressureDiagnostic` и `SweepDiagnostic` (`aios.diagnostics`) -- deterministic
evidence-сервисы поверх `StateCard`. Они читают текущее состояние поля и
скважин и возвращают typed diagnostic proposal (`PressureProposal` /
`SweepProposal`) с категорией состояния, целевыми скважинами, структурированной
причиной, confidence и risk flags.

## 2. Чем они НЕ являются

Это не cognitive MAS agents из `aios.mas`. `PressureDiagnostic`/
`SweepDiagnostic`:

- не создают `ProductionIntent`/`InjectionIntent`;
- не создают `ProductionProposal`/`InjectionProposal` (в смысле `aios.mas`);
- не участвуют в `Arbiter` как самостоятельные специалисты;
- не имеют decision authority и не формируют `ArbiterDecision`.

Их выход -- observational evidence (состояние pressure/voidage, water-cut,
efficiency), а не prescriptive numerical setpoint. `pressure_margin`,
`connectivity_score` и подобные числа как evidence разрешены; выставление
`injection_rate=...`/`producer_rate=...` -- нет и здесь не происходит.

## 3. Как их будут использовать specialist agents

```text
StateCard / static context
       |
PressureDiagnostic / SweepDiagnostic
       |
typed deterministic evidence (PressureProposal / SweepProposal)
       |
future EvidenceBuilder adapter
       |
EvidenceArtifact
       |
specialist Envelope (pressure_diagnostic / sweep_diagnostic fields)
       |
Production Agent / Injection Agent (не реализованы в этой задаче)
```

`EvidenceBuilder adapter` этой задачей не реализован: typed diagnostic output
сохранён как есть, потому что превращение его в произвольный dict сейчас
потеряло бы typed domain information без пользы.

## 4. Canonical vs legacy naming

| Canonical (`aios.diagnostics`) | Legacy compatibility (`aios.agents`) |
|---|---|
| `PressureDiagnostic`           | `PressureAgent`                      |
| `SweepDiagnostic`               | `SweepAgent`                         |

`PressureDiagnostic`/`SweepDiagnostic` -- пустые subclass-обёртки над
`PressureAgent`/`SweepAgent`: все поля, defaults, thresholds и `propose()`
унаследованы без изменений. Легаси-имена продолжают импортироваться и
работать без каких-либо runtime deprecation warnings -- это сознательное
решение, чтобы не создавать шум в существующих тестах/пользователях. Ни одна
формула, threshold или физическое допущение этой миграцией не менялись:
для одного и того же `StateCard` canonical и legacy классы дают побайтово
идентичный `to_json()`/`digest()`.

Version-строка (`PRESSURE_DIAGNOSTIC_VERSION`/`SWEEP_DIAGNOSTIC_VERSION`)
намеренно указывает на тот же identity string, что и у legacy
(`aios.agents.pressure_agent/1.0.0` и `aios.agents.sweep_agent/1.0.0`): эта
строка входит в `metadata` каждого proposal и, следовательно, в его digest,
поэтому переименование строки изменило бы output content -- а это уже не
behavior-preserving migration.

## 5. FIPFAULT / geology semantics

Diagnostic-слой не усиливает существующие geology-допущения. Если
Pressure/Sweep реализация уже использует region/block membership как
screen/prior -- это сохранено как есть. `FIPFAULT` block separation
по-прежнему не объявляется доказательством нулевой hydraulic connectivity
между блоками; это осталось prior/evidence, а не hard law.

## 6. Что не входит в эту задачу

Production/Injection Controller, Arbiter, LLM, PromptBuilder, EvidenceBuilder
adapter implementation, BaselinePolicy, GroundingAuditor, ProposalCompiler,
MagnitudeResolver, PredictiveSafetyScreen, Orchestrator, MPC, MemoryStore,
DecisionTrace, ResponseMap, новая geology-модель, новые pressure/sweep
формулы, compensation formula, изменения CHDD/Guardian, UI.
