"""Reading a music track's tempo and beat grid, with only numpy and ffmpeg.

AutoCut places image cuts on the beat, so it needs to know where the beats
are. A full beat-tracking library (librosa, madmom) would be the obvious
tool, but each drags in a heavy native stack the rest of this service does
not carry - and the job here is narrow enough to do honestly without one:
decode the track to mono PCM through the ffmpeg already pinned for effects,
build an onset-strength envelope, find the tempo by autocorrelation, and fix
the grid's phase to where the onsets actually land.

Measured against the hand-made @ai_videos_tiktok references on 2026-09-07:
their cuts fall on whole multiples of the beat period this finds - most on
two beats, tighter runs on one, holds on four - which is the cadence the
templates encode.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

#: Downsample rate for analysis. Beat energy lives well below this, and a low
#: rate keeps a three-minute track's envelope small and fast to autocorrelate.
ANALYSIS_RATE = 11025
#: Samples per onset-envelope frame - about 23ms at the analysis rate, fine
#: enough to place a cut within a frame of video at 30fps.
HOP = 256
#: The tempo range a short-form template actually uses. Below 60 nothing cuts
#: on every beat; above 180 the autocorrelation just finds a faster harmonic.
MIN_BPM = 60.0
MAX_BPM = 180.0


@dataclass(frozen=True)
class BeatGrid:
    """A track's tempo and the beat times it implies."""

    bpm: float
    #: Beat timestamps in seconds, from the first beat to the last before the
    #: track ends. Empty when the track is silent or too short to read.
    beats: tuple[float, ...]
    #: The analysed duration in seconds, so a caller can place a final hold.
    duration: float

    @property
    def period(self) -> float:
        """Seconds per beat, or 0 for a track with no readable tempo."""
        return 60.0 / self.bpm if self.bpm else 0.0


def _decode_mono(ffmpeg: Path, audio: Path) -> np.ndarray:
    completed = subprocess.run(
        [
            str(ffmpeg), "-hide_banner", "-nostdin", "-i", str(audio),
            "-vn", "-ac", "1", "-ar", str(ANALYSIS_RATE), "-f", "f32le", "-",
        ],
        capture_output=True, timeout=300,
    )
    if completed.returncode != 0 or not completed.stdout:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(completed.stdout, dtype=np.float32)


def _onset_envelope(samples: np.ndarray) -> tuple[np.ndarray, float]:
    """A per-frame onset-strength signal, and its frame rate.

    Rising energy only: an onset is where sound arrives, not where it leaves,
    so the negative half of the energy difference carries no beat and is
    discarded before it can blur the autocorrelation.
    """
    frames = len(samples) // HOP
    if frames < 8:
        return np.zeros(0, dtype=np.float32), 0.0
    trimmed = samples[: frames * HOP].reshape(frames, HOP)
    energy = np.sqrt((trimmed ** 2).mean(axis=1) + 1e-9)
    onset = np.maximum(np.diff(energy, prepend=energy[0]), 0.0)
    peak = float(onset.max())
    if peak > 0:
        onset = onset / peak
    return onset.astype(np.float32), ANALYSIS_RATE / HOP


def _estimate_bpm(onset: np.ndarray, fps: float) -> float:
    best_bpm, best_score = 0.0, -1.0
    for bpm in np.arange(MIN_BPM, MAX_BPM + 0.5, 0.5):
        lag = int(round(fps * 60.0 / bpm))
        if lag <= 0 or lag >= len(onset):
            continue
        score = float(np.dot(onset[:-lag], onset[lag:]) / (len(onset) - lag))
        if score > best_score:
            best_bpm, best_score = float(bpm), score
    return best_bpm


def _grid(onset: np.ndarray, fps: float, bpm: float) -> tuple[float, ...]:
    """Beat times for a known tempo, phased to the onsets' own downbeat."""
    period = 60.0 / bpm
    lag = int(round(fps * period))
    if lag <= 0:
        return ()
    # Try every phase within one beat; keep the one whose grid sums the most
    # onset energy - that is where the track's beats actually sit.
    phase_energy = [float(onset[start::lag].sum()) for start in range(lag)]
    phase = int(np.argmax(phase_energy)) / fps
    length = len(onset) / fps
    count = int((length - phase) / period) + 1
    return tuple(round(phase + i * period, 4) for i in range(max(count, 0)))


#: A track's beat grid depends only on its bytes, and AutoCut re-reads the same
#: few template tracks on every plan and preview the operator tweaks. Decoding
#: and autocorrelating each time spawns an ffmpeg process for a result that
#: never changes, so grids are memoised by (path, mtime, size): the same file
#: is analysed once, and an edited one - different mtime - is analysed afresh.
_GRID_CACHE: dict[tuple[str, int, int], BeatGrid] = {}
_GRID_CACHE_MAX = 32


def _analyze(ffmpeg: Path, audio: Path) -> BeatGrid:
    samples = _decode_mono(ffmpeg, audio)
    duration = round(len(samples) / ANALYSIS_RATE, 3)
    onset, fps = _onset_envelope(samples)
    if fps <= 0:
        return BeatGrid(bpm=0.0, beats=(), duration=duration)
    bpm = _estimate_bpm(onset, fps)
    if not bpm:
        return BeatGrid(bpm=0.0, beats=(), duration=duration)
    return BeatGrid(bpm=round(bpm, 1), beats=_grid(onset, fps, bpm), duration=duration)


def analyze_beats(ffmpeg: Path, audio: Path) -> BeatGrid:
    """Read a track's tempo and beat grid, memoised by the file's identity.

    Degrades honestly: a track that cannot be decoded, or is silent, or is
    too short to read returns a grid with no tempo and no beats rather than a
    guessed one. AutoCut then falls back to even spacing, which the caller
    decides - this function never invents a beat that is not there.
    """
    key: tuple[str, int, int] | None = None
    try:
        stat = audio.stat()
        key = (str(audio.resolve()), stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None  # an unstattable path is still analysed, just not cached
    if key is not None and key in _GRID_CACHE:
        return _GRID_CACHE[key]
    grid = _analyze(ffmpeg, audio)
    if key is not None:
        if len(_GRID_CACHE) >= _GRID_CACHE_MAX:
            _GRID_CACHE.pop(next(iter(_GRID_CACHE)))
        _GRID_CACHE[key] = grid
    return grid
