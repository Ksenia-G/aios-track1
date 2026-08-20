"""Восстановление состояния поля на начало окна управления.

Состояние не угадывается по одному замеру: история проигрывается блок за
блоком ровно в том порядке, в каком её читает симулятор, и запоминается
последнее управление каждой скважины.
"""

from dataclasses import dataclass
from datetime import date

from aios.schedule.deck import (
    DeckParseError,
    DeckSchedule,
    parse_injector_record,
    parse_producer_record,
)
from aios.schedule.events import (
    InjectorControl,
    ProducerControl,
    WellRole,
    WellStatus,
    natural_key,
)


@dataclass(frozen=True, slots=True)
class WellState:
    """Последнее известное управление скважины на момент среза."""

    well: str
    role: WellRole
    status: WellStatus
    mode: str
    target: float | None
    bhp_limit: float | None
    phase: str | None = None
    since: date | None = None

    def as_producer(self) -> ProducerControl:
        if self.role is not WellRole.PRODUCER:
            raise ValueError(f"well {self.well} is not a producer")
        return ProducerControl(
            well=self.well,
            status=self.status,
            mode=self.mode,
            liquid_rate=self.target,
            bhp_limit=self.bhp_limit,
        )

    def as_injector(self) -> InjectorControl:
        if self.role is not WellRole.INJECTOR:
            raise ValueError(f"well {self.well} is not an injector")
        return InjectorControl(
            well=self.well,
            phase=self.phase or "WATER",
            status=self.status,
            mode=self.mode,
            surface_rate=self.target,
            bhp_limit=self.bhp_limit,
        )


@dataclass(frozen=True, slots=True)
class FieldState:
    """Срез поля: известные скважины и управление каждой из них."""

    as_of: date
    known_wells: frozenset[str]
    wells: tuple[WellState, ...]

    @property
    def by_well(self) -> dict[str, WellState]:
        return {state.well: state for state in self.wells}

    @property
    def producers(self) -> tuple[WellState, ...]:
        return tuple(s for s in self.wells if s.role is WellRole.PRODUCER)

    @property
    def injectors(self) -> tuple[WellState, ...]:
        return tuple(s for s in self.wells if s.role is WellRole.INJECTOR)

    def role_of(self, well: str) -> WellRole | None:
        state = self.by_well.get(well)
        return None if state is None else state.role


def reconstruct_state(deck: DeckSchedule, at_date: date) -> FieldState:
    """Проиграть историю до `at_date` (не включая) и вернуть срез."""

    known: set[str] = set()
    states: dict[str, WellState] = {}
    for block, block_date in zip(deck.blocks, deck.block_dates, strict=True):
        if block_date >= at_date:
            break
        if block.keyword in ("WELSPECS", "COMPDAT"):
            for record in block.records:
                name = record.text(0)
                if name:
                    known.add(name)
        elif block.keyword == "WCONPROD":
            for record in block.records:
                control = parse_producer_record(record)
                known.add(control.well)
                states[control.well] = WellState(
                    well=control.well,
                    role=WellRole.PRODUCER,
                    status=control.status,
                    mode=control.mode,
                    target=control.liquid_rate,
                    bhp_limit=control.bhp_limit,
                    since=block_date,
                )
        elif block.keyword == "WCONINJE":
            for record in block.records:
                control = parse_injector_record(record)
                known.add(control.well)
                states[control.well] = WellState(
                    well=control.well,
                    role=WellRole.INJECTOR,
                    status=control.status,
                    mode=control.mode,
                    target=control.surface_rate,
                    bhp_limit=control.bhp_limit,
                    phase=control.phase,
                    since=block_date,
                )
        elif block.keyword == "WCONHIST":
            for record in block.records:
                name = record.text(0)
                mode = record.text(2)
                if not name or not mode:
                    raise DeckParseError(f"line {record.line + 1}: incomplete WCONHIST record")
                known.add(name)
                states[name] = WellState(
                    well=name,
                    role=WellRole.PRODUCER,
                    status=WellStatus(str(record.text(1) or "OPEN").upper()),
                    mode=mode.upper(),
                    target=record.number(3),
                    bhp_limit=record.number(9),
                    since=block_date,
                )
        elif block.keyword == "WELTARG":
            for record in block.records:
                name = record.text(0)
                key = (record.text(1) or "").upper()
                value = record.number(2)
                current = states.get(name or "")
                if current is None or name is None:
                    raise DeckParseError(
                        f"line {record.line + 1}: WELTARG for {name!r} has no prior well state"
                    )
                producer_targets = {"LRAT"}
                injector_targets = {"RATE"}
                if key == "BHP":
                    states[name] = WellState(
                        well=current.well,
                        role=current.role,
                        status=current.status,
                        mode=current.mode,
                        target=current.target,
                        bhp_limit=value,
                        phase=current.phase,
                        since=block_date,
                    )
                elif (
                    current.role is WellRole.PRODUCER
                    and key in producer_targets
                    or current.role is WellRole.INJECTOR
                    and key in injector_targets
                ):
                    states[name] = WellState(
                        well=current.well,
                        role=current.role,
                        status=current.status,
                        mode=key,
                        target=value,
                        bhp_limit=current.bhp_limit,
                        phase=current.phase,
                        since=block_date,
                    )
                else:
                    raise DeckParseError(
                        f"line {record.line + 1}: unsupported WELTARG target {key!r} "
                        f"for {current.role} well {name!r}"
                    )

    ordered = tuple(states[name] for name in sorted(states, key=natural_key))
    return FieldState(as_of=at_date, known_wells=frozenset(known | set(deck.wells)), wells=ordered)
