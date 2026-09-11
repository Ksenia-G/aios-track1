"""Забойное давление судится только у работающей скважины.

Признак берётся не из метаданных ремонта, а из того, что сообщил симулятор.
На реальном прогоне закрытая скважина отдаёт `WBHP = 0.000` при `WLPR = -0.000`,
а работающая на пределе -- `WBHP = 50.000` при `WLPR = 32.980`. Значит
авторитетный признак активности -- реализованный дебит жидкости, и он закрывает
не только плановый ремонт, но любую фактически закрытую скважину.

Эпсилон не вводится: симулятор пишет отрицательный ноль, а `-0.0 > 0` уже
ложно.
"""

from __future__ import annotations

from aios.newcase.activity import is_flowing_producer, judgeable_producers


def test_a_flowing_producer_is_judged() -> None:
    assert is_flowing_producer(liquid_rate=32.98)


def test_a_closed_producer_reporting_negative_zero_is_not_judged() -> None:
    """Ровно то, что пишет OPM для скважины в ремонте."""
    assert not is_flowing_producer(liquid_rate=-0.0)


def test_a_closed_producer_reporting_plain_zero_is_not_judged() -> None:
    assert not is_flowing_producer(liquid_rate=0.0)


def test_a_missing_rate_is_not_treated_as_flow() -> None:
    assert not is_flowing_producer(liquid_rate=None)


def test_a_producer_exactly_on_the_floor_but_flowing_is_still_judged() -> None:
    """50.000 бар при живом дебите -- это работа на пределе, а не отсутствие режима."""
    assert is_flowing_producer(liquid_rate=0.001)


def test_only_the_non_flowing_wells_are_dropped_from_judgement() -> None:
    rates = {"1": 32.98, "40": -0.0, "26": 0.0, "55": 12.5}

    assert judgeable_producers(rates) == ("1", "55")


def test_one_closed_well_never_silences_another_well() -> None:
    rates = {"40": -0.0, "1": 32.98}

    assert "1" in judgeable_producers(rates)


def test_a_returned_well_is_judged_again_once_it_flows() -> None:
    """Возврат из ремонта виден по дебиту, а не по календарю."""
    during = {"40": -0.0}
    after = {"40": 100.0}

    assert judgeable_producers(during) == ()
    assert judgeable_producers(after) == ("40",)
