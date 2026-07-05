"""Audio-proof anchor alignment (judge._anchor and helpers).

Model segment TIMES are quantized buckets (production showed uniform 5s/15s
blocks drifting 3-6s — a ten-word sentence "timed" into 0.48s), so anchors
must never trust them when measurements exist: the quote's tokens are aligned
char-proportionally over the MEASURED speech time and snapped OUTWARD to real
pause edges. Model times survive only for turns with no stored measurements.
"""
import pytest

from backend.judge import (
    LEGACY_POSTROLL_S,
    LEGACY_PREROLL_S,
    POSTROLL_S,
    PREROLL_S,
    UNSNAPPED_POSTROLL_S,
    UNSNAPPED_PREROLL_S,
    _anchor,
    _snap_outward,
    _speech_chunks,
)

# Two speech chunks: [0, 3] and [4, 7], separated by a measured 1s pause.
CHUNKS = [(0.0, 3.0), (4.0, 7.0)]


# --- _snap_outward -----------------------------------------------------------
def test_bound_inside_pause_clamps_to_speech_edge():
    # Quoted words cannot live in silence: a start pulls to the NEXT onset,
    # an end to the PREVIOUS offset.
    assert _snap_outward(3.5, CHUNKS, "start") == (4.0, True)
    assert _snap_outward(3.5, CHUNKS, "end") == (3.0, True)


def test_bound_near_chunk_edge_adopts_the_edge_outward():
    assert _snap_outward(1.0, CHUNKS, "start") == (0.0, True)   # earlier: safe
    assert _snap_outward(2.8, CHUNKS, "end") == (3.0, True)     # later: safe
    # Never INWARD: a start near its chunk's END stays put (words follow it),
    # an end near its chunk's START stays put.
    assert _snap_outward(2.8, CHUNKS, "start") == (2.8, False)
    assert _snap_outward(4.4, CHUNKS, "end") == (4.4, False)


def test_bound_deep_in_continuous_speech_stays_unsnapped():
    assert _snap_outward(10.0, [(0.0, 30.0)], "start") == (10.0, False)
    assert _snap_outward(10.0, [(0.0, 30.0)], "end") == (10.0, False)


def test_speech_chunks_complement_and_fallback():
    assert _speech_chunks(7.0, [(3.0, 4.0)]) == [(0.0, 3.0), (4.0, 7.0)]
    assert _speech_chunks(30.0, []) == [(0.0, 30.0)]        # continuous speech
    assert _speech_chunks(5.0, [(0.0, 1.0), (4.5, 5.0)]) == [(1.0, 4.5)]


# --- _anchor: aligned over measured speech ----------------------------------
# Fixture: 6 words, 4 normalized chars each (equal weights), split over two
# segments, spoken over CHUNKS (6s of speech in a 7s file). The model's own
# bucket times are deliberately WRONG (both segments claim 0..1s) — nothing
# may leak from them.
SEG_A = "واحد بحار جمال"
SEG_B = "سماء قلوب نجوم"


def _room(audio_stats, texts=(SEG_A, SEG_B), duration=7.0):
    return {
        "turns": [{
            "turn": "turn_a1", "debater": "a", "duration_s": duration,
            "audio_stats": audio_stats,
            "transcript": {"status": "ok", "segments": [
                {"i": i, "start_s": 0.0, "end_s": 1.0, "text": t}
                for i, t in enumerate(texts)
            ]},
        }],
    }


STATS = {"silences": [{"s": 3.0, "e": 4.0}]}


def test_anchor_aligns_quote_over_measured_speech_not_model_times():
    # The quote is the second half of the token stream -> second half of the
    # 6s speech time -> the second chunk exactly, snapped to its edges.
    a = _anchor(_room(STATS), ["t1-01"], SEG_B)
    assert a["start_s"] == pytest.approx(4.0 - PREROLL_S)
    assert a["end_s"] == pytest.approx(7.0)  # 7.0 + POSTROLL_S clamped to EOF


def test_anchor_quote_overrides_a_wrong_citation():
    # Model cited segment 0 but quoted segment 1's words: the TEXT wins —
    # identical window to the correct citation.
    a = _anchor(_room(STATS), ["t1-00"], SEG_B)
    assert a["start_s"] == pytest.approx(4.0 - PREROLL_S)
    assert a["end_s"] == pytest.approx(7.0)


def test_anchor_without_quote_uses_cited_segments_tokens():
    # Key-moment path (no quote): the cited segment's token range aligns the
    # same way.
    a = _anchor(_room(STATS), ["t1-01"])
    assert a["start_s"] == pytest.approx(4.0 - PREROLL_S)
    assert a["end_s"] == pytest.approx(7.0)


def test_anchor_deep_in_continuous_speech_keeps_wide_pads():
    # 10 equal-weight words over one 30s chunk; quote = words 5-6 ->
    # est. [12.0, 18.0], too far from any edge to snap.
    words = "واحد بحار جمال سماء قلوب نجوم كتاب قلمي ورقه شمسي".split()
    room = _room({"silences": []},
                 texts=(" ".join(words[:5]), " ".join(words[5:])), duration=30.0)
    a = _anchor(room, ["t1-00"], " ".join(words[4:6]))
    assert a["start_s"] == pytest.approx(12.0 - UNSNAPPED_PREROLL_S)
    assert a["end_s"] == pytest.approx(18.0 + UNSNAPPED_POSTROLL_S)


def test_anchor_without_silence_data_keeps_legacy_model_times():
    # Turns uploaded before silence capture existed (audio_stats None or
    # missing the key): model times with the wide legacy pads are all we have.
    for stats in (None, {}):
        a = _anchor(_room(stats), ["t1-00"], SEG_A)
        assert a["start_s"] == pytest.approx(max(0.0, 0.0 - LEGACY_PREROLL_S))
        assert a["end_s"] == pytest.approx(1.0 + LEGACY_POSTROLL_S)
