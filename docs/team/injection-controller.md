# Injection Controller -- Ксения

## MISSION

Injection Controller -- specialist, предлагающий структурные намерения
(`InjectionIntent`) по управлению закачкой на основе `InjectionEnvelope`. Это
не cognitive decision-maker с authority: он не выставляет численные
setpoints, не запускает симулятор, не считает CHDD и не является Arbiter.
LLM (если включён) предлагает гипотезу; ProposalCompiler/GroundingAuditor
превращают её в grounded, типизированный `InjectionProposal`.

## INPUT

`aios.mas.envelopes.InjectionEnvelope` -- единственный официальный вход.
Содержит:

- `state_card: StateCard`
- `horizon_context: HorizonContext`
- `memory_slice: MemorySlice`
- `allowed_actions: tuple[InjectionAction, ...]`
- diagnostic evidence: `pressure_diagnostic`, `sweep_diagnostic`,
  `geology_card`, `response_map`, `compensation_state`
  (все -- `EvidenceArtifact | None`)
- `peer_proposals` -- пусто на Round 1, непусто только на Round 2

**Baseline НЕ входит в Envelope.** Это структурная гарантия (T1,
anti-anchoring): `ProductionEnvelope`/`InjectionEnvelope` не имеют поля
baseline, и будущий `SpecialistPromptBuilder.build(envelope)`
(`aios.mas.protocols`) структурно не принимает baseline вторым аргументом.

## OUTPUT

`aios.mas.proposals.InjectionAgentOutput`:

```python
InjectionAgentOutput(
    primary_proposal: InjectionProposal,   # источник: LLM_LIVE/LLM_LOCAL/LLM_REPLAY/DETERMINISTIC_FALLBACK
    baseline_proposal: InjectionProposal,  # source ОБЯЗАН быть DETERMINISTIC_BASELINE
)
```

`primary_proposal` и `baseline_proposal` -- **два разных объекта**
(anti-anchoring), оба обязаны ссылаться на один и тот же `envelope_digest`.
`primary` не обязан быть LLM-источником: для `llm_enabled=False` ablation
допустим `DETERMINISTIC_FALLBACK`.

`InjectionProposal.intents: tuple[InjectionIntent, ...]` -- уже
провалидированные, grounded (`GroundingReport.passed == True`) структурные
намерения. Ни `InjectionIntent`, ни `InjectionProposal` не содержат
числовых setpoint-полей (проверено структурно, `dataclasses.fields`).

## OWNED FUTURE PATHS

`src/aios/mas/specialists/injection/` -- пакет ещё не существует, эта задача
его не создаёт. На своей feature-ветке (`feature/injection-controller-001`)
Ксения создаёт эту область самостоятельно. Внутри неё будущие компоненты:

- `InputValidator`
- `EvidenceBuilder` (для Injection-специфичного evidence: превращает typed
  diagnostic output, включая будущий `PressureDiagnostic`/`SweepDiagnostic`
  adapter, в `EvidenceArtifact`)
- role-specific `BaselinePolicy` profile
- `PromptBuilder` (реализация `SpecialistPromptBuilder[InjectionEnvelope]`)
- provider-independent LLM branch
- `StrictOutputParser`
- `VocabularyValidator` (сверяет LLM-вывод с уже frozen `InjectionAction`
  vocabulary -- **новый enum не придумывается**)
- `GroundingAuditor`
- `ProposalCompiler`
- Injection Controller facade, реализующий
  `Agent[InjectionEnvelope, InjectionAgentOutput]` (`aios.mas.protocols.Agent`)

## READ-ONLY DEPENDENCIES

Frozen contracts, которые Ксения **читает, но не меняет** без approval
integration owner (полный список -- `docs/team/branching-and-handoff.md`):
`aios.mas.common`, `aios.mas.intents`, `aios.mas.evidence`,
`aios.mas.envelopes`, `aios.mas.proposals`, `aios.mas.protocols`.
`aios.state.StateCard` и `aios.diagnostics.{PressureDiagnostic,SweepDiagnostic}`
-- тоже read-only (принадлежат другим владельцам).

## PIPELINE STAGES

```
InjectionEnvelope
   -> InputValidator
   -> EvidenceBuilder (evidence уже в envelope -> контекст для промпта)
   -> BaselinePolicy (строит DETERMINISTIC_BASELINE InjectionProposal)
   -> [если llm_enabled] PromptBuilder -> LLM provider -> StrictOutputParser
   -> VocabularyValidator (только frozen InjectionAction/Direction/Intensity)
   -> GroundingAuditor (evidence_ids реально существуют в envelope)
   -> ProposalCompiler (собирает primary InjectionProposal; all-or-nothing)
   -> InjectionAgentOutput(primary, baseline)
```

## FORBIDDEN AUTHORITY

- Не создаёт `ProducerControl`/`InjectorControl` и не импортирует их.
- Не запускает `SimulatorRuntime`/существующий `Simulator`.
- Не владеет CHDD.
- Не является Arbiter и не производит `ArbiterDecision`.
- Не реализует `MagnitudeResolver`.
- Baseline не передаётся в LLM prompt.
- Round 1: `peer_proposals` envelope обязан быть пуст -- он не видит чужое
  предложение (Production).
- Round 2: peer proposal виден только если Orchestrator явно запросил
  deterministic conflict round (`Round2Request`, `aios.mas.arbiter`) и роль
  `INJECTION` в нём присутствует.
- Не придумывает новый `InjectionAction`/`Direction`/`Intensity` -- vocabulary
  уже frozen в `aios.mas.intents`/`aios.mas.common`.

## FIRST DELIVERABLE

**`INJECTION-CONTROLLER-001`**

Цель: реализовать role-specific deterministic pipeline shell (см. PIPELINE
STAGES), где LLM-провайдер может быть injected fake -- чтобы не
блокироваться на внешней LLM policy.

## DEFINITION OF DONE

- valid `InjectionEnvelope` -> valid `InjectionAgentOutput`;
- `llm_enabled=False` путь работает и возвращает `DETERMINISTIC_FALLBACK`
  primary;
- baseline -- отдельный объект, никогда не строится из primary и наоборот;
- baseline никогда не попадает во вход `PromptBuilder`;
- ни один numerical setpoint нигде не появляется;
- невалидный proposal целиком отклоняется (all-or-nothing, не частичный
  fallback на "почти валидный" результат);
- ровно один grounding/schema retry, если LLM-branch подключён (описать
  контракт retry, реализация может подождать отдельного under-task);
- Round 1 envelope без peer proposals обрабатывается корректно;
- Round 2 envelope с peer proposal обрабатывается, если роль запрошена.

## TEST EXPECTATIONS

- unit-тесты каждой стадии pipeline (InputValidator, GroundingAuditor и
  т.д.) отдельно;
- integration-тест полного `propose(envelope) -> InjectionAgentOutput` с
  fake provider;
- тест на отсутствие baseline в prompt input (структурная проверка через
  `aios.mas.invariants.require_no_baseline_field`, где применимо);
- тест на grounded-only accepted proposal (`GroundingReport.passed`).

## HANDOFF TO NEXT COMPONENT

`InjectionAgentOutput` уходит в `ArbiterEnvelope.injection_output`
(central integration owner собирает `ArbiterEnvelope` из выходов обоих
специалистов). Ксения не вызывает Arbiter сама -- это делает Orchestrator
(Лиза Д.) на своей стадии pipeline.
