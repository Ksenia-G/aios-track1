"""Хранилище артефактов симуляции и ссылки на них.

На этом этапе трека формат вывода симулятора неизвестен, поэтому артефакт --
непрозрачные байты. `TrajectoryArtifactRef` описывает только их адресацию и
целостность. Нормализованный `TrajectoryData` появится отдельной задачей,
после разбора реального вывода официального симулятора.
"""

import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol, runtime_checkable

from aios.evaluation.canonical import JsonValue, digest_bytes


class ArtifactStoreError(RuntimeError):
    """Базовая ошибка хранилища артефактов."""


class ArtifactMissingError(ArtifactStoreError):
    """Артефакт отсутствует по своему адресу."""


class ArtifactCorruptedError(ArtifactStoreError):
    """Содержимое артефакта не соответствует объявленному дайджесту."""


class ArtifactPathEscapeError(ArtifactStoreError):
    """Путь артефакта после разрешения ссылок выходит за пределы своего корня."""


@dataclass(frozen=True, slots=True)
class TrajectoryArtifactRef:
    """Дескриптор сохранённых байтов траектории.

    Путь всегда относительный: абсолютный путь, попавший в БД или кеш, ломает
    переносимость эксперимента между машинами и запусками.
    """

    relative_path: str
    content_digest: str
    media_type: str
    byte_size: int
    producer_id: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"relative_path must stay inside its root: {self.relative_path!r}")
        if self.byte_size < 0:
            raise ValueError("byte_size must be non-negative")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "relative_path": self.relative_path,
            "content_digest": self.content_digest,
            "media_type": self.media_type,
            "byte_size": self.byte_size,
            "producer_id": self.producer_id,
        }

    @classmethod
    def from_json(cls, payload: dict[str, JsonValue]) -> "TrajectoryArtifactRef":
        return cls(
            relative_path=str(payload["relative_path"]),
            content_digest=str(payload["content_digest"]),
            media_type=str(payload["media_type"]),
            byte_size=int(payload["byte_size"]),  # type: ignore[arg-type]
            producer_id=str(payload["producer_id"]),
        )


@runtime_checkable
class ArtifactReader(Protocol):
    """Минимальный доступ потребителя к уже принятому артефакту.

    Намеренно не раскрывает корень хранилища и не отдаёт путей: потребитель
    (эвалюатор NPV, POST-ограничение) не должен собирать абсолютные пути,
    join-ить relative_path вручную или обходить проверку целостности.
    Единственный способ добраться до байтов -- через этот протокол.
    """

    def open_binary(self, ref: TrajectoryArtifactRef) -> BinaryIO:
        """Открыть проверенный поток байтов артефакта."""
        ...

    def verify(self, ref: TrajectoryArtifactRef) -> bool:
        """Проверить целостность артефакта, не поднимая исключения."""
        ...


