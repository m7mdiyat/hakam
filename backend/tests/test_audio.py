"""Unit tests for the ffmpeg transcode step (backend/audio.py)."""
import pytest

from backend.audio import TranscodeError, ffmpeg_available, transcode_to_m4a

from .conftest import make_tone

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _is_mp4(data: bytes) -> bool:
    return data[4:8] == b"ftyp"


def test_webm_opus_roundtrip():
    m4a, duration, stats = transcode_to_m4a(make_tone(3.0, "webm"), "audio/webm")
    assert _is_mp4(m4a)
    assert duration == pytest.approx(3.0, abs=0.35)
    # ffmpeg's sine test tone peaks ~-17 dB — far above the -50 dB gate —
    # and "speech" runs to the end of the clip.
    assert stats["max_db"] > -25
    assert stats["speech_end_s"] == pytest.approx(duration, abs=0.5)


def test_ios_mp4_roundtrip():
    m4a, duration, stats = transcode_to_m4a(make_tone(3.0, "m4a"), "audio/mp4")
    assert _is_mp4(m4a)
    assert duration == pytest.approx(3.0, abs=0.35)
    assert stats["max_db"] > -25


def test_silent_capture_measures_dead(silence=None):
    from .conftest import make_silence
    _, _, stats = transcode_to_m4a(make_silence(3.0), "audio/webm")
    assert stats["max_db"] < -80          # the dead-mic signature (-91 in prod)
    assert stats["speech_end_s"] < 0.5    # no speech anywhere


def test_silence_intervals_detected_without_moving_speech_end():
    from .conftest import make_gapped_tone
    # 2s tone, 1s gap, 2s tone: the gap is a snappable boundary, but at 1s it is
    # SHORTER than the 1.5s speech-end detector — speech_end must stay at EOF
    # (the transcription coverage check was calibrated against that).
    _, duration, stats = transcode_to_m4a(make_gapped_tone(2.0, 1.0, 2.0), "audio/webm")
    assert stats["speech_end_s"] == pytest.approx(duration, abs=0.5)
    gaps = [iv for iv in stats["silences"] if iv[0] == pytest.approx(2.0, abs=0.4)]
    assert gaps and gaps[0][1] == pytest.approx(3.0, abs=0.4)


def test_trailing_silence_still_sets_speech_end():
    from .conftest import make_gapped_tone
    # 2s tone, 0.5s gap, 2s tone, 2s trailing silence (≥1.5s): speech_end lands
    # at the trailing-silence onset, and both quiet intervals are captured.
    _, _, stats = transcode_to_m4a(make_gapped_tone(2.0, 0.5, 2.0, tail_s=2.0),
                                   "audio/webm")
    assert stats["speech_end_s"] == pytest.approx(4.5, abs=0.5)
    assert len(stats["silences"]) >= 2


def test_garbage_raises_transcode_error():
    with pytest.raises(TranscodeError):
        transcode_to_m4a(b"definitely not audio", "audio/webm")


def test_empty_raises_transcode_error():
    with pytest.raises(TranscodeError):
        transcode_to_m4a(b"", "audio/webm")
