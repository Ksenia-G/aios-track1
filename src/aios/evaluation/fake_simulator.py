"""Детерминированный синтетический симулятор для разработки и тестов.

ВАЖНО: генерируемые байты -- НЕ схема траектории. Имена каналов намеренно
бессмысленные (`synthetic_channel_*`), чтобы никто не принял их за формат
официального симулятора и не начал писать под них разбор.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from aios.evaluation.artifacts import TrajectoryArtifactRef
from aios.evaluation.canonical import JsonValue, digest_bytes
from aios.evaluation.simulation import (
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    SimulatorIdentity,
    compute_simulation_key,
)

FAKE_SIMULATOR_ID = "fake-simulator/NOT_OFFICIAL"
FAKE_SIMULATOR_VERSION = "0.1.0"
FAKE_ARTIFACT_FORMAT = "aios.synthetic.trajectory"
FAKE_ARTIFACT_FILENAME = "trajectory.json"
FAKE_MEDIA_TYPE = "application/json"


def _derive_values(seed_material: str, count: int) -> list[float]:
    """Детерминированный поток чисел из SHA-256.

    Не используем random/время/hostname: результат обязан совпадать между
    процессами, машинами и версиями Python.
    """
    values: list[float] = []
    for index in range(count):
        block = hashlib.sha256(f"{seed_material}:{index}".encode()).digest()
        raw = int.from_bytes(block[:8], "big")
        # Три знака после запятой -- точное двоичное представление не требуется,
        # но строковая форма стабильна.
        values.append(round((raw % 1_000_000) / 1000.0, 3))
    return values


@dataclass(frozen=True, slots=True)
class FakeSimulator:
    """Симулятор, чей вывод полностью определён запросом и идентичностью."""

    step_count: int = 8
    simulator_version: str = FAKE_SIMULATOR_VERSION
    configuration: Mapping[str, JsonValue] = field(default_factory=dict)

    @property
    def identity(self) -> SimulatorIdentity:
        configuration: dict[str, JsonValue] = {"step_count": self.step_count}
        configuration.update(dict(self.configuration))
        return SimulatorIdentity(
            simulator_id=FAKE_SIMULATOR_ID,
            simulator_version=self.simulator_version,
            configuration=configuration,
        )

    def run(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        seed_material = compute_simulation_key(request, self.identity)
        channel_a = _derive_values(f"{seed_material}:a", self.step_count)
        channel_b = _derive_values(f"{seed_material}:b", self.step_count)
        document: dict[str, JsonValue] = {
            "format": FAKE_ARTIFACT_FORMAT,
            "format_version": 1,
            "not_official_schema": True,
            "steps": [
                {
                    "step_index": index,
                    "synthetic_channel_a": channel_a[index],
                    "synthetic_channel_b": channel_b[index],
                }
                for index in range(self.step_count)
            ],
        }
        data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / FAKE_ARTIFACT_FILENAME).write_bytes(data)

        trajectory_ref = TrajectoryArtifactRef(
            relative_path=FAKE_ARTIFACT_FILENAME,
            content_digest=digest_bytes(data),
            media_type=FAKE_MEDIA_TYPE,
            byte_size=len(data),
            producer_id=f"{FAKE_SIMULATOR_ID}@{self.simulator_version}",
        )
        return SimulationResult.create(
            status=SimulationStatus.SUCCESS,
            trajectory_ref=trajectory_ref,
            metadata={"step_count": self.step_count, "synthetic": True},
        )
