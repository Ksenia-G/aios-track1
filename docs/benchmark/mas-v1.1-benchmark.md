# MAS v1.1 competitive benchmark

Measures whether Hybrid MAS produces economic edge against the management
baseline and our experiment candidates **under one physical and economic
frame**. It is not a proof that MAS works — that is closed — and it is not a
tuning exercise: no variant is optimised here.

## Reproducibility command

```bash
export MODEL_Y_ROOT=/path/to/model-y-deck      # OPM-compatible deck
export CHDD_ROOT=/path/to/CHDD_PYTHON          # official calculator package

PYTHONPATH=src uv run python -m aios.benchmark \
  --suite mas-v1.1 \
  --variants baseline,exp003,exp004,mas-off,mas-on \
  --workspace-root /private/tmp/aios-mas-benchmark \
  --output ./benchmark-results \
  --decision-steps 1
```

No source edit is required to run a subset — pass `--variants baseline,mas-off`.
`--validate-only` resolves the variants and reports cognitive-provider
availability without launching a simulation.

Workspaces live outside the repository on purpose: a single run writes hundreds
of megabytes of `.UNSMRY`/`.EGRID`/`.UNRST`, none of which belongs in history.
Only the compact `benchmark_summary.json` is meant to be kept.

## Variants

| id | family | run kind | source |
|---|---|---|---|
| `B0_BASELINE` | BASELINE | fixed schedule replay | `experiments/exp001/baseline.json` |
| `B1_EXP003` | EXP003 | fixed schedule replay | re-derived from `compensation_scenarios` |
| `B1_EXP003_HISTORICAL` | EXP003 | historical reference | `experiments/exp003/summary.json` |
| `B2_EXP004` | EXP004 | fixed schedule replay | `experiments/exp004/best_candidate.json` |
| `B3_MAS_OFF` | MAS_OFF | closed-loop policy | `build_real_closed_loop` |
| `B4_MAS_ON` | MAS_ON | closed-loop policy | same, cognitive branch enabled |

Every variant is evaluated through the same OPM image, the same
`ConstraintGuardian`, the same CHDD calculator and the same horizon. Only the
origin of the controls differs.

## Fairness before ranking

A number is only comparable inside its frame, so the frame is recorded with it:
case and deck identity, simulator image, runtime mode, starting state digest,
horizon bounds, CHDD calculator version, Guardian version and compensation
policy. Comparisons are classified, never assumed:

- **DIRECT_COMPARABLE** — identical frame and identical run kind.
- **NORMALIZED_COMPARABLE** — same frame, different mechanism (a closed-loop
  policy against a fixed schedule) or a normalisable frame difference.
- **REFERENCE_ONLY** — an ingested historical record, not a run reproduced
  under this frame.
- **NOT_COMPARABLE** — case, deck, horizon, CHDD version or compensation mode
  differ; the numbers describe different questions.

A candidate that failed Guardian, produced non-finite economics, or is only
`REFERENCE_ONLY`/`NOT_COMPARABLE` cannot be the headline candidate regardless of
its NPV.

## Schedule lineage

Schedule identity is checked, not assumed. Each reference variant is rebuilt
from its recorded scenario and its digest compared with the recorded one:

- `B0_BASELINE` and `B2_EXP004` **reproduce bit-exactly**.
- `B1_EXP003` **does not**. The EXP003 winner is a supplemental-injection
  scenario, and `render_full_schedule` (MAS-FOUNDATION-001) changed how that
  renders once history bytes became a preserved prefix. The historical NPV
  therefore describes a schedule current code no longer builds, so it is
  carried as `B1_EXP003_HISTORICAL` (reference only) while `B1_EXP003` is the
  re-derived candidate actually evaluated under this frame.

This is exactly the trap the benchmark exists to avoid: the two EXP003 numbers
look like the same candidate and are not.

## Compensation

The official compensation formula remains unconfirmed by the organisers. The
benchmark records `compensation_policy` per run and defaults to
`OFFICIAL_UNKNOWN_PORT`. EXP003 and EXP004 were searched under research
screening assumptions; that is a caveat on their provenance, not grounds to
disqualify them under a rule nobody has confirmed.

