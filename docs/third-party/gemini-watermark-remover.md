# Gemini Watermark Remover (GeminiWatermarkTool)

**Repository:** https://github.com/allenk/GeminiWatermarkTool
**Licence:** MIT, source published. The program is still run here as the author's prebuilt, unsigned mini build (`gwt-mini`, about 5 MB) rather than a self-built binary, on the owner's own media, and never redistributed.
**Commercial use:** allowed under the MIT licence for the owner's own media; still never redistributed.
**Used by:** the **Remove Gemini watermark** effect in the Library. **Surface:** `library` · **Runs:** local

## How it is installed

Like any other tool: Tools > Gemini Watermark Remover > Install. The catalogue pins one release (v0.3.2), and per system the download URL, size and SHA256. The installer:

- accepts only an https GitHub release URL and refuses a redirect to any other host;
- stops if more bytes arrive than the pinned size, and discards the file unless size and SHA256 both match;
- unpacks only the files the catalogue names, by member name, into `.tools/gemini-watermark-remover/`, so nothing in the archive chooses where a file lands;
- records the revision in `.installed-revision`, which is what marks the install complete.

The Windows SHA256 was checked two ways when the entry was written: against the release's published SHA256SUMS.txt and against a download made on the owner's machine (5,086,584 bytes, matching). The program is unsigned, so Windows or antivirus tools may warn on first run.

Uninstalling removes the folder. Nothing is installed outside the project.

## What it does and does not do

Reverse alpha blending of the visible Gemini sparkle / bottom-right mark. It does not remove SynthID or other invisible provenance marks. Images with no mark are detected and passed through untouched rather than altered. See `docs/watermark-removal.md`.
