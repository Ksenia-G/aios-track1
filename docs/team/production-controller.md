# Production Controller -- Елизавета Вшивцева

## MISSION

Production Controller -- specialist, предлагающий структурные намерения
(`ProductionIntent`) по управлению отбором на основе `ProductionEnvelope`.
Симметричен Injection Controller (см.
`docs/team/injection-controller.md`): не cognitive decision-maker с
authority, не выставляет numerical setpoints, не запускает симулятор, не
считает CHDD, не является Arbiter.

## INPUT

`aios.mas.envelopes.ProductionEnvelope` -- единственный официальный вход.
Содержит:

- `state_card: StateCard`
- `horizon_context: HorizonContext`
- `memory_slice: MemorySlice`
- `allowed_actions: tuple[ProductionAction, ...]`
- diagnostic evidence: `pressure_diagnostic`, `sweep_diagnostic`,
  `geology_card`, `response_map`, `compensation_state`,
  **`economic_context`** (только у Production, у Injection этого поля нет)
- `peer_proposals` -- пусто на Round 1, непусто только на Round 2

Baseline НЕ входит в Envelope -- та же структурная T1-гарантия, что и у
Injection Controller.

## OUTPUT

`aios.mas.proposals.ProductionAgentOutput`:

```python
ProductionAgentOutput(
    primary_proposal: ProductionProposal,   # LLM_LIVE/LLM_LOCAL/LLM_REPLAY/DETERMINISTIC_FALLBACK
    baseline_proposal: ProductionProposal,  # source ОБЯЗАН быть DETERMINISTIC_BASELINE
)
```

Оба -- отдельные объекты, один и тот же `envelope_digest`.
`ProductionProposal.intents: tuple[ProductionIntent, ...]` -- grounded,
структурные, без числовых setpoint-полей.

## OWNED FUTURE PATHS

`src/aios/mas/specialists/production/` -- ещё не существует, создаётся на
`feature/production-controller-001`. Будущие компоненты внутри (симметрично
Injection):

- `InputValidator`
- `EvidenceBuilder`
- role-specific `BaselinePolicy` profile
- `PromptBuilder` (реализация `SpecialistPromptBuilder[ProductionEnvelope]`)
- provider-independent LLM branch
- `StrictOutputParser`
- `VocabularyValidator` (только frozen `ProductionAction` -- новый enum не
  придумывается)
- `GroundingAuditor`
- `ProposalCompiler`
- Production Controller facade, реализующий
  `Agent[ProductionEnvelope, ProductionAgentOutput]`

## READ-ONLY DEPENDENCIES

Те же frozen contracts, что у Injection Controller: `aios.mas.common`,
`aios.mas.intents`, `aios.mas.evidence`, `aios.mas.envelopes`,
`aios.mas.proposals`, `aios.mas.protocols`, `aios.state.StateCard`,
`aios.diagnostics.{PressureDiagnostic,SweepDiagnostic}`.

## PIPELINE STAGES

```
ProductionEnvelope
   -> InputValidator
   -> EvidenceBuilder
   -> BaselinePolicy (строит DETERMINISTIC_BASELINE ProductionProposal)
   -> [если llm_enabled] PromptBuilder -> LLM provider -> StrictOutputParser
   -> VocabularyValidator (только frozen ProductionAction/Direction/Intensity)
   -> GroundingAuditor
   -> ProposalCompiler (all-or-nothing)
   -> ProductionAgentOutput(primary, baseline)
```

## FORBIDDEN AUTHORITY

- Не создаёт/не импортирует `ProducerControl`/`InjectorControl`.
- Не запускает `SimulatorRuntime`/`Simulator`.
- Не владеет CHDD authority.
- Не является Arbiter, не производит `ArbiterDecision`.
- Не реализует `MagnitudeResolver`.
- Baseline вне LLM prompt.
- Round 1 -- `peer_proposals` пуст. Round 2 -- виден, только если Orchestrator
  запросил conflict round и роль `PRODUCTION` в нём присутствует.
- `ProductionAction` -- уже frozen enum, новый не вводится.

## PredictiveSafetyScreen (резерв, НЕ в этой задаче)

За Вшивцевой резервируется отдельная будущая задача:
`PredictiveSafetyScreen`. Не реализовывать её в первом deliverable. Когда
придёт время: Screen может только **annotate/rank**, сохраняя exact
cardinality и identity кандидатов (`aios.mas.invariants.require_candidate_preservation`).
Он не имеет veto и не является candidate filter -- см.
`docs/team/authority-matrix.md`.

## FIRST DELIVERABLE

**`PRODUCTION-CONTROLLER-001`** -- аналогичный Injection shell, на
`ProductionEnvelope`/`ProductionProposal`.

## DEFINITION OF DONE

- valid `ProductionEnvelope` -> valid `ProductionAgentOutput`;
- deterministic baseline -- отдельный объект;
- provider-off (`llm_enabled=False`) путь работает;
- ни один numerical setpoint не появляется нигде;
- vocabulary contract соблюдён (`ProductionAction`/`Direction`/`Intensity` --
  без новых значений);
- grounding строгий (all-or-nothing);
- baseline вне prompt;
- round-семантика (Round 1 без peer, Round 2 -- только по запросу)
  соблюдена.

`PredictiveSafetyScreen` **не входит** в этот первый commit -- второй task
после controller shell.

## TEST EXPECTATIONS

Симметрично Injection Controller: unit-тесты по стадиям, integration-тест
полного propose() с fake provider, тест на отсутствие baseline в prompt
input, тест на grounded-only proposal.

## HANDOFF TO NEXT COMPONENT

`ProductionAgentOutput` уходит в `ArbiterEnvelope.production_output`,
который собирает central integration owner. Orchestrator (Доценко) вызывает
Production Controller как часть своей pipeline-стадии.