## Cognitive provider

`B4_MAS_ON` requires an approved provider. Availability is detected without
reading or printing secrets, and a missing provider produces
`MAS_ON_BLOCKED_BY_PROVIDER` rather than a fabricated run.

## First measured comparison

Deck `compat_p002`, image `openporousmedia/opmreleases@sha256:18c497f6…`,
horizon 2014-01-01 → 2015-12-01, 108 report steps, 49 wells, CHDD
`7.0.2-negative-row-filter`, compensation `OFFICIAL_UNKNOWN_PORT`.

**`PRE-END-STAMP HISTORICAL SCREEN / REFERENCE_ONLY`.** The figures below —
including `B0_BASELINE` 1030.8777 and `B1_EXP003_HISTORICAL` 1190.5934 —
predate the CHDD interval END-stamp correction (`c320087`) and are on the old
START-stamp scale. They are kept as lineage, not as a current claim, and are
not comparable with the frozen PERF-004 numbers.

| variant | NPV mln RUB | Δ vs B0 | Δ% | class | Guardian | sims | controls | LLM |
|---|---:|---:|---:|---|---|---:|---:|---|
| `B2_EXP004` | 1826.2679 | +795.3902 | +77.16% | DIRECT | PASS | 1 | 211 | off |
| `B1_EXP003_HISTORICAL` | 1190.5934 | +159.7157 | +15.49% | REFERENCE_ONLY | PASS | 0 | 0 | off |
| `B1_EXP003` | 1187.2390 | +156.3613 | +15.17% | DIRECT | PASS | 1 | 144 | off |
| `B0_BASELINE` | 1030.8777 | — | — | reference | PASS | 1 | 120 | off |
| `B3_MAS_OFF` | 1028.2096 | −2.6681 | −0.26% | NORMALIZED | PASS | 2 | 49 | off |

`B0` and `B2` reproduce their recorded NPV exactly. `B1_EXP003` is the
re-derived candidate (1187.2390) and differs from the historical record
(1190.5934) because its schedule differs — the two are listed separately
rather than merged.

> **Historical note.** Everything in this section describes the FIRST measured
> comparison, taken before an approved live cognitive provider was integrated.
> At that time `B4_MAS_ON` was **not run**: `aios.mas.specialists.*.provider`
> held a Protocol and a deterministic fake with no live client, which was a
> missing capability rather than missing credentials.
>
> That is no longer the current state. The live cognitive branch exists, and
> the current frozen MAS ON economic measurement is
> **`PERF-004-CAUSAL-CLEAN-001`** (code `205ad86`): MAS OFF 5325.05 →
> MAS ON 5342.72 mln RUB organizer-compatible, **+17.68 mln RUB (+0.332 %)**.
> See [`submission_evidence/`](../../submission_evidence/README.md) for the
> auditable package. Do not read the table above as the current headline.

## What the first run says about MAS

`B3_MAS_OFF` does not beat the baseline. It ran two simulations and committed
HOLD, and its CHDD input digest is identical to the standalone HOLD smoke.

The cause is visible in the candidate set, not in the economics: with the
cognitive branch disabled both specialists return their deterministic
fallback, which is a HOLD intent. The Arbiter therefore receives no non-HOLD
structural proposal, no agent bundle is built, and the resolver produces only
HOLD and the deterministic baseline. **MAS OFF is structurally unable to
exceed its own floor** — there is no upside candidate in the set to select.

The residual −2.67 mln RUB against `B0` is schedule lineage, not decision
quality: MAS compiles HOLD through `compile_candidate_schedule` while the
baseline renders through the experiment path, and the two produce different
schedule text for the same intent. This is why the comparison is classified
NORMALIZED rather than DIRECT.

Consequently the strongest submission-safe candidate on this evidence remains
`B2_EXP004` at 1826.2679, and MAS has not yet been shown to add economic edge.
`B2_EXP004` passes Guardian under re-run, but its provenance caveat stands: it
was searched under research compensation assumptions while the official rule is
unconfirmed.

Full machine-readable record, including every manifest digest and the fairness
matrix: `docs/benchmark/benchmark_summary.json`.
