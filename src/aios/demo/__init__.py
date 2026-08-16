"""Демонстрационный слой КТ1: чтение проверенных свидетельств и их показ."""

from aios.demo.snapshot import (
    PRESSURE_FLOOR_STATUS,
    PROVENANCE,
    SOURCE_ARTIFACTS,
    WATER_BALANCE_STATUS,
    CandidateEntry,
    DemoSnapshot,
    EvidenceError,
    StageEntry,
    build_snapshot,
    repository_root,
    serialize,
    snapshot_path,
    write_snapshot,
)

__all__ = [
    "PRESSURE_FLOOR_STATUS",
    "PROVENANCE",
    "SOURCE_ARTIFACTS",
    "WATER_BALANCE_STATUS",
    "CandidateEntry",
    "DemoSnapshot",
    "EvidenceError",
    "StageEntry",
    "build_snapshot",
    "repository_root",
    "serialize",
    "snapshot_path",
    "write_snapshot",
]
