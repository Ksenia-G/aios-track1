"""Тесты content-addressed хранилища артефактов."""

from pathlib import Path

import pytest

from aios.evaluation.artifacts import (
    ArtifactCorruptedError,
    ArtifactMissingError,
    ArtifactPathEscapeError,
    ArtifactReader,
    ArtifactStore,
    TrajectoryArtifactRef,
)
from aios.evaluation.canonical import digest_bytes

PAYLOAD = b"synthetic artifact bytes \x00\x01\x02"


def test_roundtrip_returns_identical_bytes(artifact_store: ArtifactStore) -> None:
    ref = artifact_store.put_bytes(PAYLOAD, media_type="application/octet-stream", producer_id="t")
    assert artifact_store.read(ref) == PAYLOAD
    assert ref.content_digest == digest_bytes(PAYLOAD)
    assert ref.byte_size == len(PAYLOAD)


def test_descriptor_path_is_relative(artifact_store: ArtifactStore) -> None:
    ref = artifact_store.put_bytes(PAYLOAD, media_type="application/octet-stream", producer_id="t")
    assert not Path(ref.relative_path).is_absolute()
    assert artifact_store.resolve(ref).is_file()


def test_identical_bytes_are_stored_once(artifact_store: ArtifactStore) -> None:
    first = artifact_store.put_bytes(PAYLOAD, media_type="application/json", producer_id="t")
    second = artifact_store.put_bytes(PAYLOAD, media_type="application/json", producer_id="t")
    assert first.relative_path == second.relative_path
    blobs = list((artifact_store.root / "blobs").rglob("*"))
    assert len([path for path in blobs if path.is_file()]) == 1


def test_no_temporary_files_are_left_behind(artifact_store: ArtifactStore) -> None:
    artifact_store.put_bytes(PAYLOAD, media_type="application/json", producer_id="t")
    leftovers = [path.name for path in artifact_store.root.rglob(".tmp-*")]
    assert leftovers == []


def test_corrupted_blob_is_rejected_on_read(artifact_store: ArtifactStore) -> None:
    ref = artifact_store.put_bytes(PAYLOAD, media_type="application/json", producer_id="t")
    artifact_store.resolve(ref).write_bytes(b"tampered")
    with pytest.raises(ArtifactCorruptedError):
        artifact_store.read(ref)


def test_verify_reports_corruption_without_raising(artifact_store: ArtifactStore) -> None:
    ref = artifact_store.put_bytes(PAYLOAD, media_type="application/json", producer_id="t")
    assert artifact_store.verify(ref) is True
    artifact_store.resolve(ref).write_bytes(b"tampered")
    assert artifact_store.verify(ref) is False


def test_missing_blob_raises(artifact_store: ArtifactStore) -> None:
    ref = artifact_store.put_bytes(PAYLOAD, media_type="application/json", producer_id="t")
    artifact_store.resolve(ref).unlink()
    with pytest.raises(ArtifactMissingError):
        artifact_store.read(ref)
    assert artifact_store.verify(ref) is False


def test_ingest_moves_bytes_and_verifies_digest(
    artifact_store: ArtifactStore, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "trajectory.bin").write_bytes(PAYLOAD)
    declared = TrajectoryArtifactRef(
        relative_path="trajectory.bin",
        content_digest=digest_bytes(PAYLOAD),
        media_type="application/octet-stream",
        byte_size=len(PAYLOAD),
        producer_id="test-simulator",
    )
    stored = artifact_store.ingest(workspace, declared)
    assert stored.content_digest == declared.content_digest
    assert stored.relative_path != declared.relative_path
    assert artifact_store.read(stored) == PAYLOAD


def test_ingest_rejects_wrong_declared_digest(
    artifact_store: ArtifactStore, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "trajectory.bin").write_bytes(PAYLOAD)
    declared = TrajectoryArtifactRef(
        relative_path="trajectory.bin",
        content_digest="0" * 64,
        media_type="application/octet-stream",
        byte_size=len(PAYLOAD),
        producer_id="test-simulator",
    )
    with pytest.raises(ArtifactCorruptedError):
        artifact_store.ingest(workspace, declared)


