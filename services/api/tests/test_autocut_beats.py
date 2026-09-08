"""Beat analysis is memoised by the file's identity, not recomputed per call.

AutoCut re-reads the same few template tracks on every plan and preview the
operator tweaks; decoding and autocorrelating each time spawns an ffmpeg
process for a result that never changes. The grid is cached by (path, mtime,
size), so the same file is read once and an edited one is read afresh.
"""

from __future__ import annotations

from pathlib import Path

from trendrelay_api.autocut import beat_analysis as ba
from trendrelay_api.autocut.beat_analysis import BeatGrid


def _fake_track(tmp_path: Path, name: str, body: bytes = b"\x00\x01\x02") -> Path:
    path = tmp_path / name
    path.write_bytes(body)
    return path


def test_the_same_file_is_analysed_once_then_served_from_cache(tmp_path, monkeypatch) -> None:
    ba._GRID_CACHE.clear()
    calls: list[Path] = []

    def counted(_ffmpeg, audio):
        calls.append(audio)
        return BeatGrid(bpm=120.0, beats=(0.0, 0.5), duration=1.0)

    monkeypatch.setattr(ba, "_analyze", counted)
    track = _fake_track(tmp_path, "loop.m4a")

    first = ba.analyze_beats(Path("ffmpeg"), track)
    second = ba.analyze_beats(Path("ffmpeg"), track)

    assert first is second          # the very same grid object, straight from cache
    assert len(calls) == 1          # analysed once, not twice


def test_an_edited_file_is_analysed_again(tmp_path, monkeypatch) -> None:
    ba._GRID_CACHE.clear()
    calls: list[Path] = []
    monkeypatch.setattr(ba, "_analyze", lambda _f, a: calls.append(a) or BeatGrid(0.0, (), 0.0))
    track = _fake_track(tmp_path, "loop.m4a")

    ba.analyze_beats(Path("ffmpeg"), track)
    # A different size and mtime: the cache key changes, so it is read again.
    track.write_bytes(b"\x00\x01\x02\x03\x04\x05")
    ba.analyze_beats(Path("ffmpeg"), track)

    assert len(calls) == 2
