import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_audio import registration, wav_bytes

from predmaint_backend.app import create_app
from predmaint_backend.models import MAX_AUDIO_BYTES, MAX_METADATA_BYTES
from predmaint_backend.security import Principal, get_principal


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("PREDMAINT_ENTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("PREDMAINT_ENTRA_AUDIENCE", raising=False)
    monkeypatch.delenv("PREDMAINT_AZURE_MODE", raising=False)
    monkeypatch.delenv("PREDMAINT_ENVIRONMENT", raising=False)
    return create_app(tmp_path / "data")


@pytest.fixture
def client(app):
    app.dependency_overrides[get_principal] = lambda: Principal(
        "tenant", "owner", frozenset({"operator"})
    )
    with TestClient(app) as client:
        yield client


def register(client, payload=None, **changes):
    payload = wav_bytes() if payload is None else payload
    metadata = registration(payload, **changes).model_dump(mode="json")
    response = client.post("/v1/recordings", json=metadata)
    assert response.status_code == 200, response.text
    return response.json(), metadata


def upload(client, recording_id, payload=None, **kwargs):
    return client.put(
        f"/v1/recordings/{recording_id}/audio",
        content=wav_bytes() if payload is None else payload,
        headers=kwargs.pop("headers", {"content-type": "audio/wav"}),
        **kwargs,
    )


def test_health_and_fail_closed(app):
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        response = client.get("/v1/recordings")
        assert response.status_code == 503
        assert set(response.json()) == {"error"}
        assert set(response.json()["error"]) == {"code", "message"}


def test_workflow_and_idempotent_retries(client, app):
    record, metadata = register(client)
    recording_id = record["id"]
    assert client.post("/v1/recordings", json=metadata).json()["id"] == recording_id
    changed = metadata | {"track": "different"}
    assert client.post("/v1/recordings", json=changed).status_code == 409
    assert client.post(f"/v1/recordings/{recording_id}/complete").status_code == 409
    assert upload(client, recording_id).json()["status"] == "uploaded"
    complete = client.post(f"/v1/recordings/{recording_id}/complete")
    assert complete.status_code == 200
    result = complete.json()
    assert result["status"] == "completed"
    assert result["analysis_status"] == "unavailable"
    assert "No approved analysis model" in result["analysis_reason"]
    assert client.post(f"/v1/recordings/{recording_id}/complete").json() == result
    assert upload(client, recording_id).status_code == 409
    assert client.get(f"/v1/recordings/{recording_id}").json() == result
    path = app.state.storage.audio_dir / f"{recording_id}.wav"
    assert path.read_bytes() == wav_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(app.state.storage.audio_dir.glob(".upload-*")) == []


def test_owner_and_tenant_scope(client, app):
    record, _ = register(client)
    for tenant, owner in (("tenant", "other"), ("other", "owner")):
        app.dependency_overrides[get_principal] = lambda: Principal(
            tenant, owner, frozenset({"operator"})
        )
        assert client.get("/v1/recordings").json()["items"] == []
        assert client.get(f"/v1/recordings/{record['id']}").status_code == 404
        assert upload(client, record["id"]).status_code == 404
        assert client.post(f"/v1/recordings/{record['id']}/complete").status_code == 404


def test_operator_required_but_authorized_owner_can_read(client, app):
    record, metadata = register(client)
    app.dependency_overrides[get_principal] = lambda: Principal(
        "tenant", "owner", frozenset({"reviewer"})
    )
    assert client.post("/v1/recordings", json=metadata).status_code == 403
    assert upload(client, record["id"]).status_code == 403
    assert client.post(f"/v1/recordings/{record['id']}/complete").status_code == 403
    assert client.get(f"/v1/recordings/{record['id']}").status_code == 200
    assert client.get("/v1/recordings").status_code == 200


def test_pagination_and_validation(client):
    for _ in range(3):
        register(client)
    page = client.get("/v1/recordings?limit=2&offset=1").json()
    assert page["total"] == 3 and len(page["items"]) == 2
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert client.get(f"/v1/recordings?{query}").json()["error"]["code"] == "invalid_request"
    _, metadata = register(client)
    for changes in (
        {"synthetic": False},
        {"synthetic": 1},
        {"captured_at": "2026-10-08T12:00:00"},
        {"frame_count": 0},
        {"size_bytes": MAX_AUDIO_BYTES + 1},
        {"path": "../../outside"},
    ):
        response = client.post("/v1/recordings", json=metadata | changes)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    ("payload", "changes", "code"),
    [
        (b"not a wav", {}, "invalid_wav"),
        (wav_bytes()[:-2], {}, "truncated_wav"),
        (wav_bytes(rate=44100), {}, "unsupported_audio"),
        (wav_bytes(), {"frame_count": 479}, "frame_count_mismatch"),
        (wav_bytes(), {"sha256": "0" * 64}, "checksum_mismatch"),
    ],
)
def test_invalid_upload_leaves_registration_and_other_data(client, app, payload, changes, code):
    record, _ = register(client, payload, **changes)
    unrelated = app.state.storage.audio_dir / f"{uuid4()}.wav"
    unrelated.write_bytes(b"previous owner data")
    response = upload(client, record["id"], payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert client.get(f"/v1/recordings/{record['id']}").json()["status"] == "registered"
    assert unrelated.read_bytes() == b"previous owner data"
    assert list(app.state.storage.audio_dir.glob(".upload-*")) == []


def test_upload_preconditions_and_size_limits(client):
    record, _ = register(client)
    assert upload(client, uuid4()).status_code == 404
    response = upload(client, record["id"], headers={"content-type": "multipart/form-data"})
    assert response.status_code == 415
    response = upload(
        client,
        record["id"],
        headers={"content-type": "audio/wav", "content-length": str(MAX_AUDIO_BYTES + 1)},
    )
    assert response.status_code == 413
    assert upload(client, record["id"], b"").json()["error"]["code"] == "empty_audio"
    assert upload(client, record["id"], b"short").json()["error"]["code"] == "size_mismatch"
    response = upload(
        client,
        record["id"],
        headers={"content-type": "audio/wav", "content-length": "invalid"},
    )
    assert response.status_code == 400
    response = upload(
        client,
        record["id"],
        headers={"content-type": "audio/wav", "content-length": "9" * 5000},
    )
    assert response.status_code == 413


def test_chunked_upload_and_bounded_oversize(client, app):
    payload = wav_bytes()
    record, _ = register(client)
    response = upload(client, record["id"], iter([payload[:20], payload[20:]]))
    assert response.status_code == 200
    record, _ = register(client)
    response = upload(client, record["id"], iter([b"x" * (MAX_AUDIO_BYTES + 1)]))
    assert response.status_code == 413
    assert list(app.state.storage.audio_dir.glob(".upload-*")) == []


def test_concurrent_upload_and_completion(client):
    record, _ = register(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: upload(client, record["id"]).status_code, range(4)))
    assert responses.count(200) == 1
    assert responses.count(409) == 3
    with ThreadPoolExecutor(max_workers=4) as pool:
        completed = list(
            pool.map(lambda _: client.post(f"/v1/recordings/{record['id']}/complete"), range(4))
        )
    assert all(response.status_code == 200 for response in completed)
    assert all(response.json() == completed[0].json() for response in completed)


