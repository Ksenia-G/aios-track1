"""Протокол симулятора.

Симулятор -- намеренно «глупый» адаптер. Он ничего не знает про NPV, кеш,
базу экспериментов, оптимизатор и отправку решения: любое такое знание
привяжет нас к конкретному движку и сделает замену симулятора переписыванием
половины системы.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from aios.evaluation.simulation import SimulationRequest, SimulationResult, SimulatorIdentity


class SimulatorError(RuntimeError):
    """Отказ адаптера симулятора.

    Исключение допустимо, но движок всё равно запишет отказ в историю:
    потерянный отказ выглядит как непроведённый эксперимент.
    """


@runtime_checkable
class Simulator(Protocol):
    """Контракт адаптера симулятора."""

    @property
    def identity(self) -> SimulatorIdentity: ...

    def run(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        """Выполнить прогон.

        `workspace` -- изолированный каталог, который выделяет движок. Артефакты
        пишутся туда, а в дескрипторе указывается путь относительно workspace.
        """
        ...
