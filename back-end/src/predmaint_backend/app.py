import hashlib
import os
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Receive, Scope, Send

from .audio import validate_wav
from .models import (
    MAX_AUDIO_BYTES,
    MAX_METADATA_BYTES,
    APIError,
    Recording,
    RecordingPage,
    RecordingRegistration,
)
from .security import Principal, get_principal
from .storage import Storage


class MetadataBodyLimit:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != "/v1/recordings"
        ):
            await self.app(scope, receive, send)
            return

        async def reject(status: int, code: str, message: str) -> None:
            response = JSONResponse(
                status_code=status, content={"error": {"code": code, "message": message}}
            )
            await response(scope, receive, send)

        for name, value in scope["headers"]:
            if name.lower() == b"content-length":
                if not value.isdigit():
                    await reject(400, "invalid_content_length", "Invalid Content-Length.")
                    return
                length = value.lstrip(b"0") or b"0"
                if len(length) > len(str(MAX_METADATA_BYTES)) or int(length) > MAX_METADATA_BYTES:
                    await reject(413, "metadata_too_large", "Metadata must contain at most 64 KiB.")
                    return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                await reject(400, "metadata_interrupted", "Metadata upload was interrupted.")
                return
            chunk = message.get("body", b"")
            if len(chunk) > MAX_METADATA_BYTES - len(body):
                await reject(413, "metadata_too_large", "Metadata must contain at most 64 KiB.")
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        async def bounded_receive():
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, bounded_receive, send)


def create_app(data_dir: Path | None = None) -> FastAPI:
    """Create the synthetic-data-only local API; Azure services are not started."""
    if os.getenv("PREDMAINT_AZURE_MODE", "development") != "development" or os.getenv(
        "PREDMAINT_ENVIRONMENT", "development"
    ) not in {"development", "test"}:
        raise RuntimeError(
            "Cloud and production operation are unavailable in this local foundation."
        )
    app = FastAPI(
        title="Predmaint local foundation",
        description=(
            "Local development with synthetic recordings only. No cloud/production mode, "
            "approved model, analysis worker, SAS, or review workflow is implemented."
        ),
    )
    storage = Storage(data_dir or Path(__file__).resolve().parents[2] / "local-data")
    app.state.storage = storage
    app.add_middleware(MetadataBodyLimit)

    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status, content={"error": {"code": code, "message": message}}
        )

    @app.exception_handler(APIError)
    async def api_error(_request: Request, exc: APIError):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        return error(422, "invalid_request", "Request parameters or metadata are invalid.")

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code, content=exc.detail, headers=exc.headers
            )
        code = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            503: "auth_unavailable",
        }.get(exc.status_code, "http_error")
        return error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(OSError)
    @app.exception_handler(sqlite3.Error)
    async def storage_error(_request: Request, _exc: Exception):
        return error(503, "storage_unavailable", "Local storage is unavailable; retry later.")

    def operator(principal: Principal = Depends(get_principal)) -> Principal:
        if "operator" not in principal.roles:
            raise APIError(403, "operator_required", "The operator role is required.")
        return principal

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.post("/v1/recordings", response_model=Recording)
    def register(metadata: RecordingRegistration, principal: Principal = Depends(operator)):
        return storage.register(metadata, principal.tenant_id, principal.subject_id)

    @app.get("/v1/recordings", response_model=RecordingPage)
    def listing(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        principal: Principal = Depends(get_principal),
    ):
        return storage.list(principal.tenant_id, principal.subject_id, limit, offset)

    @app.get("/v1/recordings/{recording_id}", response_model=Recording)
    def detail(recording_id: UUID, principal: Principal = Depends(get_principal)):
        return storage.get(recording_id, principal.tenant_id, principal.subject_id)

    @app.put("/v1/recordings/{recording_id}/audio", response_model=Recording)
    async def upload(
        recording_id: UUID, request: Request, principal: Principal = Depends(operator)
    ):
        record = storage.check_upload(recording_id, principal.tenant_id, principal.subject_id)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() not in {
            "audio/wav",
            "audio/x-wav",
            "application/octet-stream",
        }:
            raise APIError(415, "unsupported_media_type", "Send a direct WAV body, not multipart.")
        content_length = request.headers.get("content-length")
        if content_length is not None:
            if not content_length.isascii() or not content_length.isdecimal():
                raise APIError(400, "invalid_content_length", "Invalid Content-Length.")
            normalized_length = content_length.lstrip("0") or "0"
            if len(normalized_length) > len(str(MAX_AUDIO_BYTES)):
                raise APIError(413, "audio_too_large", "Audio must contain at most 12 MB.")
            declared_size = int(normalized_length)
            if declared_size > MAX_AUDIO_BYTES:
                raise APIError(413, "audio_too_large", "Audio must contain at most 12 MB.")
            if declared_size == 0:
                raise APIError(422, "empty_audio", "Audio payload is empty.")
            if declared_size != record.metadata.size_bytes:
                raise APIError(422, "size_mismatch", "Body size differs from registered metadata.")
        staged = storage.audio_dir / f".upload-{uuid4()}"
        digest = hashlib.sha256()
        size = 0
        try:
            descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_AUDIO_BYTES:
                        raise APIError(413, "audio_too_large", "Audio must contain at most 12 MB.")
                    if size > record.metadata.size_bytes:
                        raise APIError(422, "size_mismatch", "Body exceeds registered size.")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size == 0:
                raise APIError(422, "empty_audio", "Audio payload is empty.")
            if size != record.metadata.size_bytes:
                raise APIError(422, "size_mismatch", "Body size differs from registered metadata.")
            if digest.hexdigest() != record.metadata.sha256:
                raise APIError(422, "checksum_mismatch", "SHA256 differs from registered checksum.")
            validate_wav(staged, record.metadata)
            return storage.accept_audio(
                recording_id, principal.tenant_id, principal.subject_id, staged
            )
        except ClientDisconnect as exc:
            raise APIError(400, "upload_interrupted", "Audio upload was interrupted.") from exc
        finally:
            staged.unlink(missing_ok=True)

    @app.post("/v1/recordings/{recording_id}/complete", response_model=Recording)
    def complete(recording_id: UUID, principal: Principal = Depends(operator)):
        return storage.complete(recording_id, principal.tenant_id, principal.subject_id)

    return app
