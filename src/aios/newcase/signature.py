"""Сигнатура загруженной колоды: похожа ли она на модель нового кейса.

Организатор описывает состояние конца 2011 года примерно как 5425 м3/сут
жидкости и 5550 м3/сут закачки. Унаследованная Model Y даёт заметно другое
состояние, и экономика нового кейса, посчитанная на ней, относилась бы к
другому промыслу.

Это ДИАГНОСТИКА, а не ограничение. Сигнатура ничего не запрещает: она не
отвергает траектории, не участвует в Guardian и не может уронить прогон на
синтетических фикстурах. Её единственная задача -- вовремя сказать, что на
входе, судя по всему, не та модель.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: Ориентиры организатора на конец 2011 года, м3/сут.
REFERENCE_LIQUID_M3_DAY = 5425.0
REFERENCE_INJECTION_M3_DAY = 5550.0

#: Допуск по каждому показателю. Десять процентов: цифры организатора названы
#: приблизительно ("~"), а разница с унаследованной колодой куда крупнее -- она
#: видна и при куда более щедром допуске.
SIGNATURE_TOLERANCE = 0.10


class ModelSignature(StrEnum):
    """Вердикт сравнения с ориентиром."""

    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class SignatureReport:
    """Что измерено, с чем сравнивалось и что из этого следует."""

    verdict: ModelSignature
    liquid: float | None
    injection: float | None
    liquid_ratio: float | None
    injection_ratio: float | None

    def to_json(self) -> dict[str, object]:
        return {
            "verdict": str(self.verdict),
            "measured": {"liquid": self.liquid, "injection": self.injection},
            "reference": {
                "liquid": REFERENCE_LIQUID_M3_DAY,
                "injection": REFERENCE_INJECTION_M3_DAY,
            },
            "ratio": {"liquid": self.liquid_ratio, "injection": self.injection_ratio},
            "tolerance": SIGNATURE_TOLERANCE,
            "note": (
                "Диагностика соответствия колоды новому кейсу. Ограничением не "
                "является: ни одна траектория по ней не отвергается."
            ),
        }


def model_signature(*, liquid: float | None, injection: float | None) -> SignatureReport:
    """Сравнить состояние конца истории с ориентирами организатора."""
    if liquid is None or injection is None:
        return SignatureReport(ModelSignature.UNAVAILABLE, liquid, injection, None, None)

    liquid_ratio = float(liquid) / REFERENCE_LIQUID_M3_DAY
    injection_ratio = float(injection) / REFERENCE_INJECTION_M3_DAY
    within = all(
        abs(ratio - 1.0) <= SIGNATURE_TOLERANCE for ratio in (liquid_ratio, injection_ratio)
    )
    return SignatureReport(
        ModelSignature.MATCH if within else ModelSignature.MISMATCH,
        float(liquid),
        float(injection),
        liquid_ratio,
        injection_ratio,
    )


__all__ = [
    "REFERENCE_INJECTION_M3_DAY",
    "REFERENCE_LIQUID_M3_DAY",
    "SIGNATURE_TOLERANCE",
    "ModelSignature",
    "SignatureReport",
    "model_signature",
]
