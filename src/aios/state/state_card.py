"""StateCard v1.0 immutable contract.

The StateCard is the only structured state object future MAS agents may read.
Raw simulator files remain owned by the physical simulation layer.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aios.evaluation.canonical import JsonValue, digest, normalize_json
from aios.state.schemas import (
    REALIZED_VECTOR_FIELDS,
    STATE_CARD_SCHEMA_VERSION,
    StateCardError,
    number_mapping,
    optional_number,
    require_key,
    require_mapping,
    required_date,
    required_text,
    text_mapping,
    text_tuple,
)


class WellRole(StrEnum):
    """Well role exposed to agents."""

    PRODUCER = "producer"
    INJECTOR = "injector"


@dataclass(frozen=True, slots=True)
class StateMetadata:
    """Reproducibility metadata for one extracted state."""

    date: datetime.date
    case_id: str
    run_id: str
    simulator_digest: str
    schedule_hash: str
    source_summary_identity: str
    extractor_version: str

    def __post_init__(self) -> None:
        required_text(self.case_id, "metadata.case_id")
        required_text(self.run_id, "metadata.run_id")
        required_text(self.simulator_digest, "metadata.simulator_digest")
        required_text(self.schedule_hash, "metadata.schedule_hash")
        required_text(self.source_summary_identity, "metadata.source_summary_identity")
        required_text(self.extractor_version, "metadata.extractor_version")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "date": self.date.isoformat(),
            "case_id": self.case_id,
            "run_id": self.run_id,
            "simulator_digest": self.simulator_digest,
            "schedule_hash": self.schedule_hash,
            "source_summary_identity": self.source_summary_identity,
            "extractor_version": self.extractor_version,
        }

    @classmethod
    def from_json(cls, payload: Any) -> StateMetadata:
        data = require_mapping(payload, "metadata")
        return cls(
            date=required_date(require_key(data, "date", "metadata"), "metadata.date"),
            case_id=required_text(require_key(data, "case_id", "metadata"), "metadata.case_id"),
            run_id=required_text(require_key(data, "run_id", "metadata"), "metadata.run_id"),
            simulator_digest=required_text(
                require_key(data, "simulator_digest", "metadata"), "metadata.simulator_digest"
            ),
            schedule_hash=required_text(
                require_key(data, "schedule_hash", "metadata"), "metadata.schedule_hash"
            ),
            source_summary_identity=required_text(
                require_key(data, "source_summary_identity", "metadata"),
                "metadata.source_summary_identity",
            ),
            extractor_version=required_text(
                require_key(data, "extractor_version", "metadata"), "metadata.extractor_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class FieldStateSnapshot:
    """Field-level quantities available to MAS agents."""

    average_pressure: float | None
    pressure_trend: float | None
    oil_rate: float | None
    water_rate: float | None
    liquid_rate: float | None
    water_injection_rate: float | None
    voidage_production_rate: float | None
    voidage_production_total: float | None
    oil_total: float | None
    water_total: float | None
    water_injection_total: float | None
    produced_water_available: float | None
    compensation_value: float | None
    compensation_status: str

    def __post_init__(self) -> None:
        for name in (
            "average_pressure",
            "pressure_trend",
            "oil_rate",
            "water_rate",
            "liquid_rate",
            "water_injection_rate",
            "voidage_production_rate",
            "voidage_production_total",
            "oil_total",
            "water_total",
            "water_injection_total",
            "produced_water_available",
            "compensation_value",
        ):
            optional_number(getattr(self, name), f"field.{name}")
        required_text(self.compensation_status, "field.compensation.status")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "pressure": {
                "average_pressure": self.average_pressure,
                "pressure_trend": self.pressure_trend,
            },
            "production": {
                "oil_rate": self.oil_rate,
                "water_rate": self.water_rate,
                "liquid_rate": self.liquid_rate,
            },
            "injection": {"water_injection_rate": self.water_injection_rate},
            "voidage": {
                "FVPR": self.voidage_production_rate,
                "FVPT": self.voidage_production_total,
            },
            "cumulative": {
                "WOPT": self.oil_total,
                "WWPT": self.water_total,
                "WWIT": self.water_injection_total,
            },
            "water": {"produced_water_available": self.produced_water_available},
            "compensation": {
                "value": self.compensation_value,
                "status": self.compensation_status,
            },
        }

    @classmethod
    def from_json(cls, payload: Any) -> FieldStateSnapshot:
        data = require_mapping(payload, "field")
        pressure = require_mapping(require_key(data, "pressure", "field"), "field.pressure")
        production = require_mapping(require_key(data, "production", "field"), "field.production")
        injection = require_mapping(require_key(data, "injection", "field"), "field.injection")
        voidage = require_mapping(require_key(data, "voidage", "field"), "field.voidage")
        cumulative = require_mapping(require_key(data, "cumulative", "field"), "field.cumulative")
        water = require_mapping(require_key(data, "water", "field"), "field.water")
        compensation = require_mapping(
            require_key(data, "compensation", "field"), "field.compensation"
        )
        return cls(
            average_pressure=optional_number(
                require_key(pressure, "average_pressure", "field.pressure"),
                "field.pressure.average_pressure",
            ),
            pressure_trend=optional_number(
                require_key(pressure, "pressure_trend", "field.pressure"),
                "field.pressure.pressure_trend",
            ),
            oil_rate=optional_number(
                require_key(production, "oil_rate", "field.production"),
                "field.production.oil_rate",
            ),
            water_rate=optional_number(
                require_key(production, "water_rate", "field.production"),
                "field.production.water_rate",
            ),
            liquid_rate=optional_number(
                require_key(production, "liquid_rate", "field.production"),
                "field.production.liquid_rate",
            ),
            water_injection_rate=optional_number(
                require_key(injection, "water_injection_rate", "field.injection"),
                "field.injection.water_injection_rate",
            ),
            voidage_production_rate=optional_number(
                require_key(voidage, "FVPR", "field.voidage"),
                "field.voidage.FVPR",
            ),
            voidage_production_total=optional_number(
                require_key(voidage, "FVPT", "field.voidage"),
                "field.voidage.FVPT",
            ),
            oil_total=optional_number(
                require_key(cumulative, "WOPT", "field.cumulative"),
                "field.cumulative.WOPT",
            ),
            water_total=optional_number(
                require_key(cumulative, "WWPT", "field.cumulative"),
                "field.cumulative.WWPT",
            ),
            water_injection_total=optional_number(
                require_key(cumulative, "WWIT", "field.cumulative"),
                "field.cumulative.WWIT",
            ),
            produced_water_available=optional_number(
                require_key(water, "produced_water_available", "field.water"),
                "field.water.produced_water_available",
            ),
            compensation_value=optional_number(
                require_key(compensation, "value", "field.compensation"),
                "field.compensation.value",
            ),
            compensation_status=required_text(
                require_key(compensation, "status", "field.compensation"),
                "field.compensation.status",
            ),
        )


@dataclass(frozen=True, slots=True)
class WellControlSnapshot:
    """Current command/control context supplied by the schedule layer."""

    role: WellRole
    status: str
    current_target: float | None
    control_mode: str

    def __post_init__(self) -> None:
        WellRole(self.role)
        required_text(self.status, "well.status")
        optional_number(self.current_target, "well.controls.current_target")
        required_text(self.control_mode, "well.controls.control_mode")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "role": str(self.role),
            "status": self.status,
            "current_target": self.current_target,
            "control_mode": self.control_mode,
        }

    @classmethod
    def from_json(cls, payload: Any, where: str) -> WellControlSnapshot:
        data = require_mapping(payload, where)
        try:
            role = WellRole(required_text(require_key(data, "role", where), f"{where}.role"))
        except ValueError as exc:
            raise StateCardError(f"{where}.role: unsupported role") from exc
        return cls(
            role=role,
            status=required_text(require_key(data, "status", where), f"{where}.status"),
            current_target=optional_number(
                require_key(data, "current_target", where), f"{where}.current_target"
            ),
            control_mode=required_text(
                require_key(data, "control_mode", where), f"{where}.control_mode"
            ),
        )


@dataclass(frozen=True, slots=True)
class WellStateSnapshot:
    """One well state exposed through StateCard."""

    well_id: str
    role: WellRole
    status: str
    current_target: float | None
    control_mode: str
    bhp: float | None
    wlpr: float | None
    wopr: float | None
    wwpr: float | None
    wwir: float | None
    wct: float | None
    wopt: float | None
    wwpt: float | None
    wwit: float | None
    producer_bhp_margin: float | None
    injector_bhp_margin: float | None
    wlpr_margin: float | None
    #: Давление призабойного блока до действия. Отдельно от WBHP: разница между
    #: ними и есть депрессия, которую скважина СЕЙЧАС отрабатывает.
    block_pressure: float | None = None
    #: Отработанная депрессия = block_pressure - BHP. Пре-акционная величина.
    drawdown_used: float | None = None
    #: Во сколько раз оставшийся до предела запас депрессии больше уже
    #: отработанной. None, когда отработанная депрессия неположительна или
    #: неизвестна: делить на ноль нельзя, а подставить туда число значило бы
    #: выдумать наблюдение. Ноль означает "запаса нет", а не "нет данных".
    drawdown_spare_ratio: float | None = None

    def __post_init__(self) -> None:
        required_text(self.well_id, "well.well_id")
        WellRole(self.role)
        required_text(self.status, "well.status")
        required_text(self.control_mode, "well.controls.control_mode")
        for name in (
            "current_target",
            "bhp",
            "wlpr",
            "wopr",
            "wwpr",
            "wwir",
            "wct",
            "wopt",
            "wwpt",
            "wwit",
            "producer_bhp_margin",
            "injector_bhp_margin",
            "wlpr_margin",
            "block_pressure",
            "drawdown_used",
            "drawdown_spare_ratio",
        ):
            optional_number(getattr(self, name), f"well.{name}")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "well_id": self.well_id,
            "role": str(self.role),
            "status": self.status,
            "controls": {
                "current_target": self.current_target,
                "control_mode": self.control_mode,
            },
            "realized": {
                "BHP": self.bhp,
                "WLPR": self.wlpr,
                "WOPR": self.wopr,
                "WWPR": self.wwpr,
                "WWIR": self.wwir,
                "WWCT": self.wct,
                "WOPT": self.wopt,
                "WWPT": self.wwpt,
                "WWIT": self.wwit,
                "WBP9": self.block_pressure,
            },
            "derived": {
                "producer_bhp_margin": self.producer_bhp_margin,
                "injector_bhp_margin": self.injector_bhp_margin,
                "wlpr_margin": self.wlpr_margin,
                "drawdown_used": self.drawdown_used,
                "drawdown_spare_ratio": self.drawdown_spare_ratio,
            },
        }

    @classmethod
    def from_json(cls, payload: Any, where: str) -> WellStateSnapshot:
        data = require_mapping(payload, where)
        controls = require_mapping(require_key(data, "controls", where), f"{where}.controls")
        realized = require_mapping(require_key(data, "realized", where), f"{where}.realized")
        derived = require_mapping(require_key(data, "derived", where), f"{where}.derived")
        for key in REALIZED_VECTOR_FIELDS:
            require_key(realized, key, f"{where}.realized")
        try:
            role = WellRole(required_text(require_key(data, "role", where), f"{where}.role"))
        except ValueError as exc:
            raise StateCardError(f"{where}.role: unsupported role") from exc
        return cls(
            well_id=required_text(require_key(data, "well_id", where), f"{where}.well_id"),
            role=role,
            status=required_text(require_key(data, "status", where), f"{where}.status"),
            current_target=optional_number(
                require_key(controls, "current_target", f"{where}.controls"),
                f"{where}.controls.current_target",
            ),
            control_mode=required_text(
                require_key(controls, "control_mode", f"{where}.controls"),
                f"{where}.controls.control_mode",
            ),
            bhp=optional_number(realized["BHP"], f"{where}.realized.BHP"),
            wlpr=optional_number(realized["WLPR"], f"{where}.realized.WLPR"),
            wopr=optional_number(realized["WOPR"], f"{where}.realized.WOPR"),
            wwpr=optional_number(realized["WWPR"], f"{where}.realized.WWPR"),
            wwir=optional_number(realized["WWIR"], f"{where}.realized.WWIR"),
            wct=optional_number(realized["WWCT"], f"{where}.realized.WWCT"),
            wopt=optional_number(realized["WOPT"], f"{where}.realized.WOPT"),
            wwpt=optional_number(realized["WWPT"], f"{where}.realized.WWPT"),
            wwit=optional_number(realized["WWIT"], f"{where}.realized.WWIT"),
            block_pressure=optional_number(realized.get("WBP9"), f"{where}.realized.WBP9"),
            drawdown_used=optional_number(
                derived.get("drawdown_used"), f"{where}.derived.drawdown_used"
            ),
            drawdown_spare_ratio=optional_number(
                derived.get("drawdown_spare_ratio"), f"{where}.derived.drawdown_spare_ratio"
            ),
            producer_bhp_margin=optional_number(
                require_key(derived, "producer_bhp_margin", f"{where}.derived"),
                f"{where}.derived.producer_bhp_margin",
            ),
            injector_bhp_margin=optional_number(
                require_key(derived, "injector_bhp_margin", f"{where}.derived"),
                f"{where}.derived.injector_bhp_margin",
            ),
            wlpr_margin=optional_number(
                require_key(derived, "wlpr_margin", f"{where}.derived"),
                f"{where}.derived.wlpr_margin",
            ),
        )


@dataclass(frozen=True, slots=True)
class ConstraintState:
    """Official physical constraints and internal controller/search constraints."""

    official: Mapping[str, float | None]
    search: Mapping[str, JsonValue] = field(default_factory=dict)
    violations: tuple[str, ...] = ()
    binding_constraints: tuple[str, ...] = ()
    margins: Mapping[str, float | None] = field(default_factory=dict)
    #: Как симулятор обходится с командой, превышающей то, что скважина у
    #: своего предела способна отдать. Формулировка нейтральная: она описывает
    #: механику, а не одобряет какое-либо направление.
    semantics: tuple[str, ...] = ()

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "official": normalize_json(dict(self.official)),
            "search": normalize_json(dict(self.search)),
            "violations": list(self.violations),
            "binding_constraints": list(self.binding_constraints),
            "margins": normalize_json(dict(self.margins)),
            "semantics": list(self.semantics),
        }

    @classmethod
    def from_json(cls, payload: Any) -> ConstraintState:
        data = require_mapping(payload, "constraints")
        return cls(
            official=number_mapping(
                require_key(data, "official", "constraints"), "constraints.official"
            ),
            search=dict(
                require_mapping(require_key(data, "search", "constraints"), "constraints.search")
            ),
            violations=text_tuple(
                require_key(data, "violations", "constraints"), "constraints.violations"
            ),
            binding_constraints=text_tuple(
                require_key(data, "binding_constraints", "constraints"),
                "constraints.binding_constraints",
            ),
            margins=number_mapping(
                require_key(data, "margins", "constraints"), "constraints.margins"
            ),
            semantics=text_tuple(data.get("semantics", []), "constraints.semantics"),
        )


@dataclass(frozen=True, slots=True)
class GeologyContext:
    """Static geological context available to agents."""

    block_membership: Mapping[str, str] = field(default_factory=dict)
    fault_relationship: Mapping[str, str] = field(default_factory=dict)
    connectivity_confidence: Mapping[str, float | None] = field(default_factory=dict)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "block_membership": dict(self.block_membership),
            "fault_relationship": dict(self.fault_relationship),
            "connectivity_confidence": normalize_json(dict(self.connectivity_confidence)),
        }

    @classmethod
    def from_json(cls, payload: Any) -> GeologyContext:
        data = require_mapping(payload, "geology")
        return cls(
            block_membership=text_mapping(
                require_key(data, "block_membership", "geology"), "geology.block_membership"
            ),
            fault_relationship=text_mapping(
                require_key(data, "fault_relationship", "geology"), "geology.fault_relationship"
            ),
            connectivity_confidence=number_mapping(
                require_key(data, "connectivity_confidence", "geology"),
                "geology.connectivity_confidence",
            ),
        )


@dataclass(frozen=True, slots=True)
class UncertaintyState:
    """Explicit unresolved or unavailable state information."""

    items: tuple[str, ...] = ()

    def to_json(self) -> dict[str, JsonValue]:
        return {"items": list(self.items)}

    @classmethod
    def from_json(cls, payload: Any) -> UncertaintyState:
        data = require_mapping(payload, "uncertainty")
        return cls(items=text_tuple(require_key(data, "items", "uncertainty"), "uncertainty.items"))


@dataclass(frozen=True, slots=True)
class StateCard:
    """Stable MAS-facing state object."""

    metadata: StateMetadata
    field: FieldStateSnapshot
    wells: tuple[WellStateSnapshot, ...]
    constraints: ConstraintState
    geology: GeologyContext = field(default_factory=GeologyContext)
    uncertainty: UncertaintyState = field(default_factory=UncertaintyState)
    schema_version: str = STATE_CARD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != STATE_CARD_SCHEMA_VERSION:
            raise StateCardError(f"unsupported StateCard schema version {self.schema_version!r}")
        seen: set[str] = set()
        for well in self.wells:
            if well.well_id in seen:
                raise StateCardError(f"duplicate well in StateCard: {well.well_id!r}")
            seen.add(well.well_id)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "metadata": self.metadata.to_json(),
            "field": self.field.to_json(),
            "wells": [well.to_json() for well in self.wells],
            "constraints": self.constraints.to_json(),
            "geology": self.geology.to_json(),
            "uncertainty": self.uncertainty.to_json(),
        }

    @classmethod
    def from_json(cls, payload: Any) -> StateCard:
        data = require_mapping(payload, "state_card")
        version = required_text(
            require_key(data, "schema_version", "state_card"), "state_card.schema_version"
        )
        wells_raw = require_key(data, "wells", "state_card")
        if not isinstance(wells_raw, list):
            raise StateCardError("state_card.wells: expected list")
        return cls(
            schema_version=version,
            metadata=StateMetadata.from_json(require_key(data, "metadata", "state_card")),
            field=FieldStateSnapshot.from_json(require_key(data, "field", "state_card")),
            wells=tuple(
                WellStateSnapshot.from_json(item, f"wells[{index}]")
                for index, item in enumerate(wells_raw)
            ),
            constraints=ConstraintState.from_json(require_key(data, "constraints", "state_card")),
            geology=GeologyContext.from_json(require_key(data, "geology", "state_card")),
            uncertainty=UncertaintyState.from_json(require_key(data, "uncertainty", "state_card")),
        )

    def digest(self) -> str:
        """Deterministic SHA-256 over the canonical StateCard JSON payload."""

        return digest(self.to_json())
