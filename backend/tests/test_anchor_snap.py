"""Audio-proof anchor snapping (judge._anchor / judge._snap_to_boundary).

Transcript timestamps are model output at whole-second MM:SS granularity;
anchors must snap to the measured silence boundaries from upload analysis so a
quote's play button starts exactly where the quoted words do.
"""
import pytest

from backend.judge import (
    LEGACY_POSTROLL_S,
    LEGACY_PREROLL_S,
    POSTROLL_S,
    PREROLL_S,
    _anchor,
    _snap_to_boundary,
)

SILENCES = [[2.0, 2.6], [5.0, 5.5]]


def test_snap_start_to_nearby_speech_onset():
    # Model said 3.0; the pause before the sentence ends at 2.6 -> onset 2.6.
    assert _snap_to_boundary(3.0, SILENCES, "start") == (2.6, True)


def test_snap_end_to_nearby_speech_offset():
    # Model said 4.2; speech stops at 5.0 where the next pause begins.
    assert _snap_to_boundary(4.2, SILENCES, "end") == (5.0, True)


def test_time_inside_a_pause_clamps_to_its_speech_edge():
    assert _snap_to_boundary(2.3, SILENCES, "start") == (2.6, True)
    assert _snap_to_boundary(5.2, SILENCES, "end") == (5.0, True)


def test_no_boundary_in_window_returns_raw_unmatched():
    # 3.9 is >1.2s from every onset (2.6, 5.5): continuous speech, no snap.
    assert _snap_to_boundary(3.9, SILENCES, "start") == (3.9, False)
    assert _snap_to_boundary(0.0, [], "end") == (0.0, False)


def _room(audio_stats):
    return {
        "turns": [{
            "turn": "turn_a1", "debater": "a", "duration_s": 10.0,
            "audio_stats": audio_stats,
            "transcript": {"status": "ok", "segments": [
                {"i": 0, "start_s": 3.0, "end_s": 4.7, "text": "الشاهد"},
            ]},
        }],
    }


def test_anchor_snaps_and_pads_inside_the_pauses():
    a = _anchor(_room({"silences": SILENCES}), ["t1-00"])
    # start: onset 2.6 minus the tight preroll; end: offset 5.0 plus postroll —
    # both pads sit inside measured silence, never inside neighbouring speech.
    assert a["start_s"] == pytest.approx(2.6 - PREROLL_S)
    assert a["end_s"] == pytest.approx(5.0 + POSTROLL_S)


def test_anchor_without_silence_data_keeps_legacy_pads():
    # Turns uploaded before silence capture existed (audio_stats None or
    # missing the key): coarse model times keep the wide legacy pads.
    for stats in (None, {}):
        a = _anchor(_room(stats), ["t1-00"])
        assert a["start_s"] == pytest.approx(3.0 - LEGACY_PREROLL_S)
        assert a["end_s"] == pytest.approx(4.7 + LEGACY_POSTROLL_S)
