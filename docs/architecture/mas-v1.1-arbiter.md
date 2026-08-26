# DeterministicArbiter -- deterministic arbitration core (ARBITER-001)

## 1. Второй эшелон

Arbiter не запускается параллельно со специалистами -- он получает уже
готовые `ProductionAgentOutput`/`InjectionAgentOutput` через `ArbiterEnvelope`
и строит candidate space:

```text
ProductionAgentOutput ──┐
                        ├──> DeterministicArbiter.decide(envelope)
InjectionAgentOutput  ──┘
                              |
                        ArbiterDecision
                              |
                        MagnitudeResolver (не реализован здесь)
```

`aios.mas.arbitration` -- реализация (central integration write zone), не
frozen contract: сами типы `ArbiterEnvelope`/`DecisionBundle`/`ArbiterDecision`
живут в `aios.mas.envelopes`/`aios.mas.arbiter` и этой задачей не изменены.

## 2. Specialist outputs -> structural bundles

`DeterministicArbiter.decide()` строит `DecisionBundle`, ссылаясь на
specialist proposals и intents только по их существующему frozen digest
(`Proposal.digest()`, `Intent.digest()`) -- никогда по значению. Ни один
`ProducerControl`/`InjectorControl`/`MagnitudeCandidate` здесь не появляется.

## 3. HOLD + deterministic baseline никогда не пропадают

На каждый вызов `decide()` в `ArbiterDecision.bundles` гарантированно
попадают ровно один `CandidateKind.HOLD` (без ссылок на proposals/intents) и
ровно один `CandidateKind.DETERMINISTIC_BASELINE` (ссылающийся на
`baseline_proposal` обоих специалистов, никогда на `primary_proposal`). Они
защищены от вытеснения tight simulation budget: если
`SimulationBudget.max_candidates < 2`, `decide()` явно отказывает
(`ArbitrationError`), а не молча теряет floor.

## 4. Структурная новизна != provider provenance

`Proposal.digest()` включает `source`/`provenance` (`model_id`,
`prompt_digest`...), а `Intent.digest()` включает `evidence_ids`/
`uncertainty`/`rationale_short` -- ни то, ни другое не подходит критерием
"добавил ли primary что-то новое по сравнению с baseline". Один и тот же
Intent, предложенный LLM и детерминированным fallback, не должен тратить два
слота simulation budget только из-за разных `model_id`.

Поэтому `aios.mas.arbitration.engine` строит собственный internal structural
signature -- ТОЛЬКО из prescriptive полей:

- Production: `action`, `direction`, `intensity`, `target_wells`,
  `target_regions`;
- Injection: `action`, `direction`, `intensity`, `donor_wells`,
  `recipient_wells`, `source_regions`, `target_regions`.

Это canonical дайджест через тот же `aios.evaluation.canonical.digest`, а не
второй JSON canonicalizer. Primary считается structurally novel, если
canonical (order-independent) набор structural-дайджестов его intents
отличается от такого же набора у baseline.

## 5. Bundle dedup игнорирует audit/rationale-only отличия

Дедупликация candidate pool использует тот же structural signature (не
`Intent.digest()`, не `Proposal.digest()`), объединённый в один digest
`{production_intents, injection_intents}`. Два кандидата с одинаковой
prescriptive структурой, но разной `rationale_short`/`latency_ms`/
`declined_alternatives`, схлопываются в один -- эти поля audit-only и не
входят в structural identity.

## 6. Никаких числовых setpoints

`DeterministicArbiter` не создаёт `ProducerControl`/`InjectorControl`/
`MagnitudeCandidate`, не назначает `RATE`/`BHP`/`LRAT`/injection rate. Это
проверено AST-аудитом импортов пакета (`tests/test_arbiter_authority.py`).

## 7. Simulation budget discipline

Floor (`HOLD` + `DETERMINISTIC_BASELINE`) резервирует первые 2 слота всегда.
Оставшийся бюджет (`max_candidates - 2`) уходит на `AGENT_BUNDLE`:

1. **Combined primary** (`exploration=False`) -- сторона, где primary
   structurally novel, берёт primary; сторона без новизны канонизируется к
   baseline (чтобы provenance-only отличие не создало fake exploration).
   Не создаётся вообще, если ни одна сторона не novel.
2. **Mixed alternatives** (`exploration=True`) -- "Production primary +
   Injection baseline" (если Production novel) и "Production baseline +
   Injection primary" (если Injection novel). Совпадающие структурно с
   combined primary схлопываются дедупликацией.
3. Если два mixed alternatives конкурируют за один оставшийся слот, порядок
   определяется единым combined structural digest кандидата (не позиционным
   сравнением tuple'ов по ролям -- это давало бы скрытый "Production first"
   bias, и было явно найдено и исправлено при разработке). Ни role
   preference, ни random, ни wall-clock, ни model latency не участвуют в
   выборе.

## 8. Round2 conflict policy инжектируется, не зашита

`DeterministicArbiter` вызывает `conflict_policy.evaluate(envelope) ->
Round2Request | None` ровно один раз. Default policy в ARBITER-001 --
`NoRound2ConflictPolicy`, которая детерминированно возвращает `None`.

Это не решение "конфликтов не бывает": калиброванного правила вида
"INCREASE_OFFTAKE + REDUCE_SUPPORT_PATH -- это конфликт" или "разные
FIPFAULT blocks независимы" не существует, и придумывать его здесь --
непроверенная petroleum-domain эвристика. Wiring для Round 2 существует и
протестирован (`decide()` принимает любую `ConflictPolicy`); domain conflict
predicate -- отдельная будущая калиброванная задача.

## 9. Cognitive/provider ветка -- ARBITER-002

Этот документ описывает только deterministic core/fallback Arbiter.
LLM-провайдер, PromptBuilder для Arbiter, парсер cognitive-ответа -- всё это
вне рамок ARBITER-001 и будет отдельной задачей ARBITER-002.

## 10. Arbiter priority -- не прогноз NPV

`DecisionBundle.priority_rank` -- порядок рассмотрения/simulation
scheduling, а не экономический ранкинг и не предсказание ЧДД. Финальная
authority по-прежнему ниже по цепочке:

```text
ArbiterDecision -> MagnitudeResolver -> simulation -> Guardian -> CHDD
```

Ни одна доля кода/документации в этой задаче не утверждает
`NPV(MAS) >= NPV(BASELINE)` -- такой теоремы не существует. Гарантия ровно
одна: HOLD и deterministic baseline физически присутствуют в structural
candidate space на каждом decision step; сохранение этого свойства после
`MagnitudeResolver` отдельно проверяется
`aios.mas.invariants.require_simulation_floor` перед симуляцией.
