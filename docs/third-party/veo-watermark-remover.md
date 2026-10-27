# Veo Watermark Remover (GeminiWatermarkTool-Video)

**Repository:** https://github.com/allenk/VeoWatermarkRemover
**Licence:** none published. The repository has no licence file and the program's source is not public (issue 27 asks for it), so all rights are reserved by default.
**Commercial use:** conditional. It runs here on the owner's own media and is never redistributed. Revisit when the author merges it into the MIT-licensed GeminiWatermarkTool with source.
**Used by:** the **Remove Veo watermark** effect in the Library. **Surface:** `library` · **Runs:** local

## How it is installed

Like any other tool: Tools > Veo Watermark Remover > Install. The catalogue pins one release (v0.6.5-demo), and per system the download URL, size and SHA256. The installer:

- accepts only an https GitHub release URL and refuses a redirect to any other host;
- stops if more bytes arrive than the pinned size, and discards the file unless size and SHA256 both match;
- unpacks only the files the catalogue names, by member name, into `.tools/veo-watermark-remover/`, so nothing in the archive chooses where a file lands;
- records the revision in `.installed-revision`, which is what marks the install complete.

The Windows SHA256 was checked two ways when the entry was written: against the digest GitHub shows for the release asset and against a download made on the owner's machine. The author's VirusTotal links cover older releases only. The program is unsigned, so Windows or antivirus tools may warn on first run.

Uninstalling removes the folder. Nothing is installed outside the project.

## What it does and does not do

Reverse alpha blending of the visible Veo / Gemini mark. It does not remove SynthID or other invisible provenance marks. Known open reports: output cut to 240 frames on longer clips (TrendRelay checks the result's length, size and sound and fails the render if they differ), a black diamond on some clips, and newer Veo variants not yet calibrated. See `docs/watermark-removal.md`.
