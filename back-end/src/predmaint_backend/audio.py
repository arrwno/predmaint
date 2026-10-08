import os
import struct
import wave
from pathlib import Path
from typing import BinaryIO

from .models import MAX_AUDIO_BYTES, MAX_FRAMES, APIError, RecordingRegistration


def validate_wav(path: Path, metadata: RecordingRegistration) -> None:
    with path.open("rb") as source:
        validate_wav_stream(source, metadata)


def validate_wav_stream(source: BinaryIO, metadata: RecordingRegistration) -> None:
    size = os.fstat(source.fileno()).st_size
    if size == 0:
        raise APIError(422, "empty_audio", "Audio payload is empty.")
    if size > MAX_AUDIO_BYTES:
        raise APIError(413, "audio_too_large", "Audio must contain at most 12 MB.")
    try:
        source.seek(0)
        header = source.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise APIError(422, "invalid_wav", "Expected a RIFF WAV file.")
        if struct.unpack("<I", header[4:8])[0] + 8 != size:
            raise APIError(422, "truncated_wav", "WAV container length does not match payload.")
        format_seen = data_seen = False
        declared_frames = 0
        while source.tell() < size:
            chunk = source.read(8)
            if len(chunk) != 8:
                raise APIError(422, "truncated_wav", "Incomplete WAV chunk header.")
            kind, length = struct.unpack("<4sI", chunk)
            end = source.tell() + length
            if end + (length % 2) > size:
                raise APIError(422, "truncated_wav", "WAV chunk exceeds the payload.")
            if kind == b"fmt ":
                if format_seen or length < 16:
                    raise APIError(422, "invalid_wav", "Invalid or duplicate WAV format chunk.")
                format_seen = True
                fmt = struct.unpack("<HHIIHH", source.read(16))
                if fmt != (1, 1, 48_000, 96_000, 2, 16):
                    raise APIError(
                        422,
                        "unsupported_audio",
                        "Only 48 kHz mono 16-bit PCM WAV is supported.",
                    )
            elif kind == b"data":
                if not format_seen or data_seen or length % 2:
                    raise APIError(422, "invalid_wav", "Invalid WAV PCM data chunk.")
                data_seen = True
                declared_frames = length // 2
            source.seek(end + (length % 2))
        if not format_seen or not data_seen:
            raise APIError(422, "invalid_wav", "WAV requires format and data chunks.")
        if not 0 < declared_frames <= MAX_FRAMES:
            raise APIError(
                422, "invalid_duration", "Audio duration must be > 0 and <= 120 seconds."
            )
        source.seek(0)
        with wave.open(source, "rb") as decoded:
            frames = decoded.getnframes()
            count = 0
            while chunk := decoded.readframes(48_000):
                count += len(chunk)
            if frames != declared_frames or count != frames * 2:
                raise APIError(
                    422, "truncated_wav", "Decoded PCM length differs from declared frames."
                )
        if declared_frames != metadata.frame_count:
            raise APIError(
                422, "frame_count_mismatch", "Audio frames differ from registered metadata."
            )
    except (wave.Error, EOFError, struct.error) as exc:
        raise APIError(422, "invalid_wav", "Malformed or unsupported WAV payload.") from exc
