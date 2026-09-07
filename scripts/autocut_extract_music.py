"""Extract each AutoCut template's music from a reference video's audio.

The @ai_videos_tiktok reference videos in the operator's own Library were made
by hand with the same image-to-video templates AutoCut automates, so their
audio is exactly the kind of track each template wants - and the operator
asked for it to be reused. This maps each template to the reference whose
tempo and length fit it best (measured with the beat analyzer), extracts that
reference's audio to the template's named file under the workspace-agnostic
AutoCut audio directory, and normalises the level so no template is quiet.

The audio directory is git-ignored data; this script is the reproducible
record of how it was populated. Re-run it after re-measuring references, or
point it at a different workspace's downloads. Nothing here reaches the
network - it reads files already on disk.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api" / "src"))

from trendrelay_api.autocut.jobs import AUDIO_ROOT  # noqa: E402
from trendrelay_api.integrations.effects import FFMPEG  # noqa: E402
from trendrelay_api.tool_registry import PROJECT_ROOT  # noqa: E402

DB = PROJECT_ROOT / ".data" / "trendrelay.db"

#: template id -> the reference video whose audio backs it, chosen by BPM match
#: and adequate length (see docs/autocut-music.md for the measurements).
MAPPING = {
    "steady-two": "7227691789480742150.mp4",  # 94.0 BPM, 19.4s
    "rapid-one": "7230618538464120069.mp4",    # 126.5 BPM, 13.7s
    "build-up": "7228481119539219717.mp4",     # 115.0 BPM, 29.8s
    "breathe": "7227711908386753798.mp4",      # 88.0 BPM, 23.4s
    "punch": "7225539850458516741.mp4",        # 140.0 BPM, 24.5s
}


def reference_paths() -> dict[str, str]:
    """title -> original_path for every TikTok reference video."""
    if not DB.is_file():
        raise SystemExit(f"No database at {DB}")
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        snapshot = Path(handle.name)
    try:
        source = sqlite3.connect(str(DB))
        copy = sqlite3.connect(str(snapshot))
        source.backup(copy)
        rows = copy.execute(
            "SELECT title, original_path FROM media_assets "
            "WHERE platform='tiktok' AND media_kind='video'"
        ).fetchall()
        source.close()
        copy.close()
        return {title: path for title, path in rows}
    finally:
        snapshot.unlink(missing_ok=True)


def extract() -> None:
    AUDIO_ROOT.mkdir(parents=True, exist_ok=True)
    by_title = reference_paths()
    for template_id, ref_title in MAPPING.items():
        source = by_title.get(ref_title)
        if not source or not Path(source).is_file():
            print(f"! {template_id}: reference {ref_title} not on disk, skipped")
            continue
        destination = AUDIO_ROOT / f"{template_id}.m4a"
        completed = subprocess.run(
            [
                str(FFMPEG), "-hide_banner", "-nostdin", "-y", "-i", str(source),
                "-vn",
                # Even loudness so no template plays quiet, then AAC to m4a.
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:a", "aac", "-b:a", "192k", str(destination),
            ],
            capture_output=True,
        )
        if completed.returncode == 0 and destination.is_file():
            print(f"+ {template_id}.m4a  <- {ref_title}  ({destination.stat().st_size} bytes)")
        else:
            print(f"! {template_id}: ffmpeg failed\n{completed.stderr.decode('utf-8', 'replace')[-400:]}")


if __name__ == "__main__":
    extract()
