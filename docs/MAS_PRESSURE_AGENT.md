# MAS Pressure/Voidage Agent

## Role

Pressure/Voidage Agent is the first proposal-generating domain agent in the Track 1 MAS stack. It represents reservoir pressure management and produces structured injection-side proposals from the current `StateCard`.

It exists to separate domain perception from legality and arbitration. The agent can identify pressure-support risks and injector capacity limitations, but it does not decide whether an action is legal, optimal, or economically useful.

## Architecture

```text
Simulator
  -> SummaryData
  -> StateExtractor
  -> StateCard
  -> PressureAgent
  -> PressureProposal
  -> ConstraintGuardian
  -> Arbiter later
```

PressureAgent reads only `StateCard`. It must not read simulator files, `SummaryData`, decks, CSV files, experiment outputs, restart artifacts, or schedules.

## Ownership

The agent owns deterministic pressure/voidage proposal logic:

- Detect producer BHP margin approaching the official floor.
- Detect injector BHP margin approaching the official ceiling.
- Identify injector-side targets available in the current StateCard.
- Emit one deterministic `PressureProposal`.

The proposal contains:

- `metadata.state_card_digest`
- `metadata.agent_version`
- `proposal_type`
- `target`
- `reason.pressure_condition`
- `reason.affected_region`
- `reason.expected_effect`
- `confidence`
- `risk_flags`

## Non-Goals

PressureAgent does not:

- run a simulator;
- edit schedules;
- validate official legality;
- report official constraint violations;
- optimize NPV;
- rank candidates;
- call an LLM;
- override Constraint Guardian.

## Relationship With Constraint Guardian

Constraint Guardian is the legality layer and has veto power. PressureAgent only proposes. Later orchestration must pass pressure proposals through Constraint Guardian and an Arbiter before any action can become a command.

This separation prevents pressure-management heuristics from being mistaken for official organizer constraints. For example, `PRESSURE_SUPPORT_NEEDED` is a pressure-risk flag, not an official violation.

## Fail-Closed Behavior

Missing StateCard data blocks proposal generation. The agent never substitutes zeros, skips wells, or reads raw artifacts to fill gaps.

Required data includes:

- field `FPR`;
- field `FVPR`;
- field `FVPT`;
- field `FWIR`;
- producer `WBHP` and producer BHP margin;
- injector `WBHP`, `WWIR`, and injector BHP margin.

The deterministic v1 rules are intentionally small. Future MAS tasks may add richer proposal types, regional geological context, or action payloads without changing the StateCard boundary.
