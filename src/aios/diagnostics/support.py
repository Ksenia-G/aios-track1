"""Связность поддержки и региональный дисбаланс -- детерминированные диагностики.

Живой структурный прогон отклонил перераспределение закачки с явной причиной:
в StateCard не было ничего, что связывало бы нагнетательную скважину с
поддерживаемыми ею добывающими и показывало бы, где поддержки избыток, а где
недостаток. Способность перераспределять при этом уже существовала и была
физически проверена -- не хватало именно свидетельства.

Связность здесь НЕ выдумывается. Единственное основание -- общий отсек по
массиву регионов колоды: скважины разных отсеков не связываются вовсе, а
внутри отсека отношения ранжируются расстоянием между перфорациями. Это
свидетельство о правдоподобной связи, а не утверждение о линиях тока, и
ограничение записано в самой диагностике.

Дисбаланс считается арифметикой по реализованному состоянию: суммы дебитов
скважин отсека, запас по давлению нагнетателей, обводнённость добывающих.
Никаких обученных весов и никаких порогов, оправданных только тем, что они
дают нужный ответ на одной колоде.

Диагностики не содержат численных управлений: они говорят «эти скважины
связаны» и «здесь поддержки больше, чем там», но никогда «закачивать столько».
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from aios.evaluation.canonical import JsonValue
from aios.schedule.events import natural_key
from aios.state import WellRole

CONNECTIVITY_VERSION = "aios.diagnostics.support.connectivity/1.0.0"
IMBALANCE_VERSION = "aios.diagnostics.support.imbalance/1.0.0"

#: Запас по давлению, ниже которого нагнетатель физически не может принять
#: больше: он уже упёрся в свой предел. Это факт состояния, а не настройка.
NO_HEADROOM_BAR = 1e-9


@dataclass(frozen=True, slots=True)
class SupportLink:
    """Нагнетатель и добывающие скважины его же отсека."""

    injector: str
    scope: str | None
    supported_producers: tuple[str, ...] = ()

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "injector": self.injector,
            "scope": self.scope,
            "supported_producers": list(self.supported_producers),
        }


@dataclass(frozen=True, slots=True)
class SupportConnectivity:
    """Кто кого правдоподобно поддерживает, и на каком основании."""

    links: tuple[SupportLink, ...] = ()
    basis: str = "UNKNOWN"
    limitations: str = ""

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "basis": self.basis,
            "limitations": self.limitations,
            "links": [link.to_json() for link in self.links],
        }


@dataclass(frozen=True, slots=True)
class SupportScope:
    """Наблюдаемый баланс поддержки одного отсека."""

    scope: str
    injectors: tuple[str, ...] = ()
    producers: tuple[str, ...] = ()
    injection_rate: float = 0.0
    withdrawal_rate: float = 0.0
    oil_rate: float = 0.0
    mean_water_cut: float | None = None
    surface_support_ratio: float | None = None
    balance: str = "UNKNOWN"
    donor_like: tuple[str, ...] = ()
    recipient_like: tuple[str, ...] = ()
    reason: str = ""

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "scope": self.scope,
            "injectors": list(self.injectors),
            "producers": list(self.producers),
            "injection_rate_sm3_per_day": self.injection_rate,
            "withdrawal_rate_sm3_per_day": self.withdrawal_rate,
            "oil_rate_sm3_per_day": self.oil_rate,
            "mean_water_cut": self.mean_water_cut,
            "surface_support_ratio": self.surface_support_ratio,
            "internal_balance": self.balance,
            "donor_like": list(self.donor_like),
            "recipient_like": list(self.recipient_like),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SupportImbalance:
    """Дисбаланс поддержки по отсекам."""

    scopes: tuple[SupportScope, ...] = ()
    limitations: str = ""
    units: Mapping[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "limitations": self.limitations,
            "units": dict(self.units),
            "scopes": [scope.to_json() for scope in self.scopes],
        }


def _scope_of(well_id: str, geology) -> str | None:
    return dict(geology.block_membership).get(well_id)


def _split(wells: Sequence[object], geology):
    injectors, producers = [], []
    for well in wells:
        (injectors if well.role is WellRole.INJECTOR else producers).append(well)
    injectors.sort(key=lambda w: natural_key(w.well_id))
    producers.sort(key=lambda w: natural_key(w.well_id))
    return injectors, producers


def derive_support_connectivity(*, wells: Sequence[object], geology) -> SupportConnectivity:
    """Связи нагнетатель -> добывающие внутри общего отсека.

    Межотсековые связи не создаются: колода даёт разбиение на отсеки, и
    утверждать переток через границу без данных о ней нельзя.
    """
    membership = dict(geology.block_membership)
    if not membership:
        return SupportConnectivity()

    injectors, producers = _split(wells, geology)
    links = []
    for injector in injectors:
        scope = _scope_of(injector.well_id, geology)
        supported = (
            tuple(
                p.well_id
                for p in producers
                if scope is not None and _scope_of(p.well_id, geology) == scope
            )
            if scope is not None
            else ()
        )
        links.append(
            SupportLink(injector=injector.well_id, scope=scope, supported_producers=supported)
        )
    return SupportConnectivity(
        links=tuple(links),
        basis="shared deck compartment (region array membership)",
        limitations=(
            "Compartment co-membership is evidence of plausible hydraulic contact, not a "
            "streamline or tracer measurement; it neither ranks nor proves individual "
            "injector-producer pairs."
        ),
    )


def derive_support_imbalance(*, wells: Sequence[object], geology) -> SupportImbalance:
    """Где поддержки сравнительно больше, а где меньше -- по реализованному состоянию."""
    membership = dict(geology.block_membership)
    if not membership:
        return SupportImbalance(limitations="no compartment membership; scopes are unknown")

    injectors, producers = _split(wells, geology)
    scopes: dict[str, dict] = {}
    for well in (*injectors, *producers):
        scope = _scope_of(well.well_id, geology)
        if scope is None:
            continue
        bucket = scopes.setdefault(scope, {"inj": [], "prod": []})
        bucket["inj" if well.role is WellRole.INJECTOR else "prod"].append(well)

    built = []
    for scope in sorted(scopes, key=natural_key):
        bucket = scopes[scope]
        inj, prod = bucket["inj"], bucket["prod"]
        injection = sum((w.wwir or 0.0) for w in inj)
        withdrawal = sum((w.wlpr or 0.0) for w in prod)
        oil = sum((w.wopr or 0.0) for w in prod)
        cuts = [w.wct for w in prod if w.wct is not None]
        mean_cut = sum(cuts) / len(cuts) if cuts else None
        ratio = injection / withdrawal if withdrawal > 0 else None

        donor, recipient, balance = _classify(inj)
        built.append(
            SupportScope(
                scope=scope,
                injectors=tuple(w.well_id for w in inj),
                producers=tuple(w.well_id for w in prod),
                injection_rate=injection,
                withdrawal_rate=withdrawal,
                oil_rate=oil,
                mean_water_cut=mean_cut,
                surface_support_ratio=ratio,
                balance=balance,
                donor_like=donor,
                recipient_like=recipient,
                reason=_reason(donor, recipient),
            )
        )
    return SupportImbalance(
        scopes=tuple(built),
        limitations=(
            "Rates are SURFACE volumes summed per compartment; this is not a reservoir "
            "voidage replacement ratio, because per-compartment reservoir voidage is not "
            "reported by the model. Treat the ratio as a comparative surface proxy only."
        ),
        units={
            "injection_rate_sm3_per_day": "sm3/day",
            "withdrawal_rate_sm3_per_day": "sm3/day",
            "oil_rate_sm3_per_day": "sm3/day",
            "surface_support_ratio": "dimensionless (surface injection / surface withdrawal)",
        },
    )


def _classify(injectors):
    """Сравнительный дисбаланс ВНУТРИ отсека, без абсолютных порогов.

    Донор и получатель -- понятия относительные: вопрос не «много ли качает
    скважина вообще», а «качает ли она заметно больше соседей по тому же
    отсеку». Поэтому сравнение идёт со средним по отсеку, а не с придуманной
    константой: подобранный порог оправдывался бы только тем, что даёт нужный
    ответ на одной колоде.

    Нулевой запас по давлению исключает скважину из получателей всегда: она
    физически не может принять больше. Это факт состояния, а не предпочтение.
    """
    if len(injectors) < 2:
        return (), (), "SINGLE_INJECTOR"

    rates = {w.well_id: (w.wwir or 0.0) for w in injectors}
    total = sum(rates.values())
    if total <= 0.0:
        return (), (), "NO_INJECTION"
    mean_rate = total / len(rates)
    if max(rates.values()) <= min(rates.values()):
        return (), (), "EVEN"

    donor = tuple(sorted((w for w, r in rates.items() if r > mean_rate), key=natural_key))
    recipient = tuple(
        sorted(
            (
                w.well_id
                for w in injectors
                if rates[w.well_id] < mean_rate
                and w.injector_bhp_margin is not None
                and w.injector_bhp_margin > NO_HEADROOM_BAR
            ),
            key=natural_key,
        )
    )
    if donor and recipient:
        balance = "UNEVEN_WITH_HEADROOM"
    elif donor:
        balance = "UNEVEN_NO_HEADROOM"
    else:
        balance = "EVEN"
    return donor, recipient, balance


def _reason(donor, recipient) -> str:
    if not donor and not recipient:
        return "no material support asymmetry is observable in this compartment"
    parts = []
    if donor:
        parts.append("realized injection above the compartment mean")
    if recipient:
        parts.append("realized injection below the compartment mean while holding BHP headroom")
    return "; ".join(parts)


__all__ = [
    "CONNECTIVITY_VERSION",
    "IMBALANCE_VERSION",
    "SupportConnectivity",
    "SupportImbalance",
    "SupportLink",
    "SupportScope",
    "derive_support_connectivity",
    "derive_support_imbalance",
]
