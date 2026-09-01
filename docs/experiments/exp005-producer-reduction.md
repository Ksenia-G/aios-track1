# EXP-005 — producer reduction calibration

Supplies the half of the producer axis EXP-004 never tested, so that
`REDUCE_OFFTAKE` can either gain an evidence-backed magnitude or stay
unsupported for a stated reason. It is a calibration, not a search for profit.

## Why it exists

`DeterministicMagnitudeResolver` refuses `REDUCE_OFFTAKE`, and that refusal is
correct: `ModelYEmpiricalMagnitudeProfile` only carries anchors for values that
were actually simulated, and EXP-004's `B_PRODUCER` family tested
`1.10 / 1.25 / 1.50 / 2.00 / 3.00 / 5.00` — every point an increase. The
injection axis was calibrated in both directions (`A_INJECTION`:
`0.60 / 0.80 / 0.90 / 1.10 / 1.25 / 1.50`); the producer axis was not.

MAS-PERF-001 made the cost concrete: five live Production proposals, all
`REDUCE_OFFTAKE`, all discarded before numerical resolution. The cognitive layer
was working and its output had nowhere to go.

## Grid derivation

No magnitude is invented here. The tested factors are **0.90, 0.80, 0.60** —
exactly the decrease magnitudes this project already accepted on the injection
axis. Mirroring them onto the producer axis means the only new variable under
test is the axis, not the size of the step. `0.90` is additionally the mirror
of `B_PRODUCER`'s smallest tested step (`1.10`, i.e. ±10%).

Formula is the existing one, unchanged:
`target = min(baseline_rate × factor, ceiling)` — identical in
`exp004.ProducerUplift.target()` and in the resolver's `INCREASE_OFFTAKE`
branch, so a reduction anchor plugs into the same arithmetic.

## Two arms — the targeting question

The resolver applies an intensity factor **generically and multiplicatively to
each well in the specialist's chosen target set**. EXP-004 calibrated with
`wells=None`, i.e. the whole field at once. The live specialist instead selects
subsets (4–10 of 33 producers). A whole-field calibration does not by itself
license subset application, because the remaining producers partially
compensate for a subset cut.

So both are tested:

| arm | wells | purpose |
|---|---|---|
| **G** | all producers (`wells=None`) | methodological twin of `B_PRODUCER` |
| **S** | top 6 producers by realized baseline water cut | does a generic factor survive subsetting? |

The subset rule is deterministic and physically motivated: the wettest
producers are the canonical reason to cut offtake at all.

## Frame

Identical to the accepted benchmark frame — case `model_y_validation`, deck
`compat_p002`, image `openporousmedia/opmreleases@sha256:18c497f6…`, horizon
2014-01-01 → 2015-12-01, the real `ConstraintGuardian`, the official CHDD
calculator, compensation `OFFICIAL_UNKNOWN_PORT`. Six new full OPM runs; the
baseline reference already exists.

Run with existing tooling only — `ProducerUplift`, `Exp004Scenario`,
`build_exp004_schedule`, `run_full_opm`, `ConstraintGuardian`, `run_chdd`. No
source architecture change was required to execute the grid.

## Anchor acceptance

A tested point qualifies as an anchor under the profile's existing policy —
"a value really tested in EXP-003/EXP-004", not a value chosen because it looked
good. NPV outcome does not gate anchor validity: `B_PROD_110` is an anchor and
was never the best NPV. What gates it is that the run completed physically and
passed Guardian, and that the arm structure supports the way the resolver will
apply it.

Reducing offtake is expected to *lower* NPV on this case — less liquid means
less oil. That does not make the calibration useless: the resolver needs to
know what a `SMALL`/`MODERATE`/`AGGRESSIVE` reduction *is* before a specialist
can propose one in a situation where cutting a watered-out well is the right
call. It does mean `REDUCE_OFFTAKE` should not be expected to win this
particular benchmark.

## Results

See `exp005_results.json` beside this document for the full record — every grid
point, its schedule digest, realized state digest, Guardian verdict, CHDD input
digest and NPV. No point is omitted.
