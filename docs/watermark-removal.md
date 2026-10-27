# Removing the Veo / Gemini watermark (PM-340)

The **Remove Veo watermark** effect takes the visible Veo or Gemini mark off a generated video in
the Library. It sits in the effect stack like any other step and runs as its own pass, in the order
you put it. Put it first: it works on the clip's own frames, so crops, zooms and captions added
before it would change what it has to find.

## What does the work

An external program, `GeminiWatermarkTool-Video` from
[allenk/VeoWatermarkRemover](https://github.com/allenk/VeoWatermarkRemover). It undoes the mark by
reverse alpha blending, so nothing is generated or painted over. It is not bundled, because its
source is not published, the repository has no licence file and its builds are unsigned.

Install it yourself:

1. Download the release for your system from the project's Releases page.
2. Check its SHA256 against the value you trust (the author lists VirusTotal scans per release).
3. Put the program in `.data/tools/veo-watermark/`, or set `TRENDRELAY_VEO_WATERMARK_TOOL` to its path.
4. Optionally set `TRENDRELAY_VEO_WATERMARK_TOOL_SHA256` to that checksum. TrendRelay then refuses
   to run a file that does not match it.

Until it is installed the effect shows as unavailable and says how to install it.

## What TrendRelay adds

- The program runs from an argument list, never a shell, with a minimal environment, in a scratch
  folder that is removed afterwards. The original clip is never changed.
- The result must match the source in running time (within 0.3 s), picture size and sound. The
  tool has an open report of cutting clips off at 240 frames, so a shortened clip fails the render
  instead of being delivered.
- A clip with no watermark passes through unchanged and the render report says so.
- A preview renders only the first seconds, cut by stream copy.

## Limits

- Visible marks only. SynthID and other invisible provenance marks are not touched.
- Quality varies on bright, busy backgrounds; the tool's own notes list residue and dark patches
  as known cases. The optional intensity assist can over-correct and is off by default.
- Use it on videos you made or have the right to edit.
