import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from test_audio import registration, wav_bytes

from predmaint_backend.models import MAX_AUDIO_BYTES, NO_MODEL_REASON, APIError
from predmaint_backend.storage import Storage


def test_registration_persistence_idempotency_and_scope(tmp_path):
    store = Storage(tmp_path / "data")
    metadata = registration(wav_bytes())
    record = store.register(metadata, "tenant", "owner")
    assert Storage(store.data_dir).register(metadata, "tenant", "owner") == record
    with pytest.raises(APIError) as conflict:
        store.register(metadata.model_copy(update={"track": "other"}), "tenant", "owner")
    assert conflict.value.code == "metadata_conflict"
    assert store.register(metadata, "tenant", "other").id != record.id
    assert store.register(metadata, "other", "owner").id != record.id
    for tenant, owner in (("other", "owner"), ("tenant", "other")):
        with pytest.raises(APIError) as missing:
            store.get(record.id, tenant, owner)
        assert missing.value.status == 404
    assert store.data_dir.stat().st_mode & 0o777 == 0o700
    assert store.audio_dir.stat().st_mode & 0o777 == 0o700
    assert store.database.stat().st_mode & 0o777 == 0o600


def test_listing_is_paginated_and_owned(tmp_path):
    store = Storage(tmp_path)
    for _ in range(3):
        store.register(registration(wav_bytes()), "tenant", "owner")
    store.register(registration(wav_bytes()), "tenant", "other")
    page = store.list("tenant", "owner", 2, 1)
    assert page.total == 3 and len(page.items) == 2
    assert store.list("tenant", "owner", 2, 3).items == []


def test_concurrent_registration_and_completion(tmp_path):
    store = Storage(tmp_path)
    metadata = registration(wav_bytes())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.register(metadata, "t", "s"), range(16)))
    assert len({result.id for result in results}) == 1
    record = results[0]
    with pytest.raises(APIError) as missing:
        store.complete(record.id, "t", "s")
    assert missing.value.code == "upload_required"
    staged = store.audio_dir / ".test-stage"
    staged.write_bytes(wav_bytes())
    staged.chmod(0o600)
    store.accept_audio(record.id, "t", "s", staged)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.complete(record.id, "t", "s"), range(16)))
    assert all(result.status == "completed" for result in results)
    assert all(result.analysis_reason == NO_MODEL_REASON for result in results)
    assert all(result.analysis_status == "unavailable" for result in results)


def test_concurrent_upload_never_overwrites(tmp_path):
    store = Storage(tmp_path)
    record = store.register(registration(wav_bytes()), "t", "s")
    paths = [store.audio_dir / f".stage-{uuid4()}" for _ in range(8)]
    for path in paths:
        path.write_bytes(wav_bytes())

    def accept(path):
        try:
            return store.accept_audio(record.id, "t", "s", path).status
        except APIError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(accept, paths))
    assert results.count("uploaded") == 1
    assert results.count("upload_already_received") == 7
    assert (store.audio_dir / f"{record.id}.wav").read_bytes() == wav_bytes()


def test_existing_object_not_deleted_on_failure(tmp_path):
    store = Storage(tmp_path)
    record = store.register(registration(wav_bytes()), "t", "s")
    destination = store.audio_dir / f"{record.id}.wav"
    destination.write_bytes(b"previous data")
    staged = store.audio_dir / ".stage"
    staged.write_bytes(wav_bytes())
    with pytest.raises(APIError) as failure:
        store.accept_audio(record.id, "t", "s", staged)
    assert failure.value.code == "audio_exists"
    assert destination.read_bytes() == b"previous data"
    assert store.get(record.id, "t", "s").status == "registered"