class ArtifactStore:
    """Content-addressed хранилище байтов.

    Путь артефакта выводится из дайджеста содержимого, поэтому одинаковые байты
    хранятся один раз, а подмена содержимого обнаруживается при чтении.
    Корень задаётся вызывающей стороной: дефолтного пути внутри репозитория нет.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _relative_path_for(self, content_digest: str) -> str:
        return str(PurePosixPath("blobs") / content_digest[:2] / content_digest[2:])

    def resolve(self, ref: TrajectoryArtifactRef) -> Path:
        return self._root / ref.relative_path

    def put_bytes(self, data: bytes, *, media_type: str, producer_id: str) -> TrajectoryArtifactRef:
        """Записать байты атомарно и вернуть дескриптор."""
        content_digest = digest_bytes(data)
        relative_path = self._relative_path_for(content_digest)
        target = self._root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        # Существующий блоб перезаписывается только если его содержимое
        # разошлось с адресом: иначе повреждённый файл остался бы навсегда,
        # и все последующие чтения по этому дайджесту падали бы.
        if not target.exists() or digest_bytes(target.read_bytes()) != content_digest:
            self._write_atomically(target, data)
        return TrajectoryArtifactRef(
            relative_path=relative_path,
            content_digest=content_digest,
            media_type=media_type,
            byte_size=len(data),
            producer_id=producer_id,
        )

    @staticmethod
    def _write_atomically(target: Path, data: bytes) -> None:
        # Временный файл в том же каталоге + os.replace: прерывание процесса
        # не должно оставить недописанный блоб по «правильному» адресу,
        # иначе он навсегда станет валидным с виду попаданием в кеш.
        handle, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, target)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _resolve_contained(root: Path, ref: TrajectoryArtifactRef) -> Path:
        """Разрешить путь артефакта и убедиться, что он лежит внутри корня.

        Лексической проверки `..` недостаточно: `workspace/link -> /вне` тоже
        уводит наружу. Поэтому корень и сам файл разрешаются физически, и
        сравниваются уже разрешённые пути. Сам артефакт symlink-ом быть не
        может -- так семантика остаётся однозначной.

        Полностью закрыть гонку TOCTOU здесь не пытаемся: адаптеры симуляторов
        локальные и доверенные, задача -- не пропустить файл вне каталога.
        """
        try:
            resolved_root = Path(root).resolve(strict=True)
        except OSError as error:
            raise ArtifactMissingError(f"artifact root not found: {root}") from error

        candidate = resolved_root / ref.relative_path
        if candidate.is_symlink():
            raise ArtifactPathEscapeError(
                f"artifact must be a regular file, not a symlink: {ref.relative_path}"
            )
        try:
            resolved_source = candidate.resolve(strict=True)
        except OSError as error:
            raise ArtifactMissingError(f"artifact not found: {ref.relative_path}") from error

        if not resolved_source.is_relative_to(resolved_root):
            raise ArtifactPathEscapeError(
                f"artifact resolves outside its root: {ref.relative_path}"
            )
        if not resolved_source.is_file():
            raise ArtifactMissingError(f"artifact is not a regular file: {ref.relative_path}")
        return resolved_source

    def ingest(self, source_root: Path, ref: TrajectoryArtifactRef) -> TrajectoryArtifactRef:
        """Перенести артефакт из рабочего каталога симулятора в хранилище.

        Симулятор пишет файл в свой workspace и описывает его относительным
        путём; движок переносит байты в content-addressed хранилище и получает
        новый дескриптор. Здесь же проверяется и принадлежность файла workspace,
        и объявленный симулятором дайджест -- дальше по конвейеру непроверенные
        байты не проходят.
        """
        source = self._resolve_contained(Path(source_root), ref)
        data = source.read_bytes()
        actual_digest = digest_bytes(data)
        if actual_digest != ref.content_digest or len(data) != ref.byte_size:
            raise ArtifactCorruptedError(
                f"artifact digest mismatch for {ref.relative_path}: "
                f"declared {ref.content_digest}, actual {actual_digest}"
            )
        return self.put_bytes(data, media_type=ref.media_type, producer_id=ref.producer_id)

    def read(self, ref: TrajectoryArtifactRef) -> bytes:
        """Прочитать байты с обязательной проверкой пути и дайджеста."""
        path = self._resolve_contained(self._root, ref)
        data = path.read_bytes()
        actual_digest = digest_bytes(data)
        if actual_digest != ref.content_digest:
            raise ArtifactCorruptedError(
                f"artifact digest mismatch for {ref.relative_path}: "
                f"expected {ref.content_digest}, actual {actual_digest}"
            )
        return data

    def open_binary(self, ref: TrajectoryArtifactRef) -> BinaryIO:
        """Отдать потребителю проверенный поток байтов, но не путь к файлу."""
        return io.BytesIO(self.read(ref))

    def verify(self, ref: TrajectoryArtifactRef) -> bool:
        """Проверить целостность без исключения. Используется поиском в кеше."""
        try:
            self.read(ref)
        except ArtifactStoreError:
            return False
        return True
