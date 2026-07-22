# eugeneware/ffmpeg-static

- Repository: https://github.com/eugeneware/ffmpeg-static
- npm packages: `ffmpeg-static@5.3.0` and `@derhuerst/ffprobe-static@5.3.0`
- Packaged media version: FFmpeg/ffprobe 6.1.1
- License: GPL-3.0-or-later for the package and bundled static builds
- TrendRelay role: local OpenMontage cutting and artifact verification

The packages supply platform-specific FFmpeg and ffprobe executables during `npm install`. TrendRelay invokes them only from the isolated, zero-network OpenMontage render subprocess. Their paths, package versions, source-media hash, upstream OpenMontage revision, and generated-artifact hashes are recorded in render provenance.

These binaries are not owned by TrendRelay. Distribution must retain the applicable GPL notices and provide the corresponding source/build information required by the packaged build. The upstream package records the source and build provenance for each binary. Review those obligations before distributing a bundled desktop release; local development installation does not by itself grant broader media rights.

TrendRelay uses `libx264` re-encoding for keyframe-safe clip boundaries, so the packaged GPL build—not an LGPL-only FFmpeg configuration—is the relevant license posture.