def test_database_failure_rolls_back_only_new_object(tmp_path):
    store = Storage(tmp_path)
    record = store.register(registration(wav_bytes()), "t", "s")
    with sqlite3.connect(store.database) as database:
        database.execute("""
            CREATE TRIGGER fail_update BEFORE UPDATE ON recordings
            BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END
        """)
    staged = store.audio_dir / ".stage"
    staged.write_bytes(wav_bytes())
    unrelated = store.audio_dir / f"{uuid4()}.wav"
    unrelated.write_bytes(b"other owner's content")
    with pytest.raises(sqlite3.IntegrityError):
        store.accept_audio(record.id, "t", "s", staged)
    assert not (store.audio_dir / f"{record.id}.wav").exists()
    assert unrelated.read_bytes() == b"other owner's content"
    assert store.get(record.id, "t", "s").status == "registered"


def test_symlink_directory_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    os.symlink(target, tmp_path / "link")
    with pytest.raises(ValueError, match="symlink"):
        Storage(tmp_path / "link")


def test_symlink_ancestor_is_rejected_without_modifying_target(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    marker = target / "existing"
    marker.write_bytes(b"preserve existing data")
    (tmp_path / "link").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        Storage(tmp_path / "link" / "new" / "data")
    assert not (target / "new").exists()
    assert target.stat().st_mode & 0o777 == 0o755
    assert marker.read_bytes() == b"preserve existing data"


def test_symlink_audio_directory_is_rejected(tmp_path):
    target = tmp_path / "existing-audio"
    target.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    (data / "audio").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        Storage(data)
    assert not (data / "metadata.sqlite3").exists()


@pytest.mark.parametrize("target", ["private/var", "/private/var", "/user-controlled"])
def test_only_trusted_macos_var_alias_is_canonicalized(monkeypatch, target):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == Path("/var"))
    monkeypatch.setattr(os, "readlink", lambda path: target)
    result = Storage._canonical_data_directory(Path("/var/predmaint/data"))
    expected = (
        "/var/predmaint/data" if target == "/user-controlled" else "/private/var/predmaint/data"
    )
    assert result == Path(expected)


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize(
    "damage", ["altered", "truncated", "symlink", "directory", "fifo", "oversized", "missing"]
)
def test_completion_refuses_invalid_objects_without_deleting_them(tmp_path, completed, damage):
    store = Storage(tmp_path / "data")
    payload = wav_bytes()
    record = store.register(registration(payload), "t", "s")
    staged = store.audio_dir / ".stage"
    staged.write_bytes(payload)
    store.accept_audio(record.id, "t", "s", staged)
    if completed:
        store.complete(record.id, "t", "s")
    path = store.audio_dir / f"{record.id}.wav"
    path.unlink()
    if damage == "altered":
        path.write_bytes(payload[:-1] + b"\1")
    elif damage == "truncated":
        path.write_bytes(payload[:-2])
    elif damage == "symlink":
        path.symlink_to(staged)
    elif damage == "directory":
        path.mkdir()
    elif damage == "fifo":
        os.mkfifo(path)
    elif damage == "oversized":
        with path.open("wb") as source:
            source.truncate(MAX_AUDIO_BYTES + 1)
    with pytest.raises(APIError) as failure:
        store.complete(record.id, "t", "s")
    assert failure.value.status == 409
    assert failure.value.code == ("audio_missing" if damage == "missing" else "audio_invalid")
    assert path.exists() == (damage != "missing")
    assert store.get(record.id, "t", "s").status == ("completed" if completed else "uploaded")
    assert staged.read_bytes() == payload


@pytest.mark.parametrize(
    ("payload", "changes"),
    [(wav_bytes(rate=44100), {}), (wav_bytes()[:-2], {}), (wav_bytes(), {"frame_count": 479})],
)
def test_completion_revalidates_pcm_even_if_metadata_checksum_matches(tmp_path, payload, changes):
    store = Storage(tmp_path / "data")
    record = store.register(registration(payload, **changes), "t", "s")
    staged = store.audio_dir / ".stage"
    staged.write_bytes(payload)
    store.accept_audio(record.id, "t", "s", staged)
    with pytest.raises(APIError) as failure:
        store.complete(record.id, "t", "s")
    assert failure.value.code == "audio_invalid"
    assert store.get(record.id, "t", "s").status == "uploaded"
