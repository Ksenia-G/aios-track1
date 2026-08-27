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

## Results

See `benchmark-results/benchmark_summary.json` for the machine-readable record
of the run, including every manifest digest and the full fairness matrix.