def test_ingest_rejects_missing_source(artifact_store: ArtifactStore, tmp_path: Path) -> None:
    declared = TrajectoryArtifactRef(
        relative_path="absent.bin",
        content_digest=digest_bytes(PAYLOAD),
        media_type="application/octet-stream",
        byte_size=len(PAYLOAD),
        producer_id="test-simulator",
    )
    with pytest.raises(ArtifactMissingError):
        artifact_store.ingest(tmp_path, declared)


@pytest.mark.parametrize("relative_path", ["/absolute/path.bin", "../escape.bin", "a/../../x.bin"])
def test_descriptor_rejects_paths_outside_root(relative_path: str) -> None:
    with pytest.raises(ValueError):
        TrajectoryArtifactRef(
            relative_path=relative_path,
            content_digest="0" * 64,
            media_type="application/octet-stream",
            byte_size=1,
            producer_id="test",
        )


def _declared_ref(relative_path: str, data: bytes) -> TrajectoryArtifactRef:
    return TrajectoryArtifactRef(
        relative_path=relative_path,
        content_digest=digest_bytes(data),
        media_type="application/octet-stream",
        byte_size=len(data),
        producer_id="test-simulator",
    )


def test_ingest_rejects_symlink_pointing_outside_workspace(
    artifact_store: ArtifactStore, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.bin"
    secret.write_bytes(PAYLOAD)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "trajectory.bin").symlink_to(secret)

    with pytest.raises(ArtifactPathEscapeError):
        artifact_store.ingest(workspace, _declared_ref("trajectory.bin", PAYLOAD))


def test_ingest_rejects_nested_symlink_escaping_workspace(
    artifact_store: ArtifactStore, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.bin").write_bytes(PAYLOAD)

    workspace = tmp_path / "workspace"
    (workspace / "nested").mkdir(parents=True)
    # Ссылкой наружу является каталог, а не сам файл: лексической проверки
    # пути здесь недостаточно.
    (workspace / "nested" / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactPathEscapeError):
        artifact_store.ingest(workspace, _declared_ref("nested/link/secret.bin", PAYLOAD))


def test_ingest_rejects_symlink_even_inside_workspace(
    artifact_store: ArtifactStore, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "real.bin").write_bytes(PAYLOAD)
    (workspace / "trajectory.bin").symlink_to(workspace / "real.bin")

    with pytest.raises(ArtifactPathEscapeError):
        artifact_store.ingest(workspace, _declared_ref("trajectory.bin", PAYLOAD))


def test_ingest_accepts_nested_regular_file(artifact_store: ArtifactStore, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "nested" / "deeper").mkdir(parents=True)
    (workspace / "nested" / "deeper" / "trajectory.bin").write_bytes(PAYLOAD)

    stored = artifact_store.ingest(
        workspace, _declared_ref("nested/deeper/trajectory.bin", PAYLOAD)
    )
    assert artifact_store.read(stored) == PAYLOAD


def test_ingest_rejects_directory(artifact_store: ArtifactStore, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "trajectory.bin").mkdir(parents=True)

    with pytest.raises(ArtifactMissingError):
        artifact_store.ingest(workspace, _declared_ref("trajectory.bin", PAYLOAD))


def test_store_satisfies_artifact_reader_protocol(artifact_store: ArtifactStore) -> None:
    assert isinstance(artifact_store, ArtifactReader)


def test_open_binary_returns_verified_stream(artifact_store: ArtifactStore) -> None:
    ref = artifact_store.put_bytes(PAYLOAD, media_type="application/json", producer_id="t")
    with artifact_store.open_binary(ref) as stream:
        assert stream.read() == PAYLOAD


def test_open_binary_rejects_corrupted_artifact(artifact_store: ArtifactStore) -> None:
    ref = artifact_store.put_bytes(PAYLOAD, media_type="application/json", producer_id="t")
    artifact_store.resolve(ref).write_bytes(b"tampered")
    with pytest.raises(ArtifactCorruptedError):
        artifact_store.open_binary(ref)


def test_descriptor_json_roundtrip() -> None:
    ref = TrajectoryArtifactRef(
        relative_path="blobs/ab/cd",
        content_digest="ab" * 32,
        media_type="application/json",
        byte_size=12,
        producer_id="test",
    )
    assert TrajectoryArtifactRef.from_json(ref.to_json()) == ref
