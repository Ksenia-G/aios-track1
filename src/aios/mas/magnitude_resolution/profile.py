"""Model Y empirical magnitude profile -- evidence-backed anchors only.

Каждый numerical anchor здесь -- значение, реально протестированное в
EXP-003/EXP-004 (`experiments/exp004/scenarios.csv`,
`experiments/exp003/compensation.csv`), а не эстетически подобранный
процент. `SMALL=10%`/`MODERATE=25%`/`AGGRESSIVE=50%` в духе произвольной
шкалы здесь не существует: если для действия нет протестированной grid
point, соответствующая intensity не получает anchor вообще (`None`), а не
интерполированное число.

Naming convention экспериментов (`EXP004_B_PROD_110`, `EXP004_A_INJ_090`):
код после подчёркивания = factor*100 (например, `110` -> множитель 1.10).
Источник: `src/aios/experiments/exp004.py` (`ProducerUplift`, `InjectionScale`)
и `experiments/exp004/scenarios.csv`.

Профиль -- чистые статические Python-данные. Runtime resolver НЕ парсит
markdown/CSV на каждом decision step -- research evidence уже свёрнуто в
эти константы один раз, на этапе написания кода.
"""

from __future__ import annotations

from dataclasses import dataclass

from aios.mas.common import Intensity

PROFILE_VERSION = "model_y_empirical_v1"


@dataclass(frozen=True, slots=True)
class MagnitudeAnchor:
    """Один evidence-backed numerical anchor. Не officially confirmed formula --
    просто протестированная точка исторического experiment grid."""

    value: float
    source_experiment: str
    source_case: str
    note: str


# ---------------------------------------------------------------------------
# Producer offtake uplift: target = min(previous_rate * factor, ceiling).
# Formula provenance: aios.experiments.exp004.ProducerUplift.target().
# Grid provenance: experiments/exp004/scenarios.csv, B_PRODUCER family
# (EXP004_B_PROD_110/125/150/200/300/500 -> factors 1.10/1.25/1.50/2.00/3.00/5.00).
# ---------------------------------------------------------------------------

_PRODUCER_UPLIFT_FACTORS: dict[Intensity, MagnitudeAnchor] = {
    Intensity.SMALL: MagnitudeAnchor(
        value=1.10,
        source_experiment="EXP-004",
        source_case="EXP004_B_PROD_110",
        note=(
            "Smallest tested standalone producer uplift factor (experiments/exp004/scenarios.csv)."
        ),
    ),
    Intensity.MODERATE: MagnitudeAnchor(
        value=1.50,
        source_experiment="EXP-004",
        source_case="EXP004_B_PROD_150",
        note="Mid tested standalone producer uplift factor.",
    ),
    Intensity.AGGRESSIVE: MagnitudeAnchor(
        value=5.00,
        source_experiment="EXP-004",
        source_case="EXP004_B_PROD_500 / EXP004_C_JOINT_P500_I600",
        note=(
            "Maximum tested producer uplift factor; also the exact factor used "
            "by EXP-004's own best joint candidate (P500 = factor 5.00)."
        ),
    ),
}

# ---------------------------------------------------------------------------
# Total injection scaling: candidate_rate = previous_rate * factor, all
# injectors (aios.experiments.exp004.InjectionScale, wells=None semantics).
# Grid: A_INJECTION family (EXP004_A_INJ_060/080/090/110/125/150 -> factors
# 0.60/0.80/0.90/1.10/1.25/1.50).
# ---------------------------------------------------------------------------

_INJECTION_SCALE_UP_FACTORS: dict[Intensity, MagnitudeAnchor] = {
    Intensity.SMALL: MagnitudeAnchor(
        1.10, "EXP-004", "EXP004_A_INJ_110", "Smallest tested standalone injection increase."
    ),
    Intensity.MODERATE: MagnitudeAnchor(
        1.25, "EXP-004", "EXP004_A_INJ_125", "Mid tested standalone injection increase."
    ),
    Intensity.AGGRESSIVE: MagnitudeAnchor(
        1.50, "EXP-004", "EXP004_A_INJ_150", "Maximum tested standalone injection increase."
    ),
}

_INJECTION_SCALE_DOWN_FACTORS: dict[Intensity, MagnitudeAnchor] = {
    Intensity.SMALL: MagnitudeAnchor(
        0.90, "EXP-004", "EXP004_A_INJ_090", "Smallest tested standalone injection decrease."
    ),
    Intensity.MODERATE: MagnitudeAnchor(
        0.80, "EXP-004", "EXP004_A_INJ_080", "Mid tested standalone injection decrease."
    ),
    Intensity.AGGRESSIVE: MagnitudeAnchor(
        0.60, "EXP-004", "EXP004_A_INJ_060", "Maximum tested standalone injection decrease."
    ),
}

# ---------------------------------------------------------------------------
# Injection reallocation transfer fraction: donor -= fraction*donor_rate,
# recipient += fraction*donor_rate (conservation), single donor/recipient
# pair only -- matches the exact tested pattern.
# Formula provenance: aios.experiments.exp002.TransferSpec /
# aios.experiments.exp004.Exp004Policy.plan() (TRANSFER mode).
# Grid provenance: EXP003_D_COMP_ADD_42_100's own transfer set
# (experiments/exp003/compensation.csv) -- the officially best EXP-003
# candidate's 4 donor/recipient pairs used exactly 3 distinct fractions:
# 0.35, 0.825, 0.975 (0.975 appears twice, for two different pairs).
# ---------------------------------------------------------------------------

_REALLOCATION_TRANSFER_FRACTIONS: dict[Intensity, MagnitudeAnchor] = {
    Intensity.SMALL: MagnitudeAnchor(
        0.35,
        "EXP-003",
        "EXP003_D_COMP_ADD_42_100 (block 2, donor 41 -> receiver 19)",
        "Smallest transfer fraction in the best EXP-003 candidate's own transfer set.",
    ),
    Intensity.MODERATE: MagnitudeAnchor(
        0.825,
        "EXP-003",
        "EXP003_D_COMP_ADD_42_100 (block 3, donor 32 -> receiver 4)",
        "Mid transfer fraction in the best EXP-003 candidate's own transfer set.",
    ),
    Intensity.AGGRESSIVE: MagnitudeAnchor(
        0.975,
        "EXP-003",
        "EXP003_D_COMP_ADD_42_100 (blocks 4/5, donors 7 & 26)",
        "Maximum transfer fraction in the best EXP-003 candidate's own transfer set.",
    ),
}


@dataclass(frozen=True, slots=True)
class ModelYEmpiricalMagnitudeProfile:
    """Единственная production empirical magnitude profile для MR-001.

    Ни один anchor не экстраполирован за пределы протестированного grid и не
    является organizer-confirmed или NPV-optimal значением -- это research
    evidence, использованное как conservative/nominal/aggressive search
    anchors, не hard rule.
    """

    version: str = PROFILE_VERSION

    def producer_uplift_factor(self, intensity: Intensity) -> MagnitudeAnchor | None:
        return _PRODUCER_UPLIFT_FACTORS.get(Intensity(intensity))

    def injection_scale_factor(
        self, intensity: Intensity, *, direction_up: bool
    ) -> MagnitudeAnchor | None:
        table = _INJECTION_SCALE_UP_FACTORS if direction_up else _INJECTION_SCALE_DOWN_FACTORS
        return table.get(Intensity(intensity))

    def reallocation_transfer_fraction(self, intensity: Intensity) -> MagnitudeAnchor | None:
        return _REALLOCATION_TRANSFER_FRACTIONS.get(Intensity(intensity))