def test_cloud_and_production_modes_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("PREDMAINT_AZURE_MODE", "azure")
    with pytest.raises(RuntimeError, match="unavailable"):
        create_app(tmp_path)
    monkeypatch.setenv("PREDMAINT_AZURE_MODE", "development")
    monkeypatch.setenv("PREDMAINT_ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="unavailable"):
        create_app(tmp_path)


def test_interrupted_stream_is_cleaned_and_can_retry(client, app):
    record, _ = register(client)
    path = f"/v1/recordings/{record['id']}/audio"
    messages = iter(
        [
            {"type": "http.request", "body": wav_bytes()[:20], "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"audio/wav")],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 80),
    }
    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"])["error"]["code"] == "upload_interrupted"
    assert list(app.state.storage.audio_dir.glob(".upload-*")) == []
    assert client.get(f"/v1/recordings/{record['id']}").json()["status"] == "registered"
    assert upload(client, record["id"]).status_code == 200


def metadata_request(app, chunks, headers=()):
    sent = []
    received = []
    messages = iter(chunks)

    async def receive():
        message = next(messages)
        received.append(message)
        return message

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/recordings",
        "raw_path": b"/v1/recordings",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 80),
    }
    asyncio.run(app(scope, receive, send))
    return sent[0]["status"], json.loads(sent[1]["body"]), received


@pytest.mark.parametrize("length", [str(MAX_METADATA_BYTES + 1), "9" * 5000])
def test_metadata_declared_oversize_rejected_without_receiving_body(app, length):
    status, body, received = metadata_request(app, [], [(b"content-length", length.encode())])
    assert status == 413
    assert body == {
        "error": {"code": "metadata_too_large", "message": "Metadata must contain at most 64 KiB."}
    }
    assert received == []


@pytest.mark.parametrize(
    "headers",
    [[], [(b"transfer-encoding", b"chunked")], [(b"content-length", b"2")]],
)
def test_metadata_actual_stream_is_bounded_before_json_parsing(app, headers):
    chunks = [
        {"type": "http.request", "body": b" " * MAX_METADATA_BYTES, "more_body": True},
        {"type": "http.request", "body": b"x", "more_body": True},
        {"type": "http.request", "body": b"never consumed", "more_body": False},
    ]
    status, body, received = metadata_request(app, chunks, headers)
    assert status == 413
    assert body["error"]["code"] == "metadata_too_large"
    assert len(received) == 2
    assert app.state.storage.list("tenant", "owner", 20, 0).total == 0


def test_metadata_exact_limit_and_streamed_registration(client, app):
    metadata = registration(wav_bytes()).model_dump(mode="json")
    encoded = json.dumps(metadata).encode()
    padded = encoded + b" " * (MAX_METADATA_BYTES - len(encoded))
    chunks = [
        {"type": "http.request", "body": padded[:100], "more_body": True},
        {"type": "http.request", "body": padded[100:], "more_body": False},
    ]
    status, body, received = metadata_request(app, chunks)
    assert status == 200
    assert body["metadata"] == metadata
    assert len(received) == 2


def test_streamed_metadata_preserves_fail_closed_auth(app):
    payload = json.dumps(registration(wav_bytes()).model_dump(mode="json")).encode()
    status, body, _ = metadata_request(
        app, [{"type": "http.request", "body": payload, "more_body": False}]
    )
    assert status == 503
    assert body["error"]["code"] == "authentication_unconfigured"


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("damage", ["altered", "truncated", "replacement", "symlink"])
def test_completion_revalidates_accepted_audio(client, app, completed, damage):
    record, _ = register(client)
    recording_id = record["id"]
    assert upload(client, recording_id).status_code == 200
    if completed:
        assert client.post(f"/v1/recordings/{recording_id}/complete").status_code == 200
    path = app.state.storage.audio_dir / f"{recording_id}.wav"
    payload = wav_bytes()
    if damage == "altered":
        path.write_bytes(payload[:-1] + b"\1")
    elif damage == "truncated":
        path.write_bytes(payload[:-2])
    elif damage == "replacement":
        path.unlink()
        path.write_bytes(b"x" * len(payload))
    else:
        target = app.state.storage.audio_dir / "symlink-target.wav"
        target.write_bytes(payload)
        path.unlink()
        path.symlink_to(target)
    response = client.post(f"/v1/recordings/{recording_id}/complete")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "audio_invalid"
    assert path.exists()
    assert client.get(f"/v1/recordings/{recording_id}").json()["status"] == (
        "completed" if completed else "uploaded"
    )
