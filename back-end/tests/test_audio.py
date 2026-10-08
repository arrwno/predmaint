import hashlib
import io
import struct
import wave
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from predmaint_backend.audio import validate_wav
from predmaint_backend.models import APIError, RecordingRegistration


def wav_bytes(frames=480, rate=48000, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        audio.writeframes(b"\0" * frames * channels * width)
    return output.getvalue()


def registration(payload, frames=480, **changes):
    values = {
        "client_recording_id": uuid4(),
        "synthetic": True,
        "captured_at": datetime.now(UTC),
        "site_id": "synthetic-site",
        "track": "synthetic-track",
        "direction": "synthetic-direction",
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "frame_count": frames,
    }
    values.update(changes)
    return RecordingRegistration(**values)


def test_valid_pcm_and_maximum_duration(tmp_path):
    for frames in (1, 480, 48000 * 120):
        payload = wav_bytes(frames=frames)
        path = tmp_path / "synthetic.wav"
        path.write_bytes(payload)
        validate_wav(path, registration(payload, frames))


@pytest.mark.parametrize(
    ("payload", "frames", "code"),
    [
        (b"not audio", 480, "invalid_wav"),
        (wav_bytes()[:-2], 480, "truncated_wav"),
        (wav_bytes(rate=44100), 480, "unsupported_audio"),
        (wav_bytes(channels=2), 480, "unsupported_audio"),
        (wav_bytes(width=1), 480, "unsupported_audio"),
        (wav_bytes(frames=0), 1, "invalid_duration"),
        (wav_bytes(), 479, "frame_count_mismatch"),
        (wav_bytes(frames=48000 * 120 + 1), 480, "invalid_duration"),
    ],
)
def test_reject_invalid_audio(tmp_path, payload, frames, code):
    path = tmp_path / "synthetic.wav"
    path.write_bytes(payload)
    with pytest.raises(APIError) as raised:
        validate_wav(path, registration(payload, frames))
    assert raised.value.code == code


def test_reject_declared_data_larger_than_decoded_payload(tmp_path):
    payload = bytearray(wav_bytes())
    struct.pack_into("<I", payload, 40, len(payload) - 44 + 2)
    path = tmp_path / "synthetic.wav"
    path.write_bytes(payload)
    with pytest.raises(APIError, match="chunk exceeds"):
        validate_wav(path, registration(payload))


def test_reject_partial_pcm_sample(tmp_path):
    payload = bytearray(wav_bytes())
    struct.pack_into("<I", payload, 40, len(payload) - 45)
    path = tmp_path / "synthetic.wav"
    path.write_bytes(payload)
    with pytest.raises(APIError) as raised:
        validate_wav(path, registration(payload))
    assert raised.value.code == "invalid_wav"
