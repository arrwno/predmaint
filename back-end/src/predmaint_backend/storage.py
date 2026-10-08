import errno
import hashlib
import json
import os
import sqlite3
import stat
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from .audio import validate_wav_stream
from .models import (
    MAX_AUDIO_BYTES,
    NO_MODEL_REASON,
    APIError,
    Recording,
    RecordingPage,
    RecordingRegistration,
)


class Storage:
    def __init__(self, data_dir: Path):
        self.data_dir = self._canonical_data_directory(data_dir)
        self._private_directory(self.data_dir)
        self.audio_dir = self.data_dir / "audio"
        self._private_directory(self.audio_dir)
        self.database = self.data_dir / "metadata.sqlite3"
        descriptor = os.open(self.database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        self.database.chmod(0o600)
        with self._connection() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    UNIQUE (tenant_id, subject_id, client_id)
                )
            """)

    @staticmethod
    def _private_directory(path: Path) -> None:
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in path.parts[1:]:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    pass
                try:
                    child = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory,
                    )
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise ValueError(
                            "Local storage directory components must not be symlinks or files"
                        ) from exc
                    raise
                os.close(directory)
                directory = child
            os.fchmod(directory, 0o700)
        finally:
            os.close(directory)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> Recording:
        completed = row["status"] == "completed"
        return Recording(
            id=row["id"],
            metadata=json.loads(row["metadata"]),
            created_at=row["created_at"],
            status=row["status"],
            analysis_status="unavailable" if completed else "not_requested",
            analysis_reason=NO_MODEL_REASON if completed else None,
        )

    @staticmethod
    def _canonical_data_directory(path: Path) -> Path:
        path = Path(os.path.abspath(path))
        # macOS provides this root-owned system alias; user-created aliases stay forbidden.
        if (
            sys.platform == "darwin"
            and path.parts[1:2] == ("var",)
            and Path("/var").is_symlink()
            and os.readlink("/var") in {"private/var", "/private/var"}
        ):
            return Path("/private/var").joinpath(*path.parts[2:])
        return path

    @staticmethod
    def _owned(connection, recording_id: UUID, tenant: str, subject: str):
        row = connection.execute(
            "SELECT * FROM recordings WHERE id = ? AND tenant_id = ? AND subject_id = ?",
            (str(recording_id), tenant, subject),
        ).fetchone()
        if row is None:
            raise APIError(404, "recording_not_found", "Recording not found.")
        return row

    def register(self, metadata: RecordingRegistration, tenant: str, subject: str) -> Recording:
        canonical = json.dumps(
            metadata.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM recordings WHERE tenant_id = ? AND subject_id = ? AND client_id = ?",
                (tenant, subject, str(metadata.client_recording_id)),
            ).fetchone()
            if row is not None:
                if row["metadata"] != canonical:
                    raise APIError(
                        409, "metadata_conflict", "Client recording ID has other metadata."
                    )
                return self._record(row)
            recording_id = str(uuid4())
            connection.execute(
                "INSERT INTO recordings VALUES (?, ?, ?, ?, ?, ?, 'registered')",
                (
                    recording_id,
                    tenant,
                    subject,
                    str(metadata.client_recording_id),
                    canonical,
                    datetime.now(UTC).isoformat(),
                ),
            )
            return self._record(self._owned(connection, UUID(recording_id), tenant, subject))

    def get(self, recording_id: UUID, tenant: str, subject: str) -> Recording:
        with self._connection() as connection:
            return self._record(self._owned(connection, recording_id, tenant, subject))

    def list(self, tenant: str, subject: str, limit: int, offset: int) -> RecordingPage:
        with self._connection() as connection:
            connection.execute("BEGIN")
            total = connection.execute(
                "SELECT count(*) FROM recordings WHERE tenant_id = ? AND subject_id = ?",
                (tenant, subject),
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT * FROM recordings WHERE tenant_id = ? AND subject_id = ?
                   ORDER BY created_at, id LIMIT ? OFFSET ?""",
                (tenant, subject, limit, offset),
            ).fetchall()
            return RecordingPage(
                items=[self._record(row) for row in rows], total=total, limit=limit, offset=offset
            )

    def check_upload(self, recording_id: UUID, tenant: str, subject: str) -> Recording:
        record = self.get(recording_id, tenant, subject)
        if record.status != "registered":
            raise APIError(409, "upload_already_received", "Accepted audio cannot be overwritten.")
        return record

    def accept_audio(
        self, recording_id: UUID, tenant: str, subject: str, staged: Path
    ) -> Recording:
        destination = self.audio_dir / f"{recording_id}.wav"
        linked = False
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = self._owned(connection, recording_id, tenant, subject)
                if row["status"] != "registered":
                    raise APIError(
                        409, "upload_already_received", "Accepted audio cannot be overwritten."
                    )
                try:
                    # Hard-link publication is atomic and refuses to overwrite an existing object.
                    os.link(staged, destination)
                    linked = True
                    directory = os.open(self.audio_dir, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except FileExistsError as exc:
                    raise APIError(
                        409, "audio_exists", "Audio already exists; overwrite refused."
                    ) from exc
                connection.execute(
                    "UPDATE recordings SET status = 'uploaded' WHERE id = ?", (str(recording_id),)
                )
                return self._record(self._owned(connection, recording_id, tenant, subject))
        except BaseException:
            if linked:
                destination.unlink(missing_ok=True)
            raise

    def complete(self, recording_id: UUID, tenant: str, subject: str) -> Recording:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._owned(connection, recording_id, tenant, subject)
            if row["status"] == "registered":
                raise APIError(409, "upload_required", "Upload validated audio before completing.")
            path = self.audio_dir / f"{recording_id}.wav"
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError as exc:
                raise APIError(
                    409, "audio_missing", "Accepted audio is missing; completion refused."
                ) from exc
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise APIError(
                        409,
                        "audio_invalid",
                        "Accepted audio must be a regular file, not a symlink.",
                    ) from exc
                raise
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                os.close(descriptor)
                raise APIError(409, "audio_invalid", "Accepted audio must be a regular file.")
            with os.fdopen(descriptor, "rb") as source:
                metadata = RecordingRegistration.model_validate_json(row["metadata"])
                if (
                    not 0 < before.st_size <= MAX_AUDIO_BYTES
                    or before.st_size != metadata.size_bytes
                ):
                    raise APIError(
                        409,
                        "audio_invalid",
                        "Accepted audio type or size changed; completion refused.",
                    )
                digest = hashlib.sha256()
                remaining = before.st_size
                while remaining:
                    chunk = source.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise APIError(409, "audio_invalid", "Accepted audio was truncated.")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if source.read(1) or digest.hexdigest() != metadata.sha256:
                    raise APIError(
                        409, "audio_invalid", "Accepted audio checksum changed; completion refused."
                    )
                try:
                    validate_wav_stream(source, metadata)
                except APIError as exc:
                    raise APIError(
                        409, "audio_invalid", "Accepted audio is no longer valid PCM WAV."
                    ) from exc
                after = os.fstat(source.fileno())
                current = path.lstat()
                if (
                    (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    )
                    != (
                        after.st_dev,
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    )
                    or not stat.S_ISREG(current.st_mode)
                    or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
                ):
                    raise APIError(
                        409, "audio_invalid", "Accepted audio changed during completion."
                    )
            connection.execute(
                "UPDATE recordings SET status = 'completed' WHERE id = ?", (str(recording_id),)
            )
            return self._record(self._owned(connection, recording_id, tenant, subject))
