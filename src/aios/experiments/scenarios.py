"""Перечень сценариев экрана EXP-001.

Сценарии не хранятся файлами и не выписываются руками: они порождаются
детерминированно из скрининговых групп и набора долей переноса. Один и тот
же перечень всегда даёт один и тот же порядок и одни и те же идентификаторы.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from aios.cases.contract import Case
from aios.experiments.blocks import (
    BLOCKS_BY_ID,
    ScreeningBlock,
    screening_blocks_with_redistribution,
)
from aios.experiments.transfer import InjectionTransfer, InjectionTransferPolicy

FIRST_PASS_FRACTION = 0.15
REFINEMENT_FRACTIONS: tuple[float, ...] = (0.05, 0.10, 0.20, 0.30)

BASELINE_SCENARIO_ID = "EXP001_BASELINE"


def fraction_tag(fraction: float) -> str:
    """0.15 -> `F15`. Доля вне сетки процентов не допускается."""

    percent = fraction * 100.0
    if abs(percent - round(percent)) > 1e-9:
        raise ValueError(f"fraction {fraction} is not a whole percent")
    return f"F{round(percent):02d}"


@dataclass(frozen=True, slots=True)
class Scenario:
    """Один прогон экрана."""

    scenario_id: str
    block_id: int
    donor: str
    receiver: str
    fraction: float

    @property
    def is_baseline(self) -> bool:
        return self.scenario_id == BASELINE_SCENARIO_ID

    @property
    def block(self) -> ScreeningBlock:
        return BLOCKS_BY_ID[self.block_id]

    def transfer(self, case: Case) -> InjectionTransfer:
        return InjectionTransfer(
            donor_well=self.donor,
            receiver_well=self.receiver,
            fraction=self.fraction,
            start_date=case.management_start_date,
            end_date=case.management_end_date,
        )

    def policy(self, case: Case) -> InjectionTransferPolicy:
        return InjectionTransferPolicy(
            transfer=self.transfer(case), policy_id=f"INJECTION_TRANSFER::{self.scenario_id}"
        )

    def to_json(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "block": self.block_id,
            "donor": self.donor,
            "receiver": self.receiver,
            "fraction": self.fraction,
        }


def scenario_id(block_id: int, donor: str, receiver: str, fraction: float) -> str:
    return f"EXP001_B{block_id}_{donor}_TO_{receiver}_{fraction_tag(fraction)}"


def make_scenario(block_id: int, donor: str, receiver: str, fraction: float) -> Scenario:
    return Scenario(
        scenario_id=scenario_id(block_id, donor, receiver, fraction),
        block_id=block_id,
        donor=donor,
        receiver=receiver,
        fraction=fraction,
    )


def first_pass_scenarios(fraction: float = FIRST_PASS_FRACTION) -> tuple[Scenario, ...]:
    """Все упорядоченные пары внутри каждой пригодной группы."""

    return tuple(
        make_scenario(block.block_id, donor, receiver, fraction)
        for block in screening_blocks_with_redistribution()
        for donor, receiver in block.ordered_pairs()
    )


def refinement_scenarios(
    pairs: Iterable[tuple[int, str, str]], fractions: Sequence[float] = REFINEMENT_FRACTIONS
) -> tuple[Scenario, ...]:
    """Уточнение по долям для отобранных направлений."""

    return tuple(
        make_scenario(block_id, donor, receiver, fraction)
        for block_id, donor, receiver in pairs
        for fraction in fractions
    )


def baseline_scenario() -> Scenario:
    """Контроль. Донор и получатель не заданы -- воздействия нет."""

    return Scenario(
        scenario_id=BASELINE_SCENARIO_ID, block_id=0, donor="", receiver="", fraction=0.0
    )


def window_of(case: Case) -> tuple[date, date]:
    return case.management_start_date, case.management_end_date
