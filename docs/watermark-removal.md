# Removing watermarks with external tools (PM-340, PM-341)

The **Remove Veo watermark** effect takes the visible Veo or Gemini mark off a generated video in
the Library. The **Remove Gemini watermark** effect does the same for a generated picture. Both sit
in the effect stack like any other step and run as their own pass, in the order you put them. Put
the step first: it works on the media's own pixels, so crops, zooms and captions added before it
would change what it has to find.

## What does the work

An external program, `GeminiWatermarkTool-Video` from
[allenk/VeoWatermarkRemover](https://github.com/allenk/VeoWatermarkRemover). It undoes the mark by
reverse alpha blending, so nothing is generated or painted over. Its source is not published and the
repository has no licence file, so it is never bundled or redistributed.

It is a tool in the Tools page like the others: open **Veo Watermark Remover** and press Install.
The catalogue pins one release with its size and SHA256 for each system, the installer checks both,
and the program lands in `.tools/veo-watermark-remover/` inside this project. Uninstall removes it.
The effect is offered only while the tool is installed and switched on. Details:
`docs/third-party/veo-watermark-remover.md`.

The image twin, **Remove Gemini watermark**, works the same way through `gwt-mini` from
[allenk/GeminiWatermarkTool](https://github.com/allenk/GeminiWatermarkTool) (MIT, v0.3.2 pinned),
landing in `.tools/gemini-watermark-remover/`. Details:
`docs/third-party/gemini-watermark-remover.md`.

## What TrendRelay adds

- The program runs from an argument list, never a shell, with a minimal environment, in a scratch
  folder that is removed afterwards. The original clip is never changed.
- The result must match the source in running time (within 0.3 s), picture size and sound. The
  tool has an open report of cutting clips off at 240 frames, so a shortened clip fails the render
  instead of being delivered.
- A clip with no watermark passes through unchanged and the render report says so.
- A preview renders only the first seconds, cut by stream copy.
- Tried on a real 10 s library clip (720x1280): the diamond was gone, length and sound matched, 9 s.

## Limits

- Visible marks only. SynthID and other invisible provenance marks are not touched.
- Quality varies on bright, busy backgrounds; the tool's own notes list residue and dark patches
  as known cases. The optional intensity assist can over-correct and is off by default.
- Use it on videos you made or have the right to edit.
