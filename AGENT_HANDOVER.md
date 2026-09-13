# Agent Handover

Last updated: 2026-09-14

## Feature: Media Tag Category Coloring by Class, 2026-09-14

- **Context & Request**:
  - Tags on media cards and the detail pane (e.g., `Cover text`, `Transcript draft`, `On-screen text draft`) were previously all rendered with the same blue color (`#1a56c4` on `var(--link-bg)`).
  - User requested distinguishing tag classes: keep the blue color for effects (`Cover text`, `Face blur`, etc.), and use different harmonious colors for other classes (transcripts, OCR on-screen text, captions, voiceovers, scene descriptions).
- **Changes**:
  - `apps/web/app/console.css`:
    - Defined semantic tokens inside `:root`: `--tag-effect` (blue), `--tag-transcript` (violet `#5e35b1` on `#f3eefa`), `--tag-ocr` (amber `#7a5c00` on `#fdf5db`), `--tag-captions` (teal `#0a635b` on `#e4f4f2`), `--tag-voiceover` (rose `#8f2747` on `#faebf0`), `--tag-vision` (slate `#4b5563` on `#f1f3f5`).
  - `apps/web/app/media-library.css`:
    - Added category modifiers: `.blurred-tag.tag-effect`, `.blurred-tag.tag-transcript`, `.blurred-tag.tag-ocr`, `.blurred-tag.tag-captions`, `.blurred-tag.tag-voiceover`, `.blurred-tag.tag-vision`.
  - `apps/web/app/library/page.tsx`:
    - Typed media tags as `MediaTag` with `MediaTagKind = "effect" | "transcript" | "ocr" | "captions" | "voiceover" | "vision"`.
    - Updated `processingTags` and `assetTags` to attach category metadata to each tag.
    - Updated card rendering and detail pane kicker to render `<em className={`blurred-tag ${tagClass(tag.kind)}`} key={`${tag.kind}-${tag.name}`}>{tag.name}</em>`.
- **Verification**:
  - `node --test apps/web/lib/palette-guard.test.ts`: Passed (all 6 palette-guard tests passed, 0 hardcoded color leaks).
  - Web unit tests: `npm --prefix apps/web test` (504/504 passed).
  - Typecheck: `npm --prefix apps/web run typecheck` (0 errors).
  - Lint: `npm --prefix apps/web run lint` (0 errors).

## Fix: Asset Transcripts Parsing in BulkVoiceEditor and VoiceEditor, 2026-09-14

- **Context & Request**:
  - Even after enabling "Allow machine drafts", the Voiceover dialog still showed "24 items have no transcript and will be skipped" and "Generate for 0", despite the cards clearly having `Transcript draft` badges.
  - Root cause:
    - `GET /api/workspaces/{workspace_id}/media/library/assets/{asset_id}` returned `{"asset": {"id": ..., "transcripts": [...]}}`.
    - `bulk-voice-editor.tsx` and `voice-editor.tsx` were expecting `payload.transcripts` directly (`(payload.transcripts ?? [])`), which evaluated to `undefined` / empty array.
- **Changes**:
  - `services/api/src/trendrelay_api/media_library_api.py`:
    - Updated `get_asset` to return `{"asset": view, "transcripts": view.get("transcripts", [])}` so both top-level and nested access work.
  - `apps/web/app/library/bulk-voice-editor.tsx`:
    - Safely extract transcripts using `payload.asset?.transcripts ?? payload.transcripts ?? []`.
  - `apps/web/app/library/voice-editor.tsx`:
    - Safely extract transcripts using `payload.asset?.transcripts ?? payload.transcripts ?? []`.
- **Verification**:
  - Backend: 48/48 tests passed (`pytest services/api/tests/test_voice_jobs.py services/api/tests/test_media_library_api.py`).
  - Web unit tests: 504/504 passed (`npm --prefix apps/web test`).
  - Typecheck: passed (`npm --prefix apps/web run typecheck`).
  - Lint: passed (`npm --prefix apps/web run lint`).

## Option to Allow Machine Draft Transcripts in Voiceover Dialog, 2026-09-14

- **Context & Request**:
  - In the Media Library Voiceover dialog (`BulkVoiceEditor` and `VoiceEditor` in batch mode), all clips with only unreviewed machine draft transcripts were unconditionally skipped with "Machine drafts are never voiced" and "Generate for 0" disabled.
  - The operator requested an option to allow machine drafts in this dialog.
- **Changes**:
  - `services/api/src/trendrelay_api/media_library_api.py`:
    - Added `allow_draft: bool = False` to `VoiceRequest`.
  - `services/api/src/trendrelay_api/voice_jobs.py`:
    - Updated `script_for` to support `allow_draft: bool = False`. When `allow_draft=True` and no `transcript_id` is supplied, it falls back to machine draft transcripts (ordered by `reviewed` first, then `machine`).
    - Passed `allow_draft` from `request` in `queue()`.
  - `services/api/tests/test_voice_jobs.py`:
    - Added `test_an_asset_with_only_a_draft_is_allowed_when_requested` (23/23 tests pass).
  - `apps/web/lib/i18n/messages/` (all 7 locales: `en`, `ar`, `fr`, `ja`, `ru`, `vi`, `zh`):
    - Added `voiceAllowDrafts`, `voiceAllowDraftsDescription`, `voiceBatchTranscriptNoteWithDrafts`, `voiceBatchMissingTranscriptTotal`, `voiceBatchDraftsCount`.
  - `apps/web/app/library/bulk-voice-editor.tsx`:
    - Added `draft` to `PreparedTarget` type and query extraction.
    - Added `allowDrafts` state and `<Switch>` component in dialog body.
    - Updated `readyTargets`, `draftCount`, `missing`, and `characters` to reactively include machine drafts when toggled on.
    - Passed active transcript ID and `allow_draft` in `POST` payload.
  - `apps/web/app/library/voice-editor.tsx`:
    - Added `allowDrafts` state, `<Switch>` control, dynamic batch note, and draft inclusion in `voicableTargets` and character billing for batch mode.
- **Verification**:
  - Backend: 23/23 tests passed (`pytest services/api/tests/test_voice_jobs.py`).
  - Web unit tests: 504/504 passed (`npm --prefix apps/web test`).
  - i18n parity: passed (`node --test apps/web/lib/i18n/messages.test.ts`).
  - Typecheck: passed (`npm --prefix apps/web run typecheck`).
  - Lint: passed (`npm --prefix apps/web run lint`).

## Fix: Duplicate React key error in EffectActivity (`edit_...`), 2026-09-14

- **Context & Request**:
  - Console error reported: `Encountered two children with the same key, 'edit_1ec923d89658cd7a6afcf3b9'. Keys should be unique so that components maintain their identity across updates.` triggered in `EffectActivity` in `apps/web/app/library/page.tsx`.
  - Root cause:
    1. In `apps/web/app/jobs-provider.tsx`, neither `announceMediaJobs` nor `refresh` (`results.flat()`) deduplicated jobs by `id`. If the same job appeared multiple times (e.g. from rapid announcements, overlapping fetches, or server responses), duplicate jobs entered the state.
    2. In `apps/web/app/library/page.tsx`, `EffectActivity` mapped `visible` and `matching` jobs to `<EffectActivityItem key={job.id} />` without deduplicating by `job.id`.
    3. In `services/api/src/trendrelay_api/jobs.py` (`list_job_records_for_kinds`), jobs query combining `unfinished` and `query` did not deduplicate by `id`, allowing race transitions where a job completed between both queries to be included twice.
- **Changes**:
  - `apps/web/app/jobs-provider.tsx`:
    - Added `dedupeJobs(jobs: BaseJob[])` helper to enforce single-occurrence by `id`.
    - Applied `dedupeJobs` in `announceMediaJobs` and `refresh` when combining job arrays.
  - `apps/web/app/library/page.tsx`:
    - In `EffectActivity`, deduplicated matching jobs by `id` using a `seenIds` Set before splitting into active/settled and rendering.
  - `services/api/src/trendrelay_api/jobs.py`:
    - In `list_job_records_for_kinds`, deduplicated records by `item.id` before serialization.
- **Verification**:
  - Pytest: 364/364 passed.
  - Typecheck: clean (0 errors).
  - Web unit tests: 504/504 passed.

## Fix: NameError: name 'get_settings' is not defined in submit_batch_render, 2026-09-14

- **Context & Request**:
  - Clicking "Apply to 24 items" in the Effect Editor with "Cover on-screen text" in the stack threw `Failed to fetch 0 of 24 were handled (0 queued); the rest were not queued.`
  - Root cause: `submit_batch_render` in `services/api/src/trendrelay_api/media_library_api.py` called `get_settings().media_ai_ocr_interval_seconds` when `needs_cover_text` was True, but `get_settings` was not imported in that scope, causing an unhandled `NameError` on the FastAPI route which resulted in a 500 error and `Failed to fetch` in the browser.
- **Changes**:
  - `services/api/src/trendrelay_api/media_library_api.py`:
    - Added `from trendrelay_api.config import get_settings` inside `needs_cover_text` block.
  - `services/api/tests/test_version_cuts.py`:
    - Added comprehensive end-to-end integration test `test_batch_render_auto_resolves_cover_text_regions` verifying `POST /effects/render-batch` with `cover_text` step across multiple assets, checking status 202, correct counts (`queued` vs `skipped`), and that the queued job's recipe has populated bounding regions.
- **Verification**:
  - Pytest: `test_version_cuts.py` 40/40 passed (including `test_batch_render_auto_resolves_cover_text_regions`).
  - Web: Typecheck 0 errors, 504/504 tests passed.

## Feature: Auto-apply detected speech & on-screen text, enable batch Cover On-screen Text, 2026-09-14

- **Context & Request**:
  - In the Media Library, clips with detected speech or on-screen text had machine drafts available, but operators had to manually click "Use draft" and save each clip individually.
  - Furthermore, running "Cover on-screen text" in batch across 42 clips failed with `Cover on-screen text has nothing to cover yet. Read the clip's on-screen text in the Library first, then add this.` because the batch effect runner passed empty regions to each clip instead of resolving regions per-asset from each clip's OCR data.
  - The OCR checkbox in `AutoTranscribe` and `BatchTranscribe` also defaulted to unchecked (`false`).
- **Changes**:
  - `services/api/src/trendrelay_api/media_library.py`:
    - Added `_auto_enrich` helper to queue speech and/or OCR enrichment after successful asset ingest when providers are ready.
    - Wrapped post-ingest call in `try/except` so enrichment failures never block asset ingestion.
  - `services/api/src/trendrelay_api/media_library_api.py`:
    - Added `_try_ocr_reading` helper (non-raising query returning reviewed first, then machine draft).
    - In `submit_batch_render`: added per-asset `cover_text` region resolution from the clip's OCR reading via `readable_lines(...)`, matching the `auto_face_object` architectural pattern. Skips items without OCR reading or text gracefully rather than failing the batch.
  - `apps/web/app/library/auto-transcribe.tsx`:
    - Default `ocr` mode to `ocrPossible` (checked by default for video/images when OCR provider is available, matching `speechPossible`).
  - `apps/web/app/library/batch-transcribe.tsx`:
    - Default `ocr` mode to `true` in `BatchTranscribe`.
  - `apps/web/app/library/effect-editor.tsx`:
    - For `param.kind === "regions"` on `cover_text` in batch mode, renders "Automatic per item" with explanation rather than a disabled "Read this clip" button.
    - Updated `automaticPreviewReason` to inform user that text regions are resolved automatically per item at queue time.
  - `apps/web/app/library/page.tsx`:
    - In `reviewedText` and `reviewedLanguage`, fall back to the machine draft so detected speech and on-screen text populate the form and apply by default without requiring manual per-clip clicks.
  - `services/api/tests/test_media_library_api.py`:
    - Added unit tests for `_auto_enrich` and `_try_ocr_reading` fallback.
- **Verification**:
  - Pytest: 237/237 passed, new tests 2/2 passed.
  - TypeScript typecheck: 0 errors.
  - Vitest / Node test runner: 504/504 passed.

## Fix: Caption language dropdown resets modal scroll and vanishes, 2026-09-14

- **Context & Request**:
  - In the Captions modal (`CaptionEditor`), scrolling down to the "Caption language" section and interacting with the language dropdown caused two problems:
    1. The dropdown's `SearchSelect` popover triggered `.ui-dialog:has(.search-select-popover) { overflow: visible; }` in `ui.css`, destroying the dialog body's scroll container and snapping `scrollTop` to 0 — the modal jumped to the top.
    2. On preview errors (`setPreview(null)`), `sourceLanguage` became `null`, `availableTranslations` dropped to `[]`, and the `<Select>` was unmounted entirely and replaced with a "No installed translation" note.
- **Changes**:
  - `apps/web/app/ui/ui.css` (lines 557–564): Added `.ui-dialog-wide:has(.search-select-popover)` selector alongside the existing `data-contain-select-popovers` opt-in, so all wide dialogs retain `overflow: hidden` / `overflow-y: auto` while a popover is open.
  - `apps/web/app/library/caption-editor.tsx`:
    - Added `data-contain-select-popovers="true"` to the root `caption-editor` div (belt-and-suspenders with the wide-dialog CSS rule).
    - Removed `setPreview(null)` from the error path so the last good preview (and its `source_language`) survives a transient failure.
    - Added `detectedLanguage` state (mirrored from `knownLanguage` ref on each successful preview) so `sourceLanguage` falls back to the last known language during render without accessing a ref (satisfies `react-hooks/refs` lint rule).
    - Normalized language-code matching (`zh-CN` ↔ `zh`) for `availableTranslations` filtering.
    - Kept `<Select>` always mounted when translation pairs exist (even if none match the current source), with a note below it instead of replacing it entirely.
- **Verification**: 504/504 web tests pass, typecheck clean, ESLint clean.

## Attribution Products Table & Toolbar Layout Optimization, 2026-09-14

- **Context & Request**:
  - The Attribution page products table and toolbar suffered from layout sizing and placement issues:
    - Filters and search bar flex-wrapped unpredictably whenever filter dropdowns changed width.
    - Search input lacked a search icon and quick-clear action button.
    - Bulk selection controls (counts, selection scope, copy links, fetch listings, campaign tagger, clear) were crowded into a flat flex row without visual separation.
    - Row expansion chevrons were positioned absolute at the far-right edge (`inset-inline-end: 1px`), floating loosely in wide columns away from the product name.
    - Offer counts and publish link counts were bare text without badge styling.
    - The table container had no bounding border or corner radius framing.
    - Duplicate `.product-campaign-tags` rules in `attribution.css` were overriding styled green pill badges.
- **Changes**:
  - `apps/web/app/attribution/product-table.tsx`:
    - Structured toolbar into 3 distinct full-width tiers using `.product-toolbar` CSS grid:
      1. `.product-search-bar`: `.product-search-input-wrap` with embedded `<ActionIcon name="search" />`, styled input, and clear (`×`) button; paired with the `.product-listing-filter` segmented tabs.
      2. `.product-filters`: Campaign, File/Import, Creator, Sub ID, Date filters, and "Clear filters" button.
      3. `.product-bulk`: Subdivided into `.product-bulk-selection` (pill badge count + scope), `.product-bulk-actions` (Copy links, Fetch listings), `.product-bulk-campaign-group` (campaign select + tag/untag buttons with subtle left divider), and clear button.
    - Moved the row expansion chevron (`.product-disclosure`) to the leading edge immediately before the thumbnail (`.product-thumb`), behaving as a standard row toggle handle.
    - Wrapped offer count and link count in `<span className="product-count-badge">`.
  - `apps/web/app/attribution.css`:
    - Added CSS grid layout for `.product-toolbar` with 8px row gap, preventing sibling tiers from shifting or rewrapping on filter select.
    - Added `.product-search-bar`, `.product-search-input-wrap`, `.product-search-icon`, and `.product-search-clear` styling.
    - Updated `.product-toggle` to flex with leading chevron, removing `position: relative` and trailing padding.
    - Converted `.product-disclosure` to flex child rotating in place (`rotate(90deg)`) on expand.
    - Styled `.product-count-badge` with subtle rounded pill framing.
    - Enhanced `.product-bulk-count` with pill badge border and tabular numbers.
    - Added subtle border and radius to `.product-table-scroll` container.
    - Merged duplicate `.product-campaign-tags` declarations, using design tokens (`var(--green-line)`, `var(--green)`, `var(--green-dark)`).
    - Refined 700px mobile media query for toolbar wrapping and touch button sizing.
- **Verification**:
  - `npm --prefix apps/web run typecheck`: 0 errors.
  - `npm --prefix apps/web test`: 504/504 passed (including stylesheet-scope and palette-guard tests).
  - `npm --prefix apps/web run lint`: 0 errors.

## Batch Transcribe Mode Explanations on Hover, 2026-09-14

- **Context & Request**:
  - In the Media Library "Transcribe" batch dialog, the user requested adding explanations on hover for what each reading mode does ("Transcribe speech", "Read on-screen text", "Recognise what it shows").
- **Changes**:
  - `apps/web/app/ui/tooltip.tsx`:
    - Added `side?: "top" | "bottom"` (defaults to `"top"`) and `className?: string` to `Tooltip` component.
    - Sets `data-side={side}` on wrapper and increased viewport padding check up to 160px for wider tooltips.
  - `apps/web/app/ui/ui.css`:
    - Added CSS rules for `.ui-tooltip[data-side="bottom"]` and positioning its arrow pointing up.
  - `apps/web/app/library/batch-transcribe.tsx`:
    - Added `MODE_DESCRIPTION` mapping defining clear explanations for `speech` (faster-whisper), `ocr` (RapidOCR), and `vision` (CLIP AI).
    - Wrapped each mode checkbox label with `<Tooltip side="bottom" content={MODE_DESCRIPTION[mode]}>`.
    - Added `<Info className="batch-transcribe-mode-info" size={14} aria-hidden="true" />` on the right side of each mode card with hover highlight.
    - Set `title={MODE_DESCRIPTION[mode]}` on the label as native browser tooltip fallback.
  - `apps/web/app/media-library.css`:
    - Styled `.batch-transcribe-modes > .ui-tooltip` to flex full width in the grid.
    - Vertically centered elements in `.batch-transcribe-mode` with `align-items: center`.
    - Set `.batch-transcribe-modes .ui-tooltip-content` max-width to 360px with `line-height: 1.4`.
    - Added styling for `.batch-transcribe-mode-info` with smooth opacity and color transition on hover/focus.
  - `apps/web/app/library/auto-transcribe.tsx`:
    - Added `title` attributes with the mode descriptions to the single-clip auto-transcription mode checkboxes for consistency.
- **Verification**:
  - `npm --prefix apps/web run typecheck`: 0 errors.
  - `npm --prefix apps/web test`: 504/504 passed (including stylesheet-scope tests).
  - `npm --prefix apps/web run lint`: 0 errors.

## Optional supplementary first comments via MCP for organic destinations, 2026-09-14

- **Context & Motivation**:
  - When a campaign destination's link placement is set to "No affiliate link" (`link_placement: "none"`), external AI agents connecting via MCP previously refused to draft first comments because `needs.first_comment = false` and `follow_up_deliverable = false`, reporting that first comments were not deliverable.
  - The operator requested: first comment drafting should be optional (not reported as missing in `needs`), but explicitly accepted and welcomed for supplementary information (e.g. styling tips, sizing advice, product details, or engagement prompts — never affiliate links).
- **Changes**:
  - `services/api/src/trendrelay_api/integrations/mcp/context.py`:
    - `_follow_up_landing`: for destinations with `link_placement == "none"`, marks `deliverable: True`, `accepts_first_comment: True`, `first_comment_optional: True`, with clear notes.
    - `get_post_context`: sets top-level `accepts_first_comment: True` and `first_comment_optional: True` when any destination accepts supplementary comments or delivers links. Keeps `needs["first_comment"] = False` (optional, not a blocker). Added `first_comment_guidance` in `added_by_the_campaign`.
  - `services/api/src/trendrelay_api/campaign_autopilot_api.py`:
    - In `_destination_view`, marks `follow_up_deliverable = True` when `item.link_placement == "none"`.
  - `services/api/src/trendrelay_api/integrations/mcp/server.py`:
    - Updated system instructions and tool descriptions (`write_first_comment`, `write_post_copy`) to explicitly accept optional supplementary first comments when link placement is "none".
  - SOPs (`SOP/campaigns/fill-needs-copy.md`, `SOP/campaigns/editorial-quality.md`, `SOP/MCP_GUIDE.md`):
    - Documented optional supplementary first comments and their non-affiliate nature.
  - Web UI (`apps/web/app/campaigns/autopilot-panel.tsx`):
    - Updated helper text under `first_comment` textarea for "No affiliate link" campaigns.
- **Verification**:
  - `services/api/tests/test_mcp.py` (156/156 passed) including new `test_get_post_context_when_destination_link_placement_is_none`.
  - `services/api/tests/test_campaign_autopilot_api.py` (98/98 passed).
  - TypeScript Typecheck (`npm run typecheck`): 0 errors.
  - Ruff linter: passed with 0 errors.

## WoopSocial TikTok Publishing & Privacy Level Mapping Fix, 2026-09-14

- **Problem & Root Cause**:
  - Uploading/publishing to TikTok via WoopSocial failed in TrendRelay with `api.woopsocial.com: HTTP 400`, but media was successfully uploaded and appeared in WoopSocial's Media Library.
  - When the user manually finished the post in WoopSocial UI, the caption had to be re-entered and Privacy Level was unset until manually picked.
  - Inspection of the live WoopSocial OpenAPI specification (`https://api.woopsocial.com/v1/openapi.yaml`) revealed that `TikTokFields` strictly requires:
    - `postType`: `"VIDEO"` or `"PHOTO"`
    - `postMode`: `"DIRECT_POST"` (required for direct delivery)
    - `privacyLevel`: `"PUBLIC_TO_EVERYONE"` or `"SELF_ONLY"` (required when `postMode=DIRECT_POST`)
    - `allowComment`: `bool` (required by API contract)
    - `allowDuet`: `bool` (required by API contract; `true` for video, `false` for photo)
    - `allowStitch`: `bool` (required by API contract; `true` for video, `false` for photo)
    - `isYourBrand`: `bool` (required by API contract)
    - `isBrandedContent`: `bool` (required by API contract)
    - `autoAddMusic`: `bool` (required by API contract; `true` for photo, `false` for video)
    - `isAiGeneratedContent`: `bool`
  - In `services/api/src/trendrelay_api/integrations/publishing.py`:
    - `_woopsocial_publish` only supplied `postType`, `privacyLevel`, and `isAiGeneratedContent`, omitting all other required fields and `postMode`.
    - `_woopsocial_validate` only passed `platform` and `socialAccountId`, missing all per-platform fields and checking only `validationErrors` instead of `errors` returned by `ValidatePostResponse`.
    - `_error_message` did not check `error_message` or format `validationErrors` / `errors`, causing the API's descriptive error details to be replaced by generic `HTTP 400`.
- **Fixes**:
  - Extracted `_woopsocial_account_entry` in `publishing.py` to build compliant platform data for both `_woopsocial_publish` and `_woopsocial_validate`.
  - Supplied all required TikTok fields: `postMode="DIRECT_POST"`, `privacyLevel` mapped from `request.visibility` (`"public"` -> `"PUBLIC_TO_EVERYONE"`, `"private"` -> `"SELF_ONLY"`), `allowComment=True`, `allowDuet=not carousel`, `allowStitch=not carousel`, `isYourBrand=False`, `isBrandedContent=False`, `autoAddMusic=carousel`, `isAiGeneratedContent=bool(request.made_with_ai)`.
  - Updated `_woopsocial_validate` to inspect `errors` or `validationErrors` while filtering missing `MEDIA`.
  - Enhanced `_error_message` to extract `error_message` and format field-level validation errors so errors from provider APIs are transparent.
- **Verification**:
  - Tested live validation against `api.woopsocial.com/v1/posts/validate`: returns `isValid: True`, `errors: []` with real uploaded media ID.
  - Pytest: `test_publishing_providers.py` (175/175 passed), `test_campaign_queue_publish_now.py` (5/5 passed), 220/220 publishing tests pass.
  - TypeScript Typecheck (`npm run typecheck`): 0 errors.
  - Frontend Test Suite (`npm test`): 504/504 passed.

## Mobile library spacing, 2026-09-14

- Commit `002b9ab`: tightened vertical spacing on mobile (≤980px and ≤480px)
  between the heading card, search row, and category tab bar.
- `.library-heading` margin-bottom reduced from 12→8px (≤980px) / 6px (≤480px),
  padding from 16→12px / 10px.
- `.library-browser` gap reduced from 10→6px, padding from 12→10px.
- `.library-browser-sticky-controls` gap set to 4px on mobile.

## Media import dialog and batch upload, 2026-09-14

- Added a full-featured `MediaImportDialog` component at
  `apps/web/app/library/media-import-dialog.tsx` with two tabs:
  - **Upload files**: drag-and-drop zone, file picker, folder picker with staged
    file list showing editable titles, type icons, sizes, per-file progress bars,
    and status badges (queued/uploading/done/duplicate/error).
  - **Local file/folder paths**: textarea for pasting local paths; supports
    individual files and recursive folder scanning.
- The `+ Import media` button sits to the right of the Search button in the
  library toolbar. On screens ≤480px the label text hides, showing only the `+`
  icon. At ≤430px, browser padding tightens further to preserve screen real
  estate. The legacy bottom-of-page import section was removed.
- Empty library state now shows a centered CTA button to open the import dialog.
- Backend endpoints added in `media_library_api.py`:
  - `POST /imports/upload` — multipart file upload, streams to
    `.data/downloads/manual/`, validates extension, creates ingest job.
  - `POST /imports/batch` — accepts `paths` list and/or `folder_path`, recursively
    scans directories for media, creates ingest jobs per file.
  - `approved_media_path()` in `media_library.py` now supports `allow_dir=True`
    for directory validation.
- i18n keys `importMedia` and `importMediaDescription` added to all 7 locales
  (en, ar, fr, ja, ru, vi, zh).
- Tests: 23/23 backend tests pass including `test_upload_media_import` and
  `test_batch_media_import`. TypeScript typecheck passes clean; 504/504 web unit tests pass.
- Fix: `apiFetch` in `apps/web/app/auth-provider.tsx` previously forced `Content-Type: application/json`
  on all requests with bodies. For `FormData` (multipart), this stripped the browser's
  boundary header and caused FastAPI to return `422 Unprocessable Content` (missing file).
  Added `hasCustomBody` guard checking for `FormData`, `Blob`, and `ArrayBuffer`.
- Fix: Left-aligned `.library-staged-title-input` flush with `.library-staged-meta` in
  `media-library.css` using `margin-left: -5px` to negate the transparent border/padding.
- Fix: Scoped dropzone dragging class to `.library-dropzone.dropzone-dragging` to avoid
  collision with `ui.css` `.filter-chip-strip.dragging`.
- Batch notification grouping: `POST /imports/upload` accepts `batch_id` and `batch_total`;
  `POST /imports/batch` generates a batch marker. `jobs-provider.tsx` preserves batch metadata
  and titles batches as `Import: N media items`. `GlobalNav`'s `groupNotifications` folds
  the batch into a single card with live progress bar (`X of N done`), item count badge,
  and completion state, preventing notification spam. Also includes a backwards-compatible
  minute-based grouping for unmarked manual imports.
- Real-time library refresh: `MediaImportDialog` calls `onItemQueued` as items are added
  (throttled to 1.2s + final flush), triggering `refresh()` and `refreshGlobalJobs()` on the
  library page. This populates `jobs` state immediately, activating the 2.5s polling loop
  so newly ingested assets and status updates appear in the library live without waiting
  for the dialog to close.
- Branch: `publishing-engines`. Commits: `a5d2cca`, `b76c57c`, `c7443bf`.

## Douyin follow-up: bookmarklet correctness and missing-source reporting

- The first bookmarklet implementation was too broad: it selected all page
  video links, including unrelated footer recommendations. Replaced it with
  `apps/web/lib/douyin-bookmarklet.ts`: only Douyin `/user/` pages and the loaded
  profile region (`user_detail_element`, fallback `data-e2e=user-post-list`),
  excluding hidden/footer anchors. Canonicalizes exact video URLs and uses a
  fresh v2 storage key to avoid old contaminated captures. Query parameters
  don't split a profile's capture. This remains click-after-scrolling, not a
  background observer; users must click the bookmark after loading more posts.
- Clipboard rejection now opens a selectable text panel and does not falsely
  announce “Copied”. Malformed/blocked storage is handled, and a capture over
  the current 400-URL API limit stops without silently truncating the list.
  Six executable VM/browser-fixture tests validate the generated JavaScript,
  line separators, scope, accumulation, storage and clipboard failures.
  These are not a live Chrome bookmark-installation test; that remains to do.
- The 20-link trial `download_062a9ad83033bbce` has terminal `succeeded`, three
  artifacts and an empty `source_errors` list. Library count is 42 for Thalia.
  Found why its failures vanished: the job loop counted code-3/empty sources
  separately but omitted them from `source_errors` whenever other sources
  produced files. Updated the loop to retain each unsuccessful/empty source's
  detail. Dashboard now marks completed jobs with source errors as partial and
  offers expandable reasons. Existing historical rows lack those details and
  were not rewritten. Do not infer the exact cause for the other 17 links from
  the old success record; a future authorized retry can now preserve evidence.
- Seven focused Python worker tests pass (mixed success/exit-3/empty exit-0,
  retained files, already-complete, partial profile). Typecheck and focused
  ESLint pass. The test factory now isolates CONNECTION_STATUS_FILE in tmp_path;
  an initial test hit the real status file, and that synthetic status was
  restored to the last verified anonymous-connected state. Cookies unchanged.
- Still open: live Chrome bookmarklet QA, automatic browser fallback, and
  obtaining the remaining profile posts. Per-batch import is now implemented:
  the shared Import links dialog validates/canonicalizes/deduplicates up to 400
  direct video URLs and submits them to the scoped API child-job endpoint. The
  child retains the original source group, media kinds, parent id and Library
  provenance, so Fetch missing/Open Library remain cumulative. No stealth,
  fingerprint spoofing, or login-gate bypass was added.
- Fetch missing now retries every known direct video link in the grouped union
  through the parent import endpoint, rather than only the original seed list;
  profile-only jobs retain their original profile retry behavior.
- The root handover is git-ignored by repository convention. The matching
  resumable note in `docs/agent-handover.md` is tracked for future checkouts.

## Douyin profile download recovery and manual link fallback, 2026-09-05

- The original report was a profile showing `39/369` while the first run only
  imported three videos. Recovery reconciled sibling run folders and queued
  their retained files through the normal Library ingest path. The latest
  durable batch `download_f02063a692691da8` has 40 provider artifacts with no
  library errors; the creator attribution is 39 assets for `塔塔Thalia` plus
  one collaboration asset credited to `14cc`. The Library currently contains
  42 videos for `塔塔Thalia` after the manually collected individual links were
  downloaded. Do not call the batch's 40-file count a creator count.
- The later bookmarklet capture supplied 384 direct links. The first pass
  fetched 299 artifacts with 45 source errors; two incremental retries recovered
  all five remaining failures. Provider storage now has 366 unique posts for
  this creator, and the Library has 366 `video` assets for `塔塔Thalia`; all
  related ingest jobs are terminal. The profile declares 369, leaving three
  posts not exposed by the current Douyin session/provider response.
- The saved session is anonymous and automatic: `.data/douyin/cookies.json`
  contains the required visit cookies and `.data/douyin/connection-status.json`
  reports `connected` with “Signed-out session saved”. Login is optional and is
  offered only in the left connection panel. The per-batch action is always
  **Fetch missing**; it is incremental and skips files already retained.
- Current external limitation: Douyin's signed API returned HTTP 403 for the
  profile listing with this anonymous session. The rendered Chrome page is
  different: it exposes 20 post links and then the explicit `登录后查看更多作品`
  gate. A service-error load can expose no links at all. These are external
  responses, not proof that the profile has only 20 or 40 posts.
- `apps/web/app/dashboard.tsx` now adds **Import links** to each individual
  download session. Its shared dialog contains a reviewable paste box, live
  unique-link count, invalid-input feedback, and an Import action; a collapsible
  section contains the bookmarklet and short instructions. The bookmarklet only
  reads `/video/<id>` anchors already rendered by Douyin, stores a cumulative
  deduplicated list in that browser's `localStorage`, and copies it. The dialog
  sends only links after the operator confirms; no cookies are transmitted.
  Styling lives in `apps/web/app/console.css`.
- The current Chrome observation used the signed-out profile URL:
  `https://www.douyin.com/user/MS4wLjABAAAAde6Wgqq4LSxzTsQueTz3BgTXSWQ7JhhBqn3IyZIaxWE`
  (`showSubTab=video&showTab=post`). It showed 20 visible links and the login
  gate. A 20-link batch was queued as `download_062a9ad83033bbce` and produced
  three new artifacts; check its terminal result before assuming all visible
  links were accepted by the provider.
- Focused frontend checks: `npm run typecheck --workspace=@trendrelay/web`
  and `npm run lint --workspace=@trendrelay/web` pass. The new
  `apps/web/lib/download-retry-action.test.ts` passes when run directly with
  `node --experimental-strip-types`; it covers incremental retry semantics,
  left-panel login placement, and the manual bookmarklet. The import parser
  tests in `apps/web/lib/douyin-import-links.test.ts` pass as well. The focused Python
  test requires the approved pytest temp-directory execution on this Windows
  host; a prior parametrized run passed the ordinary case and exposed only a
  test expectation that was corrected to use the selected request URLs.
- Do not restart the dev servers. Do not kill or rewrite the stale
  `download_fce241ce769a4450` row: it is an old running record with no lease
  progress and its last error says Douyin returned no media. Do not expose
  cookie values. If Douyin later accepts a signed-in or newly refreshed
  session, use **Fetch missing** on the original profile batch; the provider
  and Library dedupe retained files.

## Resume here — campaign control room, 2026-09-05

Current user requests: make warning badges open the affected attempts; add
compact dividers; remove the awkward outer rectangle around warning pills;
document the work thoroughly so a later session can resume without guesswork.

### Delivered commits and implementation map

- `ca28197` — pipeline tooltip explanations and count-label contrast.
- `25c8091` — warning badges open a scoped, read-only dialog.
- `561c65a` — compact separators for legend, metric columns, and campaign table.
- `9c37f61` — removed redundant warning-pill border; TypeScript passes and
  browser computed styles confirm transparent border/background, with unchanged
  28px desktop hit-area height and shared keyboard-focus styling.
- Final small follow-up changes warning button from shared `quiet` to `link`:
  `quiet` intentionally has a border, producing a rectangle around the existing
  pink badge. `link` removes that extra chrome and retains shared focus/hover
  states and the same hit area. Do not globally remove quiet-button borders.

Files to start with:
- `apps/web/app/campaigns/manage/page.tsx`: control room, independent campaign
  name links and warning actions (no nested links), warning dialog selection,
  and focus restoration to the originating badge with preventScroll.
- `apps/web/app/campaigns/manage/warnings-dialog.tsx`: shared Radix Dialog,
  cancellable read, 50-attempt pages, retry-on-read-error, date scope, account,
  error, frozen copy disclosure, and uncertain-delivery caution. Pagination is
  hidden for a single page. Closing preserves underlying filters and scroll.
- `apps/web/app/campaigns/manage/manage.module.css`: chart/table dividers and
  compact wrapping warning list; shared semantic colors, logical CSS properties.
- `services/api/src/trendrelay_api/campaign_management_api.py`: GET
  `/{campaign_id}/management/warnings` under the workspace campaigns prefix.
  Requires starts_at/ends_at from the dashboard snapshot, limit 1–100 (50
  default), offset >=0. Checks membership and campaign ownership; limits the
  requested interval to 91 days. Filters failed/uncertain states by updated_at
  before pagination, with deterministic updated_at/id ordering. No planner,
  provider call, retry, mutation, or publishing is triggered by this read.
- `services/api/tests/test_campaign_management.py`: snapshot count parity,
  old/out-of-range and other-campaign exclusion, pagination, input validation,
  non-member 404. All 4 tests pass (in-memory DB, no live campaign writes).
- All seven locale dictionaries include campaignWarnings/campaignPipeline.
  Durable chart and drilldown rules are in `SOP/design/data-visualization.md`.

### Verification and known boundaries

- TypeScript, ESLint, Ruff, and production build passed for the main work.
  Browser checks used populated localhost:3001/campaigns/manage at desktop,
  320px, 390px, and 768px. Live Her Lifestyle dialog returned the same 4 warning
  attempts as its badge, including actual errors and post copy. No live data
  was edited. Keyboard focus returns to the badge on closing; viewport reset.
- Warning counts are delivery attempts, not unique queue posts: one post can
  have multiple failures. The dialog therefore labels its count as attempts.
- The dialog is deliberately read-only. It exposes the affected attempts and
  reasons rather than dropping the user on the generic campaign page. It does
  not yet offer direct editing/retry; uncertain delivery must be checked with
  the provider before any retry. No promise of resolving provider errors was
  made in this UI task.
- Snapshot date bounds are preserved, but data is current: if a failure is
  reconciled after the snapshot, the drilldown can legitimately have fewer
  results. Empty results are explicit. Future improvement, if requested: refresh
  the dashboard count after reconciliation, not an invented stale warning.
- Existing whole-app localization coverage is about 71%; do not claim 100%.
  New strings are translated, but older control-room labels remain English.
- Branch is `publishing-engines`. There are extensive unrelated dirty files
  from earlier work (including shared UI, campaign editor, MCP, and backend).
  Preserve them. Stage only scoped files; never commit the local database or
  bulk-stage the whole worktree. No push was requested for this set of changes.
- Git index writes need the approved elevated git tool permission in this
  environment. AGENT_HANDOVER.md is ignored and must stay uncommitted.

All current warning-flow, divider, and badge-outline requests are implemented
and committed. No remaining blocker for this scope; no push was performed.
Future work should follow the user's compact-density preference: no redundant
bars or controls, readable text, shared design controls, and mobile tap targets.

## Compact comparison dividers, 2026-09-05

- Added semantic-token dividers between legend groups, pipeline metrics, and
  desktop campaign table columns. Existing row heights stay unchanged; the
  single-column mobile legend uses horizontal separators rather than empty
  column borders. Verified populated layouts at 320, 390, 768, and desktop.
- Production build passes. Warning dialog closing restores focus to its badge
  without scrolling, and single-page results hide unnecessary pagination.

## Campaign warning drilldown, 2026-09-05

- Warning badges now open a compact read-only dialog for that campaign and the
  control-room snapshot's exact dates, rather than inheriting the generic row
  link. It shows failed/uncertain attempts, account, reason, timestamp, and
  expandable frozen copy. Closing retains the underlying filters and scroll.
- New membership-scoped management/warnings GET filters before pagination
  (50 default / 100 maximum) so recent successful jobs cannot hide old warnings.
  No retry or publish side effects. Count and page parity, date bounds, other
  campaign exclusion, pagination bounds, and non-member denial are tested.
- Verified live Her Lifestyle warnings in the browser on desktop and mobile.
  Typecheck, lint, Ruff, and all four campaign-management tests pass.

## Campaign pipeline tooltips and contrast, 2026-09-05

- Published, scheduled, and planned comparison bars now use the shared tooltip
  on focusable controls. Hints explain the exact publication date range,
  committed/forecast schedule and next posting time, or approved queue semantics.
- Exact localized numbers sit outside bar fills, using the normal high-contrast
  text token. Mobile stacks each number above its track without horizontal
  overflow. New tooltip messages are present in all seven locale dictionaries.
- Added these counting-rule, tooltip-accessibility, and contrast requirements
  to `SOP/design/data-visualization.md` for future chart work.
- Verified populated desktop and mobile layouts in the in-app browser, including
  click/focus tooltip content and no mobile horizontal overflow. Typecheck,
  ESLint, and production build pass. The localization scanner completes but
  still reports pre-existing overall coverage debt (71%); this is not a claim
  that the rest of the control room is fully localized.
- Unrelated working-tree changes were preserved; no live posts were modified.

## Campaign management scheduled-count parity, 2026-09-05

- `/campaigns/manage` now defines **scheduled** exactly as Campaign Overview
  does: the same seven-day planner forecast plus already committed upcoming
  executions. It no longer reports only durable executions, which made active
  campaigns with a full outlook display zero.
- The next-scheduled timestamp is also derived from both committed and forecast
  work. A regression test compares the management result directly with the
  individual campaign preview rather than duplicating an expected constant.
- The scheduler now preloads other-campaign account load and asset rest history
  once per outlook. This removes the former query-per-candidate-per-slot pattern
  without changing caps or cross-campaign rest rules. On the populated local
  workspace, representative previews fell from 8–15 seconds to 0.3–1.4 seconds.
- Live parity check: Petal Poetry and NightClubzz each returned 30 upcoming in
  both views; Tủ Xinh Của Nàng returned zero in both because its planner had no
  valid upcoming work. The focused management and scheduler suites passed 89
  tests.

## Campaign comparison chart readability, 2026-09-05

- Reworked `/campaigns/manage` after the first dense comparison layout proved
  too compressed. The daily trend now owns the full card width with an aligned
  three-column legend; the published/scheduled/planned pipeline is a separate
  full-width matrix below it. Its three metrics use independent, explicitly
  disclosed scales so large ready queues do not flatten published counts.
- Chart labels are at least 11px on desktop and 10px on compact mobile. Lines
  are clipped to the plot. The stretched SVG no longer renders focus circles;
  fixed 8px HTML dots now follow the same pattern as Campaign Overview.
- Hover, tap, and keyboard inspection show a bounded tooltip containing the
  date plus every visible campaign's views, engagements, and post count.
  Pipeline bars expose exact values and all three campaign totals.
- Added the reusable chart standard at
  `SOP/design/data-visualization.md` and made it mandatory from `DESIGN.md`.
  It covers chart choice, honest scales, typography, plot clipping, forgiving
  pointer targets, useful tooltips, keyboard support, responsive layout, and QA.
- Live QA: default desktop and 390x844 layouts are aligned and readable; phone
  width has no horizontal page overflow. Tooltip and fixed markers were
  exercised at both widths.

## All-campaign control room, 2026-09-05

- Added `/campaigns/manage`, reached through the compact **Campaign overview →**
  link in the Campaign workspace. Its reciprocal link is **← Campaign
  workspace**; there is no extra global tab or ambiguous two-button switch.
- The control room compares every campaign in one compact responsive list:
  status, destinations/products, published and upcoming posts, stored views and
  engagement, queue readiness, held approvals, and recent delivery warnings.
  A shared-scale time-series chart compares the leading visible campaigns by
  daily views, engagement, or published posts. Hover, tap, and keyboard focus
  show the date plus every campaign's views, engagements, and posts. A compact
  companion chart compares published, scheduled, and planned/ready workload.
  Range, status, sort, and chart metric survive reloads.
- The right rail is a bounded workspace-wide approval inbox. Each row opens the
  exact campaign at `#campaign-approvals`; zero work renders a small empty state.
- Backend endpoint:
  `GET /api/workspaces/{workspace_id}/campaigns/management`. It aggregates in
  one request, reads stored provider snapshots only, bounds settled execution
  history to the chosen calendar range, and caps returned approval rows at 100.
- Verification: management API tests passed; Ruff, web TypeScript, ESLint, and
  the production Next.js build passed. Live checks covered the populated
  127.0.0.1 workspace at the default desktop viewport and 390×844. The chart
  tooltip was exercised by pointer/tap, a clean browser run had no errors, and
  the phone layout had no horizontal page overflow.

## Campaign Queue "Publish Now" & Repeat Setting Policy, 2026-08-30

- **Feature & User Request**:
  - Added an immediate "Publish now" option for posts in campaign rotation (both individual row action and batch toolbar action).
  - Enforced campaign setting **"Let a post go out more than once"** (`repeat_posts: bool`, `min_recycle_days: int`):
    - When `repeat_posts` is **Off** (`False`): each post goes to each account once. Subsequent publish requests skip accounts that already received the post, unless explicitly confirmed with `force: true`.
    - When `repeat_posts` is **On** (`True`): posts respect `min_recycle_days` rest interval unless `force: true`.
  - Optimized screen real estate on desktop and mobile (`@media (max-width: 700px)`) with compact row actions and wrapping.
- **Backend Implementation**:
  - `publish_queue_item_now` and `batch_publish_queue_items` in `services/api/src/trendrelay_api/campaign_runner.py`:
    - Validates caption and media readiness.
    - Resolves eligible campaign destinations, format compatibility (`carousel_fits_destination`, `video_fits_platform`), delivery quotas, and product offer matches.
    - Accurately checks `item.last_posted_by_destination` timestamps against `repeat_posts` and `min_recycle_days`.
    - Creates `PublicationExecution`, dispatches immediate publishing job via `_publish_execution(..., delivery_override="now")`, and records deployment rotation via `record_published`.
  - API Endpoints in `campaign_autopilot_api.py`:
    - `POST /api/workspaces/{workspace_id}/campaigns/{campaign_id}/queue/{item_id}/publish`
    - `POST /api/workspaces/{workspace_id}/campaigns/{campaign_id}/queue/publish`
    - Serializer `_queue_view` includes `last_posted_by_destination`.
- **Frontend Implementation**:
  - `apps/web/app/campaigns/autopilot-panel.tsx`:
    - Added `publishQueueItem` and `batchPublishQueue` handler functions with contextual confirmation for repeat rules.
    - Added `Publish now` button in `.campaign-queue-actions` per row and `Publish now (N)` button in batch toolbar `.campaign-queue-bar`.
    - Added `last_posted_by_destination?: Record<string, string>` to `QueueItem` interface.
  - `apps/web/app/console.css`:
    - Optimized `.campaign-queue-actions` and mobile rules (`@media (max-width: 700px)`) to preserve clean layout and prevent overflow.
- **Verification**:
  - Pytest: `test_campaign_queue_publish_now.py` (4/4 passed), full campaign suite (92/92 passed).
  - TypeScript Typecheck (`npm run typecheck`): 0 errors.
  - Frontend Test Suite (`npm test`): 390/390 passed.

## Campaign Post Preview Vertical Video Aspect Ratio Fix, 2026-08-30

- **Root Causes**:
  - In `apps/web/app/publish/composer.tsx`, `PostPreview` default aspect ratio fell back to `4 / 5` unless dynamically measured from media or marked strictly as `story`. Short-form video platforms (TikTok, Reels, Shorts) and video posts were not defaulting to `9 / 16`.
  - In `apps/web/app/styles.css`, `.post-preview[data-width="mobile"]:has(.preview-surface) .post-preview-frame` set `inline-size: 100%`. Combined with height capping (`max-block-size`), forcing 100% width overrode `aspect-ratio`, distorting TikTok/Reels frames into wide/squashed horizontal boxes.
  - In `.campaign-outings`, `.post-preview-frame` had `max-block-size: min(28vh, 220px)` while `inline-size` remained hardcoded with a calculation expecting 440px height (`calc(min(56vh, 440px) * var(--preview-ratio))`), shrinking height without scaling width and flattening vertical videos into square-ish boxes.
- **Fixes**:
  - Centralized height budgeting through `--preview-max-height` (default `min(56vh, 440px)` in `.post-preview`, `min(45vh, 320px)` in `.campaign-outings`, and `min(34vh, 260px)` in `.campaign-approval-list`).
  - Sized `.post-preview-frame` via `inline-size: min(100%, calc(var(--preview-max-height, min(56vh, 440px)) * var(--preview-ratio, 4 / 5)))` and `max-block-size: var(--preview-max-height, min(56vh, 440px))` with `aspect-ratio: var(--preview-ratio, 4 / 5)`.
  - Updated `PostPreview` in `composer.tsx` so short-form platforms (TikTok, Reels, Shorts, Stories) and video posts default to `9 / 16` (`--preview-ratio: 9 / 16` / 0.5625) and refine to exact measured dimensions upon poster/video load.
  - Removed disruptive `inline-size: 100%` and mobile `aspect-ratio: 1 / 1` overrides.
- **Verification**:
  - Full TypeScript typecheck (`npm run typecheck`): 0 errors.
  - Full web unit test suite (`npm test`): 390/390 tests pass.

## Immediate full-resolution MCP image imports, 2026-08-30

- Root cause: MCP images used the same single durable-worker lane as Douyin
  videos, proxy renders, and effects. The two reported NightClubzz jobs were
  genuinely queued, never claimed, and error-free; an earlier MCP image had
  waited about 53 minutes before that general worker reached it.
- New MCP image uploads still create a durable audit/job record but claim and
  finish it inside the upload tool call. A normal JPEG/PNG/WebP now returns
  `asset_id` immediately. Videos remain asynchronous because proxying a long
  clip can exceed an MCP call timeout. A narrow worker-claim race falls back to
  the ordinary pollable job rather than duplicating processing.
- Full size means the exact uploaded bytes and dimensions are retained as the
  immutable `original`; only a separate 720px thumbnail is derived for Library
  browsing. Tool descriptions and SOPs explicitly tell agents not to downscale,
  recompress, or submit a second WebP merely to speed an image import.
- Live recovery: `media_d538...` imported its exact 2,938,292-byte 1254x1254 PNG
  as `asset_e7d841...` in about 1.08 seconds including Python startup (roughly
  0.44 seconds ingest). It is attached to existing draft `queued_457be...`; no
  duplicate draft was created. The separate WebP fallback also finished when
  the worker resumed, but the draft uses the PNG original.
- Documentation: MCP guide quick path updated; campaign media SOP is version 9.
- Verification: Ruff and diff checks pass; focused MCP suite 138/138 passes.

## ChatGPT-compliant MCP file inputs, 2026-08-30

- Audited `upload_media` / `upload_image` against the official OpenAI plugin
  file-input contract. Both tools retain `_meta["openai/fileParams"]` and now
  expose a strict reusable `OpenAIFile` schema with exactly four declared
  string properties: required `download_url` + `file_id`, optional `mime_type`
  + `file_name`, and no undeclared properties.
- The upload paths remain deliberately redundant: ChatGPT can materialize an
  image-generation result into the typed `media` file parameter; any external
  AI that has bytes but no transferable file object can use `media_base64`; a
  direct public HTTPS file can use `media_url`.
- Uploads verify byte signatures on attachment, URL, and base64 routes. Images
  retain the 25 MB cap even through generic `upload_media`; videos retain the
  512 MB cap. HEIC/AVIF ISO containers are not misfiled as MP4, and false MIME
  claims are rejected before a file or ingest job is created.
- `SOP/MCP_GUIDE.md` and `SOP/campaigns/add-post-with-media.md` version 8 now
  contain the compact image-generation -> upload -> status -> Library asset ->
  campaign draft sequence, including client-handoff limitations and the rule
  never to manufacture a file object or substitute a different asset.
- Verification: Ruff passed. The focused MCP suite passed 138/138 tests; the
  ordinary sandbox run only failed pytest temp-directory cleanup under Windows
  ACLs, while the same suite passed outside that restriction.

## MCP Base64 Media Upload & Campaign Intake SOP, 2026-08-29

- **Context & External AI / ChatGPT Feedback**:
  - ChatGPT and external AI assistants generating images inside sandboxed runtime environments do not have public HTTPS URLs or standard chat attachment bridges to pass to `upload_media`.
  - When trying to pass sandbox paths or unregistered file identifiers, the server returned `UNREGISTERED_FILE_REFERENCE`.
- **Implementation in TrendRelay MCP**:
  - **`upload_media` and `upload_image` Base64 Support**:
    - Added `media_base64` and `image_base64` parameters accepting standard base64 strings or `data:<mime>;base64,<data>` URLs in `intake.py` and `server.py`.
    - Implemented signature-based file sniffing (`_sniff_media_type`) and size validation (`MAX_IMAGE_BYTES = 25 MB`, `MAX_VIDEO_BYTES = 512 MB`) before decoding the full payload.
    - Verified that mislabelled types (e.g. data URLs declaring wrong MIME) are safely identified by byte magic signatures.
  - **`set_post_media` Incremental Carousel Assembly**:
    - Added `append: bool = False` to `set_post_media` in `writes.py` and `server.py` allowing external AIs to attach images to a draft carousel one at a time without having to know or resend earlier assets.
  - **SOP Documentation**:
    - Updated [`SOP/campaigns/add-post-with-media.md`](file:///s:/Vibe%20Coding/TrendRelay/SOP/campaigns/add-post-with-media.md) (version 7) detailing the three intake routes (chat attachment, direct public HTTPS URL, raw bytes via `media_base64`), incremental carousel assembly, and draft review boundaries.
    - Updated [`SOP/MCP_GUIDE.md`](file:///s:/Vibe%20Coding/TrendRelay/SOP/MCP_GUIDE.md) control tower to guide external AIs directly to `media_base64` when handling sandbox-generated files.
- **Verification**:
  - `pytest tests/test_mcp.py tests/test_mcp_upload_references.py tests/test_tool_documentation.py`: All 142 tests passed.


## Publishing Notification State & Schedule Label Synchronization, 2026-08-29

- **Issue**:
  - In the Publish tab, a post was marked as `SCHEDULED` in the Upcoming Posts rail, while in the Notifications drawer the corresponding publishing job (`publish_...`) displayed as `Running` with a blue badge for ~39 minutes.
- **Root Causes**:
  1. `scheduleLabel` in `composer.tsx` had a default fallback that treated every unknown/in-progress state as `Scheduled`, so while a background job was `running` or `queued`, the UI prematurely rendered the badge as `Scheduled`.
  2. `jobs-provider.tsx` did not propagate `stalled: Boolean(j.stalled)`, `startedAt`, or `progress` for publishing jobs. When a worker process restarted and the job's lease lapsed, the notification drawer did not recognize the stalled state and continued displaying `Running`.
  3. Notification titles for publishing jobs were uniformly titled `Publish: <title>`, lacking distinction between scheduled future posts and immediate posts across their `Scheduling`, `Scheduled`, `Publishing`, and `Published` phases.
- **Fixes**:
  - Updated `jobs-provider.tsx` to map scheduled vs immediate publish jobs with contextual titles (`Scheduling: <title>`, `Scheduled: <title>`, `Publishing: <title>`, `Published: <title>`, `Publish failed: <title>`), and forwarded `stalled`, `startedAt`, `assetId`, and `progress`.
  - Refined `scheduleLabel` in `composer.tsx` so in-progress jobs render as `Scheduling` / `Publishing` (tone: info) or `Queued` (tone: neutral) while dispatching, transitioning to `Scheduled` / `Published` only upon confirmed job success.
- **Verification**:
  - Full TypeScript typecheck (`npm run typecheck`): 0 errors.
  - Full web test suite: 390 passed.


## DirectML GPU Suspension Recovery & Face Effects CPU Fallback, 2026-08-29

- **Root Cause of `887A0005 The GPU device instance has been suspended` in 200-item Batch Notifications**:
  - Processing very large batches (e.g. 200+ media items) with `onnxruntime-directml` on Windows caused GPU VRAM exhaustion / TDR timeout (`DXGI_ERROR_DEVICE_REMOVED` / `0x887A0005`).
  - When the DirectML GPU instance was suspended, the cached ONNX session remained in memory. Every subsequent item in the queue attempted to run against the dead GPU device and failed with unhandled `ONNXRuntimeError` notifications.
- **Resilient Recovery in `face_detect_onnx.py`**:
  - Implemented `_is_device_lost(error)` detecting `887A0005`, `DXGI_ERROR_DEVICE_REMOVED`, `The GPU device instance has been suspended`, and VRAM out-of-memory errors.
  - Added `_disable_gpu_and_evict()` which permanently marks `_GPU_DISABLED = True` for the process and drops all dead GPU sessions from cache.
  - Added seamless inline CPU recovery in `YuNetOnnx.detect()`: when a GPU device loss is caught during frame inference, it immediately switches the session to `CPUExecutionProvider` and re-runs the frame on CPU. No frames or detections are lost mid-clip, and all remaining items in the queue execute reliably on CPU without crashing.
- **Cross-Integration GPU Coordination in `face_identity.py`**:
  - `available_providers()` and `chosen_provider()` now check `face_detect_onnx._GPU_DISABLED` and evict dead InsightFace sessions when a device loss occurs.
  - `render_selective_blur` falls back cleanly to CPU analysis.
- **Verification**:
  - Added unit test `test_gpu_device_loss_recovers_to_cpu` in `test_face_blur.py` simulating `887A0005` device loss and verifying seamless automatic fallback to `CPUExecutionProvider`.
  - Full test suite: 232 passed across `test_face_blur.py`, `test_face_overlays.py`, `test_effect_render.py`, `test_media_transcription_api.py`, and `test_tools_api.py` (100% pass).


## GPU Hardware Acceleration, Effects Optimization & Transcription CUDA Fix, 2026-08-23

- **Transcription & CUDA cuBLAS DLL Resolution**:
  - Resolved `Library cublas64_12.dll is not found or cannot be loaded` crash for Faster-Whisper on Windows x64.
  - Installed `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, and `nvidia-cuda-nvrtc-cu12` into `.tools/media-ai/runtime`.
  - Implemented dynamic Windows DLL directory discovery (`_ensure_dll_directories`) in `media_ai.py` scanning `.tools/media-ai/runtime/nvidia/*/bin`, PyTorch `lib`, and `CUDA_PATH/bin` with `os.add_dll_directory` and `os.environ["PATH"]`.
  - Added live `_cuda_cublas_available()` check and dynamic fallback in `_speech_draft` so transcription gracefully falls back to CPU if CUDA DLLs or device memory are unavailable. Verified live: loads and runs on CUDA GPU (`device="cuda"`, `compute_type="float16"`).
- **GPU Hardware Video Encoding (`open_h264_stream_writer`)**:
  - Replaced OpenCV single-threaded CPU `mp4v` encoding in `face_blur.py` and `face_overlays.py` with `open_h264_stream_writer()` in `video_encoding.py`.
  - Streams raw BGR24 frames directly into FFmpeg H.264 hardware encoder (`h264_nvenc`), offloading encoding from CPU to RTX 2060 GPU and eliminating redundant 2nd-pass re-encoding passes.
- **DirectML ONNX Runtime Acceleration**:
  - Installed `onnxruntime-directml` (v1.24.4) enabling `DmlExecutionProvider` across InsightFace face identity embeddings and RapidOCR text recognition on DirectX 12 GPU.
- **Face Blur & Face Overlays CPU Optimization**:
  - Enabled OpenCV OpenCL acceleration (`cv2.ocl.setUseOpenCL(True)`).
  - Maintained precision downscaling contracts (`DETECT_WIDTH = 960` for prop placement and `DETECT_WIDTH = 640` for YuNet detection passes), drastically reducing CPU pixel traversal on 1080p and 4K footage.
- **Tools Surface Resurfacing**:
  - Updated `services/api/src/trendrelay_api/tool_setup.py` and `apps/web/app/tools/page.tsx` with guided setup cards and status reporting for `insightface` (DirectML GPU status + MIT/Research license acknowledgement) and `face-anon-simple`.
- **Verification**:
  - Live model test: Faster-Whisper loaded on `device: cuda` with `cublas available: True`.
  - Full backend test suite: 212 tests passed across `test_media_transcription_api.py`, `test_media_ai_performance.py`, `test_effect_render.py`, `test_face_blur.py`, `test_face_overlays.py`, and `test_tools_api.py`.
  - Frontend production build: Next.js build completed with 0 TypeScript/compilation errors.

## Campaign Autopilot selection actions import fix, 2026-08-23

- Fixed runtime `ReferenceError: SELECTION_ACTION_KEY is not defined` and missing component references in `apps/web/app/campaigns/autopilot-panel.tsx`.
- Imported `SELECTION_ACTION_ICON`, `SELECTION_ACTION_KEY`, `type LibraryMediaKind`, and `type LibrarySelectionTarget` from `../../lib/library-selection-actions`.
- Dynamically imported `CaptionEditor`, `BulkVoiceEditor`, and `BatchTranscribe` from the library module matching `apps/web/app/library/page.tsx`.
- Normalized `selectionTargets` typing and explicit callback types for `onQueued`.
- Verification: Full workspace TypeScript check (`npm run typecheck`) and all 245 web tests pass.

## Shared modern dropdowns, 2026-08-22

- Commit `035a088` records the complete dropdown and design-policy change.
- Replaced every visible native single-value selector in the web app with the
  shared `Select` wrapper over `SearchSelect`; the hidden native selector stays
  responsible for form submission, change events, reset, and required-field
  validation. Lists over eight options become searchable automatically.
- The shared listbox now handles disabled choices, long labels, error focus,
  and viewport-aware sizing. Campaign Autopilot selectors stack and shrink on
  mobile instead of widening the document.
- `SOP.md` now requires every new element to follow `DESIGN.md`, semantic
  tokens, and shared UI components. `DESIGN.md` specifically prohibits classic
  or page-local dropdown chrome.
- Context-specific picker rules were consolidated into the shared UI stylesheet,
  removing cross-page ownership collisions without changing their presentation.
- Verification: web TypeScript, targeted ESLint, and all 241 web tests pass. Live browser checks
  covered desktop Campaigns plus 390px Discover, Library, Attribution, Publish,
  Campaigns, and Workspaces: all rendered app-owned controls and no visible
  native selects. Campaigns and Library dropdown panels remained in the
  viewport; Campaigns had no horizontal overflow after the mobile correction.

## Archived Campaigns visibility, 2026-08-22

- Commit `557209e` makes Campaigns default to Current (draft + active), keeping
  archived campaigns out of the daily operating rail until explicitly selected.
- The compact visibility control offers Current, Archived, and All with counts.
  Changing scope selects the first visible campaign instead of leaving a hidden
  campaign open; archiving/restoring likewise falls forward to visible work.
- A direct URL to an archived campaign intentionally opens Archived once, then
  releases that initial routing choice so the operator can switch normally.
- Targeted ESLint and web TypeScript pass. Live browser verification covered
  default/reload hiding, Archived + Restore, and returning to Current.
- Follow-up commit `b65f433` localizes the visibility control, counts, restore
  action, and empty-state guidance across English, Vietnamese, Japanese, French,
  Chinese, Russian, and Arabic. A 390×844 browser pass found zero horizontal
  overflow and confirmed the Vietnamese control before restoring English.

## ElevenLabs guided setup and live voice controls, 2026-08-22

- Commit `d5e709c` adds ElevenLabs to the Tools guided-setup surface as a hosted
  service: the API key is saved locally, never returned to the browser, checked
  live, and the current tier, character allowance, remaining quota, and reset
  are shown without maintaining a stale pricing table.
- The provider now follows the paginated v2 voice catalog and preserves verified
  language, locale-derived region, accent, labels, compatible models, and preview
  metadata. It reads live TTS models, language support, character multipliers,
  and free/paid per-request limits from the v1 model endpoint.
- Library voice generation supports searchable/filterable voices, country or
  region and language filters, explicit model and spoken-language selection,
  and stability, similarity, speed, style, and speaker-boost settings. Model,
  language, and settings are part of the durable-job signature and payload.
- Verification: 43 focused backend tests pass; Ruff, targeted Tools/Library
  ESLint, web TypeScript, staged catalog JSON validation, and the live Tools
  setup dialog pass. No ElevenLabs key is saved on this machine, so account-live
  voice/quota rendering was verified through provider tests rather than spending
  against an operator account.

## Action-oriented SOP catalog over MCP, 2026-08-22

- Reviewed procedures now live under the top-level `SOP/` directory as recursively discovered
  Markdown files with validated YAML metadata: canonical `id`, `action`, title,
  summary, version, tags, and unique aliases.
- The first procedure is `campaigns.fill-needs-copy`, adapted from the operator's
  campaign-copy SOP. It requires live queue/post context, dynamic language and
  platform behavior, minimum-field writes, fresh-queue completion checks, and
  the stated conflict-priority order.
- `SOP/MCP_GUIDE.md` is the control tower included in MCP initialization and
  exposed as `trendrelay://mcp/guide`.
- MCP exposes the one action catalog through read-only `list_sops` / `get_sop` tools and
  `trendrelay://sops` / `trendrelay://sops/{action}` resources. MCP policy still
  controls authority; reading an SOP cannot authorize publishing or approval.
- Focused verification: 35 MCP/SOP tests pass, Ruff passes, and direct reads of
  both the catalog and action resource return the reviewed Markdown.

## Library transcription and translation audit, 2026-08-22

- Commits `3a48b3c` and `01242f6` make local media-AI setup recover after worker reloads and restore the Library setup/transcription controls that its page already imports.
- faster-whisper uses the public `Systran/faster-whisper-base` model anonymously and loads the cached model with `local_files_only=True`; an expired Hugging Face token no longer participates. A live probe loaded `WhisperModel` while `HF_TOKEN` was deliberately invalid.
- Argos Translate 1.11.0 is installed and active with all 12 configured English/Vietnamese/Japanese/French/Chinese/Russian/Arabic directions. A live English-to-Vietnamese translation succeeded.
- Provider setup jobs now have three attempts for process-loss recovery, a 120-second renewable lease, and a 30-second heartbeat during opaque pip/model/package downloads. Explicit provider failures remain terminal immediately; only a vanished worker consumes a recovery attempt.
- Argos setup progress uses ASCII `source->target`; the former Unicode arrow crashed the Windows cp1252 setup console even though the package download itself was valid.
- Library hides an old failed setup record once the runtime is demonstrably prepared, so a historical 401 or abandoned-worker message cannot contradict a current On switch. Machine transcripts remain drafts until explicitly promoted to reviewed text; translation produces caption tracks without replacing the source transcript.
- Initial verification: 106 focused API tests passed; Ruff passed on the four backend/test files; targeted ESLint passed on both Library controls; cached faster-whisper and live Argos translation both passed. A concurrent Discover update subsequently cleared the earlier unrelated syntax blocker.
- Follow-up commits `f98cb22` and `cc742f6` close the end-to-end gaps found after setup: the clean worker now drains both setup and enrichment queues; inference heartbeats its lease and reports named stages; failed/cancelled content-addressed transcription and caption jobs requeue when the operator asks again; Library keeps polling stalled/recoverable work across worker restarts; Argos now honours its Off switch; and the caption language selector shows only reachable targets from the selected transcript's source language.
- Follow-up verification: 110 focused tests, targeted Library ESLint, and the full web TypeScript check pass. A live faster-whisper pass against an existing Library audio derivative returned one English segment while `HF_TOKEN` was deliberately invalid. Live Argos reports Ready with 42 reachable installed paths and changed a generic English sentence into Vietnamese. In the running Library, both provider switches render On without stale errors; the automatic-reading controls are enabled; and the Captions dialog offered only the six reachable English targets, then built a Vietnamese one-cue preview with no error.

## Current state

- Repository uses a hybrid web/desktop, Python modular-monolith architecture.
- Next.js web shell, hardened Electron shell, shared TypeScript schemas, plugin contracts, publication states, and a FastAPI health endpoint are scaffolded.
- `scripts/dev.py` supervises hot-reload services, reuses healthy backend/frontend processes instead of launching duplicates, and waits for new services to become healthy before starting dependents. Reused services require consecutive failed probes before shutdown. Browser mode opens `http://127.0.0.1:3001/` automatically only after readiness; `start-electron.bat` delegates to `start.cmd --desktop`; normal startup also applies pending database migrations.
- The pinned `jiji262/douyin-downloader` 2.0.0 provider is integrated as `media.douyin-downloader` and installed locally at revision `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- Local development uses a `local-admin@trendrelay.local` AAL2 bypass by default, automatically creates one Local Workspace owner membership, and marks the UI with a Local admin badge. It trusts loopback and private-network clients (RFC 1918, link-local and unique-local IPv6) so a browser on the same LAN is signed in as the local operator; it stays unavailable to public addresses and outside the development environment. Machine-level actions (tool installs, credential writes, browser-session capture, provider downloads, manual-package export, device pairing) keep their own loopback-only 403 guards regardless. Set `LOCAL_AUTH_BYPASS=false` to exercise real authentication locally.
- The global toolbar exposes job updates through an outlined notification bell rather than a Jobs text button. Each `job_id:status` transition is an unread notification, displayed in a wrapping vertical list with per-row and mark-all-read controls. Read keys are bounded and stored locally per user; reading never deletes durable job history.
- All nine primary tabs now share a compact sticky page-context treatment beneath the global toolbar. Existing workspace selectors remain visible where the page header owns them; Library keeps only Creative intelligence, Media Library, its purpose, and Workspace sticky, while processing/transcription status remains ordinary scrolling content. Desktop sticky side panels are offset below the persistent context, and the shared header adapts below the two-row mobile navigation.
- The media console now owns Douyin authentication setup: **Connect Douyin** starts a loopback-only, owner-confirmed connection process, installs isolated Playwright/Chromium login support if missing, opens a visible Douyin window, polls for the required session cookies without terminal input, stores them only in ignored `.data/douyin/cookies.json`, and refreshes readiness. Chromium is never used for media downloading.
- The root `/` screen is a focused batch Douyin downloader. It extracts and deduplicates supported links from pasted share text, previews detected video/profile/collection sources, ignores unsupported URLs, exposes profile mode and 10/20/50/100 per-source limits progressively, submits one explicit durable `douyin_download` action without a redundant confirmation dialog, and filters the live queue by active/completed/attention state. Its primary download action is disabled only during submission; incomplete input or provider setup produces a specific inline correction and missing input returns focus to the link field. The label sits outside the focused textarea shell, Home opens directly into the link composer and live queue without a redundant workflow strip, and history uses five-row progressive disclosure while active work remains expanded. Completed batches link to their folder, Library/Studio preparation, campaign planning, and publishing. `npm run douyin --` remains the full CLI. Downloads, SQLite state, ephemeral configuration, upstream source, dependencies, and credentials remain ignored.
- Fresh Windows clones use a four-stage `start.cmd` setup with actual Node/Python version checks, reproducible `npm ci`, and `scripts/bootstrap.py` for visible, retried, time-bounded API dependency installation. The lockfile and Windows CI are pinned to npm 11.16 so its stricter manifest/lock consistency checks match current clean machines. A version-pinned npm `allowScripts` policy covers the six reviewed native build/media dependencies, while `scripts/check_node_dependencies.mjs` detects and repairs incomplete JavaScript installs instead of trusting the presence of `node_modules`. A manifest/Python fingerprint skips unchanged work, interrupted setup is safely resumable, `--check` is download-free, and CI verifies the same dependency probe.
- Douyin Library ingestion reads provider sidecar metadata to retain the channel name, source caption, publication time, and item-specific video URL. Duplicate refreshes backfill missing provenance without overwriting reviewed values; the detail view links the creator name to the originating Douyin profile when the batch retained that profile URL, shows the original video as an accessible Douyin icon action, and opens the selected asset's containing folder through the guarded local API.
- Next.js serves `apps/web/app/icon.svg` as the product favicon: a green rounded square with a white rising four-node relay path, designed to remain distinct at browser-tab sizes.
- Social publishing is hosted-API only. `services/api/src/trendrelay_api/integrations/publishing.py` implements four interchangeable engines behind one adapter: Bundle.social (`x-api-key` + team ID, uploads the local MP4), Zernio (`Bearer sk_…` at `https://zernio.com/api/v1`, presigned upload then `POST /posts`), Buffer (GraphQL `createPost` at `https://api.buffer.com`, which requires an already-public media URL), and WoopSocial (`Bearer` at `https://api.woopsocial.com/v1`, multipart `POST /media` then `POST /posts`). `PUBLISHING_PROVIDER` selects the active engine. No AGPL publishing service, PostgreSQL, Redis, or Temporal is installed or supervised any more.
- Whether an engine is handed the local file is two capabilities, not one. `requires_public_media` says it cannot take an upload at all (Buffer); `ingests_media_url` says it can fetch a URL instead of being given the file. WoopSocial can do neither but the upload, which matters because one post spans several engines: a public URL supplied so Buffer can work must not leave WoopSocial with nothing to send. Its single-request upload is capped at 100 MB and refused here, by name and size, rather than at the engine.
- WoopSocial reports delivery per destination - status, external URL and error for each account - which no other engine here does. Its `WOOPTEST` sandbox platform is deliberately unmapped so it cannot appear as a destination, and the platform on each post body is read back from the account because their `LINKEDIN` and `LINKEDIN_PAGES` are both `linkedin` to us and the body rejects the wrong one.
- Engine API keys are entered on `/publish` and written back to the project `.env` by `env_store.py` through a loopback-only, owner/approver, explicitly confirmed endpoint. Only fixed allow-listed keys are writable, cached settings refresh immediately, and stored values are never returned — the API exposes configured booleans and the variable name only.
- `config/tool-catalog.json`, the `npm run tools --` CLI, the loopback-only lifecycle API, and `/tools` catalogue all seven managed capability projects. The Tools page is also the provider setup hub: Douyin owns automatic cookie capture, Meta Ads uses a confirmed fixed-command authentication launcher, Last 30 Days exposes only configured optional secret names, Agent Reach provides local diagnostics, and no-auth tools link to their operational surface. Compact, collapsed Meta Marketing API and Amazon Creators API access guides link to official credential setup pages; Opportunities links directly to the Amazon guide.
- The pinned Last 30 Days 3.16.0 source is installed and active locally. `npm run research --`, the research API, and `/research` execute its stable agent JSON 1.x contract and persist workspace-scoped evidence.
- The pinned OpenMontage source is installed and active locally. `/studio` and `npm run studio --` expose clip-factory/podcast-repurpose preflights plus approved, zero-network local VideoTrimmer jobs with immutable-source checks, manual clip ranges, budget enforcement, verified outputs, and provenance.
- Windows exposes three deliberate root entry points: `start.cmd` for browser development, `start-electron.bat` for desktop development, and `update.cmd` for a guarded fast-forward-only pull. The updater refuses dirty worktrees, detached HEAD state, and branches without an upstream. Provider/tool operations use npm scripts rather than extra `.cmd` files.
- The pinned Agent Reach 1.5.0 source is installed and active locally. `npm run reach --` and the `/tools` Diagnose action expose 15-channel local-presence diagnostics without upstream execution, network probes, user-config reads, browser-session access, or secret-value exposure.
- The pinned Meta Ads Kit source is installed and active at `0879bb4566a836670f33beb509ff7d8d4779849e`; its isolated `@vishalgojha/social-flow` 0.2.17 runtime provides the `social` command. The Python adapter exposes only read-only account/campaign/ad/fatigue reports, and `/research` presents it as first-party validation alongside Last30Days execution and Agent Reach diagnostics.
- The pinned Meta Ads Collector 1.4.0 source is installed and active at `0ffb2fb1af94eae6542b328ab3ae31fc1c9a5897`. `/research` embeds its no-key public Ad Library search as competitive creative intelligence with bounded normalized results, a fixed isolated bridge, and no cookies, proxy controls, media downloads, webhooks, or mutations.
- Last 30 Days is adapter-ready. OpenMontage preflight and deterministic local clipping are adapter-ready; paid/networked generation remains intentionally blocked.
- MediaCrawler is a catalogued source-checkout provider, installable and activatable from `/tools` and off by default. Its upstream README asks against large-scale crawling, so its adapter keeps collection bounded and operator-initiated rather than scheduled.
- TrendRelay core still uses SQLite locally and plans PostgreSQL/pgvector plus S3 for shared deployment. No embedded database, queue, or scheduler ships with the app.
- `Research/` and `References/` are local-only and ignored by Git.
- SQLAlchemy 2 models and Alembic migrations through `20260726_0010` provide user profiles, workspaces, four roles, expiring invitations, device pairings, secret references, and transactional audit events. SQLite is the easy local default; PostgreSQL is the shared/production target.
- The API verifies Supabase asymmetric JWTs through JWKS plus distinct TrendRelay device JWTs; both require issuer, audience, expiry, and subject claims. `/sign-in` implements password sign-in/sign-up, verification redirects, magic links, Google OAuth, password recovery, and global sign-out.
- `/workspaces` lists and creates workspaces, displays members and audit events, and gives owners controls for membership, expiring email-bound invite links, optional encrypted SMTP delivery, secret references, and TOTP account security. Enrolled AAL1 browser sessions are globally challenged before authenticated screens render; deployments can require AAL2 for governed actions.
- `/publish` picks an engine, configures its keys, discovers connected accounts, produces dry-run previews, and submits governed work. Engine cards and per-platform destination cards carry inline SVG marks from `apps/web/app/publishing-icons.tsx` so operators can tell Zernio, Buffer, and Bundle.social — and each destination — apart at a glance. Publishing joins Last30Days research and OpenMontage preflights in TrendRelay's leased SQL job store and supervised hot-reload worker.
- TikTok Discovery reads TikTok Creative Center's public trend tabs. Creative Center is client-rendered and its `creative_radar_api` answers `40101 no permission` to unsigned callers, so plain HTTP cannot read it: `scripts/tiktok_creative_bridge.py` renders the page in an isolated Playwright runtime and `integrations/tiktok_creative.py` normalizes the result. Two extractors run per render - rows carrying `data-index`, then a generic repeating-sibling detector that names no CSS class - with a rendered-text scanner as a third fallback. Metrics arrive in three layouts (`303.7K` + `Posts`, `Video views` + `79M`, and the combined `787.7K followers`) and all three are parsed. Anonymous visitors get three hashtag rows and four video cards before a login wall, which is reported as a note rather than hidden. The song and creator tabs are retired upstream and are declared unavailable rather than silently redirected. Nothing is ever fabricated: an empty render raises and the API answers 503.
- `/campaigns` provides persistent workspace campaigns and a timezone-aware content calendar. Plans bind approved media to captions, hashtags, affiliate links, disclosure, platform deep links, and posting times; owner/approver decisions lock content. Approved plans can hand off to the active publishing engine or produce an idempotent, audited manual ZIP under `.data/manual-packages/`.
- Opportunity scoring - workspace-scoped products and affiliate offers, idempotent CSV import, persisted evidence, deterministic score `v1` with nine visible contributions, research-job provenance, and draft campaign creation linked back to the opportunity and primary offer - is still in force, but no longer has a page of its own. The scoring sits on Discover under the research it scores, the products and offers sit in Attribution, and `/opportunities` redirects (see the Attribution bullet below).
- `/library` is the persistent creative-intelligence layer: durable SHA-256-deduplicated ingestion, hash-addressed immutable originals, FFmpeg thumbnail/proxy/audio derivatives, authenticated content, reviewed transcript/OCR records, versioned creative recipes, rights review, search, and publishable Studio/Campaign handoffs. Contextual counted facets filter by media type, channel, source, and usage rights; each facet ignores only its own selection, full filtered totals are calculated before pagination, and results can be grouped with exact counts without duplicating immutable assets. Douyin and authenticated OpenMontage outputs queue into it automatically.
- `/attribution` is the first-party revenue workbench: governed transparent links, HTTPS and parameter-collision controls, country routing, privacy-minimized click events, idempotent conversion CSV imports, campaign/creative-format summaries, and explicit measurement limitations. Public `/c/{code}/info` reveals the destination before `/c/{code}` records and redirects.
- Shopee Product Offer import uses the operator-exported UTF-8 `.csv` from **Hoa hồng Sản phẩm / Product Offer** as the supported bulk path; a real 100-product Shopee export supplied on 2026-08-15 parsed as 100 valid rows with zero problems. `.xlsx` remains a compatibility fallback. The user could not get past Shopee's CAPTCHA, confirming that automated session reads are not a reliable product boundary. Attribution opens the real offer page only when the user explicitly clicks **Open Shopee Product Offer**, previews the file, shows new/existing/duplicate/problem counts, and imports at most 100 products without a TrendRelay cookie. The CSV has no image field, so the UI does not reserve empty Shopee thumbnails. `Link ưu đãi` is already the commission-bearing link: imports preserve and expose it directly and do not automatically mint TrendRelay redirects or ask for a campaign/platform. Tracking links remain an explicit later campaign action. Automatic page enrichment is not queued after file imports. The older session endpoints remain for compatibility/investigation but are no longer exposed in the main UI; their probes are headless and never open a window, and verification failures direct the user to the CSV path. The parser supports Vietnamese/English headers, UTF-8 BOMs, tab-delimited clipboard rows and preambles, and rejects more than 100 rows. Integration tests prove a CSV creates 100 products, 100 offers, 100 preserved affiliate URLs, and zero implicit tracking links.

- Douyin batches download one source at a time. `run_download_job` invokes `scripts/douyin.py batch` per URL and hands each finished source to the Media Library before starting the next, so files and ingest jobs appear while the batch is still running. Previously every URL went in one invocation and the pinned tool enumerated everything before writing a file. Three layers stop an item being fetched or ingested twice: the downloader's own de-duplicating database plus incremental flags, `_new_artifacts` recording every media path already collected (only unseen files are fingerprinted, so re-scanning after each source stays cheap), and the library's sha256 import de-dupe. A source yielding nothing new is counted, not fatal; a source that fails hard lands in `source_errors` and the batch continues. The worker heartbeats between sources to hold its lease.
- `/discover` opens on a trend rather than empty space. The last TikTok category, region and period persist in `localStorage` under `trendrelay.discover.tiktok` and are restored on the next visit, falling back to the first live category. Retired Creative Center tabs (Songs, Creators) stay in the registry so the adapter can explain itself but are filtered out of the quick links. The adapter's cache decides whether a revisit costs a fresh render.
- The Discover feed shows only what a provider returned. The former `AFFILIATE_STARTERS` cards carried invented conversion rates and commissions under an "Affiliate Signals" source and were shown whenever a provider returned nothing; they are gone and the empty state names the real sources instead.

- Attribution is the product-and-revenue surface. Catalog and Opportunities are
  retired into it: `/catalog` redirects to its Books tab, `/opportunities`
  redirects to Discover carrying `trend`, `source`, `title`, `url` and `job`
  through. Its four tabs are Products (one row per product, expanding to its
  offers, links, clicks and commission), Links, Books (ad economics, which only
  mean anything one level above a product) and Imports (conversion CSV and the
  offer CSV that creates products in the first place). Attribution is now a
  top-level nav item between Library and Publish; the Publish and Discover
  section strips render nothing, having one destination each. Opportunity
  scoring lives on Discover, under the research it scores.
- Publishing addresses several accounts on several engines in one post.
  Destinations are chosen per account rather than per network, so two TikTok
  accounts on two engines are two destinations; `PublishRequest` rejects a
  repeated account, not a repeated network. Every capability question - caption
  and title limits, threading, first comments, approval holds, whether media
  must be public - is asked of the engine delivering that destination, never of
  one active engine. Engines carry an explicit "Use for publishing" switch and
  one of six states (ready, off, no key, key refused, unreachable, no channels),
  each with the engine's own message and a next step. The account load decides
  whether an engine works; the credential probe is only consulted before any
  load has run.
- Campaigns run as standing programmes. `campaign_autopilot.py` decides link
  placement and composes captions, `campaign_scheduler.py` decides what to post
  and why, `campaign_runner.py` is the only part that creates anything
  irreversible, and the durable worker ticks every switched-on campaign once a
  minute. Three tables: `campaign_autopilot`, `campaign_destinations` (each with
  its own tracking link, which is what makes destinations comparable) and
  `campaign_queue_items` (recycling, so a posted item goes to the back rather
  than being consumed). The panel on `/campaigns` gates its switch behind a
  readiness checklist and previews the next day's posts before anything exists.

- Engine cards say what is connected and on which plan. Each engine reports its
  connected channels rather than the platforms it supports - the supported list
  was the same eight or twelve icons on every card whether an account was
  attached or none. No engine exposes which plan an account is on: there is no
  endpoint that names a tier and no field on any response that carries one, so
  `engine_limits.infer_plan` reads it off the limits they do report. Buffer's
  30-day request quota separates Free/Essentials/Team and arrives in
  `RateLimit-Policy`, not `RateLimit` - the latter is the 15-minute window,
  which is 100 on every tier and would call a Team account Free. Against the
  live key Buffer sends only the policy header, reporting 3,000: Free, measured.
  A quota matching no published figure leaves the plan unnamed rather than
  rounded, and the answer carries the same measured/counted/published mark as
  every figure beside it.
- An engine with no quota left stops offering destinations. Its accounts are
  marked unavailable rather than dropped, because a destination that vanishes
  reads as a disconnected account, and each carries how much went against the
  quota. Only a measured or counted figure can block - a published one is a
  pricing-page scrape with no usage and must never refuse a post - and only
  allowances that actually stop a post count, which is why "3 of 3 connected
  accounts" does not: that is room for more accounts, not room to post. A page
  reached by two engines routes around the exhausted one.
- Media hosting is checked rather than trusted. `media_hosting.probe` signs a
  request against the bucket, writes a small object and fetches it back through
  the public base URL with no credentials - the hop an engine makes, and the
  only one that proves public access, which is a separate switch from the API
  token. Each stage names the setting it clears; a public URL answering 200 with
  different bytes fails rather than passes. Saving refuses the two paste errors
  the form invites (the S3 endpoint as the secret, the account ID as the access
  key) and leaves anything wrong-but-plausible to the check.

- Affiliate networks are sent a sub ID, which is the only field that survives
  into their own conversion report. `attribution_subids.py` holds the contract
  per network and the slot map, which is a constant: networks report sub IDs
  positionally, so a slot that meant a placement on one link and a campaign on
  another would produce a column that cannot be grouped. Slot one always carries
  a key derived from the tracking code, because it resolves every other
  dimension from our own database and the conversion importer matches on it -
  `token_urlsafe` puts a `-` or `_` in 27% of codes and Shopee accepts letters
  and digits only, so the key is hashed rather than stored or stripped. Values
  are fixed when the link is minted, since one that changed with a campaign
  rename would split a link's history in two. A host matching no known network
  gets nothing at all: a guessed parameter name can break the sale rather than
  merely weaken tracking.

## Decisions in force

- Follow `SOP.md`; atomic descriptive commits and current README/handover files are mandatory. Its "Interface work" rules apply to every change that touches the UI: the right control for the interaction, built once in `apps/web/app/ui/`, native semantics kept, logical properties, all seven dictionaries, and layout verified at real widths with real content rather than assumed.
- Python/FastAPI is the control-plane runtime; Python also powers compute-heavy workers.
- Provider source remains isolated under `.tools/`; core modules depend only on capability contracts.
- Supabase access tokens are verified with asymmetric JWKS only. Workspace authorization is membership-and-role based; no service credential is exposed to the web or Electron renderer.
- Browser components receive an authorized-fetch capability rather than token values. OAuth started in Electron opens in the system browser; the signed one-time device authorization flow pairs the approved identity back to Electron, whose main process encrypts the distinct device token with operating-system `safeStorage`.
- TOTP MFA is optional at the account level. Missing assurance claims are AAL1; `REQUIRE_AAL2_FOR_GOVERNED_ACTIONS` can enforce AAL2 for owner controls, provider publishing, and pairing approval. Paired device tokens preserve the approving browser session's assurance.
- Secret records store approved locators only and reject raw values. Governed mutations append audit events in the same transaction.
- Invitation email is opt-in, owner-only, TLS-only, and rate-limited. The token digest commits before SMTP; raw tokens are never queued, stored, logged, or audited, and delivery failure preserves the copy-link fallback.
- Live trend research requires explicit external-action confirmation. Browser-cookie extraction is disabled and the adapter passes only allowlisted research secrets to Last 30 Days.
- OpenMontage proposals require a declared rights basis, immutable source hash, budget cap, and explicit approval. Rendering requires a second confirmed action, stays local and zero-network, uses fixed output roots, and never implies permission to publish.
- Agent Reach diagnostics are local-presence-only. The upstream installer, MCP/skill mutation, browser-cookie import, command execution, live network probes, and user-config access remain outside the trusted adapter boundary.
- Meta Ads Kit is read-only in TrendRelay. Briefings are loopback-only and confirmed; commands are constructed from fixed report templates, provider stderr is sanitized, credentials remain in the isolated CLI profile, and pause/resume/budget/create/upload/delete operations are absent. Any spend-impacting capability requires a new ADR and approval design.
- Meta Ads Collector is a separate public-research boundary. Searches are loopback-only, explicitly confirmed, capped at 50 ads, and normalized before returning to the browser. Its reverse-engineered browser-facing GraphQL transport is accepted as operationally fragile; failures stay sanitized and no provider credentials or account mutations are in scope.
- Meta setup prefers the tool-owned OAuth launcher; raw or temporary access tokens are never pasted into TrendRelay. Amazon setup is documentation-only until a reviewed Creators API adapter exists: do not accept Amazon credentials yet, do not start a Product Advertising API integration, and continue using SiteStripe links or CSV imports.
- Every managed capability repository must be pinned in the machine-readable catalog and documented in the human-readable catalog; supporting runtime repositories must be lockfile-pinned and documented.
- Tool installation and activation remain separate; source presence never implies credentials, dependencies, or production readiness.
- Tool setup reports expose readiness, dependency state, and credential names only. They never return secret values. Interactive setup actions are loopback-only, explicitly confirmed, and selected from fixed tool/action mappings rather than user-supplied commands.
- Publishing is dry-run-first. TrendRelay never accepts social-platform passwords and never returns an engine credential to the browser; social accounts are connected inside the engine's own dashboard. Drafts are the default and scheduling is opt-in.
- An engine declares the platforms it supports, and a request naming an unsupported platform is rejected before any network call. Because the engines disagree about media, a request carries both an approved local `video_path` and an optional public `media_url`: Buffer requires the URL, Zernio prefers it and otherwise uploads, and Bundle.social always uploads the reviewed local file.
- Publishing operations use content-derived IDs and freeze the resolved engine into the job payload, so switching engines never re-routes in-flight work. Workspace publishing jobs are durable but receive one provider attempt because duplicate and uncertain retries require inspection. Only owners and approvers can discover integrations, change engines, save keys, or execute; editors may preview.
- Publishing media must resolve to an existing MP4 beneath `PUBLISHING_MEDIA_ROOTS`; the local defaults are `.data/downloads`, `.data/media`, and `.data/productions`.
- Douyin batches default to 50 items per selected profile mode. Full crawls require explicit `--limit 0`. Downloads use only the pinned API provider; browser fallback has been removed. The optional login browser exists only to capture cookies. Empty provider output is a failed job, while historical zero-artifact successes are labeled `empty` in the UI.
- Cookie values come from the app-controlled Douyin connection flow, local environment variables, or `.env`; values are redacted from status/dry runs and exist in generated download configuration only for the process lifetime. Starting connection requires an owner, governed assurance, explicit confirmation, and a loopback request.
- SQLite and file-based deduplication remain enabled for repeat and incremental downloads.
- Keep downloaded content as reference media until rights and policy classification permits further use.
- Library originals are immutable. Only owned, licensed, and public-domain assets may enter campaigns; owner/approver rights changes require confirmation, evidence, governed assurance, and an audit. Automatic transcription remains visibly unavailable until a reviewed provider is incorporated.
- Attribution links are transparent, HTTPS-only, and fail closed. Existing affiliate parameters are preserved; configured campaign/platform parameters cannot collide. No raw IP, full referrer path, fingerprint, or network order reference is stored. Production requires `ATTRIBUTION_HASH_SECRET`; currency totals are never combined.

- Usage rights are retired as a product concept. Removing the controls left the classification enforcing itself with nothing able to satisfy it: every import defaulted to `unknown`, which is not publishable, so the Library detail pane hid its Studio/Campaign/Publish links and `campaigns_api` rejected every plan with a 409. The campaign gate, the `/assets/{id}/rights` endpoint and `RightsUpdate`, the publishable-rights import gate, the `rights_status` filter and `rights` facet, `rights_status`/`publishable` on the asset view, `PUBLISHABLE_RIGHTS` and the `RightsStatus` literal are all removed. The `media_assets` columns stay with their `unknown` default as inert provenance; dropping them would need a migration and would discard history for no functional gain.
- Buffer needs per-network metadata or it refuses the post. `InstagramPostMetadataInput` declares `type: PostType!` and `shouldShareToFeed: Boolean!`, Facebook declares `type: PostTypeFacebook!`, and YouTube requires a title on create. `_buffer_metadata` supplies them: Instagram and Facebook publish as Reels, matching the other two engines for short-form video, YouTube takes the title or the caption trimmed to 100 characters, and the AI-disclosure toggle reaches Instagram and TikTok through `isAiGenerated`. Enum values are bare GraphQL tokens, never quoted strings.
- Error banners must stay readable. `.registry-error` painted `#ffc1af` on `#f8d7da`, a contrast ratio of 1.16:1, which made engine failures such as a bundle.social 403 invisible. It is `#721c24` at 8.25:1, and the two blocked badges sharing that pink wash moved from 3.33:1 to 5.81:1.

- Affiliate link placement is decided by the network, not by a setting. A URL in
  an Instagram or TikTok caption is not a link - it renders as plain text - and
  a link in a first comment now costs Instagram reach and gets the comment
  hidden. So: caption where a link is clickable and unpenalised, bio for
  Instagram and TikTok with the tracking link on the profile, and a
  first-comment path that exists but is deliberately unused unattended. Every
  destination carries the reason alongside the decision.
- The disclosure leads every caption and is not configurable. It is required
  near the endorsement, no later than the link, prominent, and on every post. A
  post with an offer and no disclosure is refused at the setting and at the
  post.
- Ranking is by measured earnings per click and refuses to rank below five
  settled conversions, reporting why rather than printing a figure built on
  luck. Every fourth slot explores, because always posting to the current leader
  guarantees the others never gather the evidence that would overturn it.
- DM automation is out of scope by choice. It converts several times better than
  a bio link on Instagram and it is the fastest way to get an account restricted
  when driven from a tool.
- Autopilot never approves content, writes copy, or invents a posting schedule.
  A workspace with no slots posts nothing and says so.

## Validation

- TikTok Discovery rewrite (2026-08-04): the previous adapter fetched the page, read only its `<title>`, and returned three hard-coded rows labelled as Creative Center data; it also blocked the event loop and pointed at URLs that now redirect. Replaced with a rendered-page collector, verified live against both working tabs - hashtags returned `#spidermanbrandnewday` at 303.7K posts / 944.2M views, videos returned four creator cards with followers and view counts. Both extractors and the text fallback were checked to agree on the same render. 38 parser tests run offline against two recorded fixtures of real markup, covering metric layouts, column reordering, malformed bridge output, caching, retired categories and credential scoping. The API answers 503 for a retired tab and 422 for bad input.
- Publishing-engine polish (2026-08-04): the bundle.social payload was corrected against the published OpenAPI schema - it had been sending `socialAccountIds` (not in the schema; the API selects by `socialAccountTypes`), omitting the required top-level `title`, and using `privacyLevel`/`brandContentToggle`/lowercase `privacy` values the API does not accept, so every bundle.social publish would have failed. Reddit and Pinterest now collect the subreddit and board those engines require. Contract tests assert each corrected field.
- Publishing-engine migration (2026-08-03): `npm test` passes 177 tests, including a new provider suite covering the Bundle.social upload/post pair, Zernio presign-then-`POST /posts` for both scheduled and draft deliveries, Buffer's per-channel GraphQL mutations and surfaced `MutationError` text, unsupported-platform rejection, and a `.env` writer suite. ESLint, TypeScript, and Ruff pass. An end-to-end run through the real ASGI app saved a Zernio key, confirmed it reached a scratch `.env`, confirmed the secret was not echoed in the response, rejected an unconfirmed save (400) and an unknown engine (422), and persisted an engine switch to `PUBLISHING_PROVIDER`. Every Postiz validation entry below is superseded: that service no longer exists in the tree.
- Postiz Winget recovery tests reproduce `0x8A15004B`, verify community-source isolation, one refresh retry, and non-blocking core startup. The focused launcher/Postiz suite passes 24 tests, and the full lint, TypeScript, and 185-test Python validation passes.
- Favicon QA confirmed that Next.js discovers the SVG as image/svg+xml, exposes /icon.svg as a static route, and passes ESLint, TypeScript, and the production web build.
- The collapsed Meta and Amazon access guides were browser-tested under local admin at desktop and 390px widths. Both expose four concise steps and official provider links, Amazon's unsupported-adapter warning is visible, Opportunities links back to the Amazon guide, there is no horizontal overflow, and the browser console is clean. ESLint, TypeScript, and the production web build pass.
- Douyin console browser QA verified a separate field label and accessible shell focus ring with no textarea outline overlap, five initial history rows, expandable artifacts and handoffs, five-at-a-time history loading, no duplicate help rail, no console warnings, and no horizontal overflow at a 390 px viewport.
- Download-action QA verified that the empty form keeps **Start download** enabled, clicking it shows a specific supported-link message and focuses the textarea, and a valid `v.douyin.com` link remains ready without starting a live download. ESLint and TypeScript pass.
- Native Postiz validation completed: PostgreSQL, Redis, Temporal, the backend, orchestrator, and frontend all reported healthy.
- The app-managed Postiz local-session route set its auth cookie and landed at `/launches` without a login form. The Publish screen reported `Local service ready`, and its **Open local Postiz** action completed successfully.
- Live browser QA opened Tools without sign-in, rendered all provider setup cards, then opened embedded Postiz. Clicking unconfigured Reddit and Instagram Standalone connectors stayed on `/launches` and showed the setup warning; no external OAuth URL containing `undefined` opened.
- The one-click runner remained healthy through a real WatchFiles backend reload after Windows child services were isolated with `CREATE_NO_WINDOW`; backend and web both returned 200 afterward. Focused Postiz/API tests pass (20), along with Ruff, explicit Python compilation, ESLint, and TypeScript checks.
- The local Postiz API key validated. Integration discovery correctly returns an empty list until platform accounts are connected through their OAuth flows.

- Pinned upstream checkout resolved exactly to `ef3ad18c2b50e38e534f72aabe2b3fbb0b3fadd7`.
- Isolated provider installation succeeded on Python 3.14; `npm run douyin -- check` reported version 2.0.0.
- Fourteen API, wrapper, URL-security, validation, secret-lifecycle, environment-loader, and manifest tests passed.
- Ruff lint and formatting checks passed for the integration code and tests.
- End-to-end `npm run douyin -- batch --file ... --dry-run` parsed copied share text and profile URLs, deduplicated input, applied incremental bounded settings, and produced redacted configuration.
- A live media download was not initiated because no user-authorized Douyin URL was provided.
- Pinned Postiz checkout resolved exactly to `41c5a9dbd6b2776863e7c05c22e7a385c208321c`; the isolated build and `npm run postiz -- check` reported version 2.0.15.
- Postiz wrapper and governed-adapter smoke tests produced dry-run drafts with private/safe defaults and made no provider call. The authenticated publishing API enforces workspace roles, explicit confirmation, approved media roots, and one-attempt durable execution; `/publish` is included in the production browser build.
- No real social upload or post was initiated because credentials, integration IDs, an approved video, and explicit execution confirmation were not supplied.
- `start-electron.bat` repaired the missing Electron 43.1.1 Windows binary through the package-provided installer, then passed desktop-mode validation.
- Unified-runner tests cover healthy-service reuse, unavailable-service startup selection, and missing Electron detection.
- The Last 30 Days pinned checkout was verified as 3.16.0 and activated. CLI and API mock runs completed through agent JSON schema 1.2 and each ingested two workspace-scoped evidence records without external calls.
- The exact OpenMontage checkout was installed and activated. Its two guarded manifests load successfully. The isolated upstream VideoTrimmer produced and ffprobe-verified a real one-second MP4 from the pinned demo source using locked FFmpeg 6.1.1 binaries. The worker passes no provider credentials, performs no network call, records source/artifact hashes and package/upstream provenance, and reports zero provider cost.
- The consolidated `/research` inspiration radar was visually smoke-tested in the local app at desktop and 390px mobile widths. Recent topic evidence, public competitor creative, and first-party Meta signals render in one filterable card feed; an empty workspace defaults to six usable starter patterns rather than an empty state. Trend and public-ad search share one mode-switching query bar, account validation and activity are compact disclosures, and a source-health drawer links directly to tool management. Local-admin access and browser console checks passed; the shared browser API resolver follows loopback or LAN hostnames instead of hard-coding `localhost`, and development CORS accepts private-LAN frontend origins.
- The Agent Reach pinned checkout resolves to `1494c2ab239e7355a77e7cceaf3271453a1f34b5` (upstream 1.5.0). The adapter reports all 15 pinned channels, currently with 2 ready, 1 setup-required, and 12 unavailable local capabilities; no live platform calls were made.
- Meta Ads Kit resolves exactly to `0879bb4566a836670f33beb509ff7d8d4779849e`; isolated Social Flow 0.2.17 version/help checks pass locally on Node 22 despite its declared newer engine warning. Adapter/API tests prove strict account/preset validation, fixed read-only commands, winner/bleeder/fatigue synthesis, loopback confirmation, provider-error redaction, and harmonized research status. No Meta account was authenticated and no live briefing or mutation was run.
- Meta Ads Collector resolves exactly to `0ffb2fb1af94eae6542b328ab3ae31fc1c9a5897`; its isolated runtime imports successfully and a live one-result public `coffee` query completed with one request, one normalized ad, and no account/API credential. Twelve focused adapter/API tests, Ruff, TypeScript, ESLint, JSON validation, diff checks, and the full Next.js production build pass.
- Migrations `20260722_0001` through `20260726_0009` upgrade a fresh local database to head. Foundation tests cover workspace creation, roles, invitations, owner-only secret references, raw-secret rejection, slug validation, and ordered audit events.
- Browser production builds cover `/sign-in`, `/update-password`, and `/workspaces`; ESLint and TypeScript checks pass. Next.js is updated to 16.2.11, patched PostCSS/Sharp overrides are installed, and `npm audit` reports zero known vulnerabilities.
- Migration `20260722_0002` adds one-time, email-bound invitation tokens with expiry, revocation, replay protection, and transactional acceptance. Development CORS now explicitly permits the browser Authorization header.
- Migration `20260722_0003` and `/device` implement a loopback-only, ten-minute, one-time desktop authorization grant. Device JWTs have a separate token type, audience validation, and an eight-hour default lifetime; production requires `DEVICE_TOKEN_SECRET`.
- Electron keeps the device JWT encrypted with operating-system `safeStorage` in the main process. IPC sender origin, renderer navigation, API origin/path, and HTTP methods are allowlisted; the preload never exposes bearer tokens. `start-electron.bat --check` validates without launching services.
- `/account/security` implements TOTP enrollment, QR/manual-secret setup, challenge-and-verify login, unfinished-factor cleanup, AAL2-only verified-factor removal, and a global enrolled-session gate. API tests cover fail-closed claim defaults and optional governed-action enforcement; migration `20260722_0005` carries browser assurance into device pairings. A fresh SQLite database upgraded from empty to `20260722_0005`, and the production browser build includes `/account/security`. A live enrollment was not attempted because no configured Supabase test account was supplied.
- Migration `20260722_0004` adds shared durable jobs with expiring leases, heartbeats, retry budgets, scheduling, cooperative cancellation, and recovery of abandoned running work. Last30Days and OpenMontage are migrated off JSON.
- The unified runner includes a watch-reloaded durable worker for Douyin acquisition, Media Library ingestion, Last30Days research, OpenMontage rendering, and Postiz publishing. A live Last30Days mock completed from SQL with no legacy file, an OpenMontage proposal/approval completed its SQL preflight record with no legacy file, and `scripts/worker.py --once` drains recoverable work.
- Opt-in workspace invitation email uses standard SMTP with STARTTLS or implicit TLS, HTTPS-only public links outside loopback, a 20-attempt-per-workspace hourly default, metadata-only audits, and an always-available copy-link fallback. Unit and API tests prove delivery behavior and raw-token non-persistence; no real email was sent because SMTP credentials were not supplied.
- Local-admin behavior was smoke-tested in the running browser app: login was bypassed on loopback, Local Workspace was created with owner role, the Local admin badge rendered, and no browser console errors were reported. LAN/production fail-closed behavior is unit-tested.
- Notification-center QA uses the production CSS and list markup at desktop and 390px widths with deliberately unbroken URL/error strings. The notification list explicitly resets the global `ol` auto-fit columns, gap, and background to one full-width column; panel and row client/scroll widths match, the bell has a visible resting outline, and mark-all-read retains all rows while clearing the badge. Live QA in the running local-admin app confirmed 15 full-width vertically stacked rows at desktop and narrow widths with no horizontal overflow. The fixed heading stays in place while the 1,478px list scrolls independently inside its 558px viewport; scrolling moved the list to 430px without moving the page. ESLint, TypeScript, and the production web build pass.
- Automatic Douyin connection tests cover loopback owner authorization, explicit confirmation, isolated subprocess startup, automatic cookie detection without stdin, atomic local cookie/status writes, and secret-free public status. The visible login window itself was not completed against a real account during automation.
- Douyin provider 2.0.0 passed installation checks and redacted dry-run configuration. Tests cover cookie sources, missing-cookie refusal, absence of browser fallback, artifact hashing, and rejection of zero-exit runs that save no media. A live network download was not run because valid cookies and a user-authorized source URL were not supplied.
- Douyin provenance validation passes 19 focused job/ingestion tests plus Ruff and ESLint. The production web build passed with the channel and icon treatment; the final single-primary-link refinement was rechecked with the focused Python suite, Ruff, and ESLint.
- Library categorization validation passes the three focused Media Library API tests, Ruff, ESLint, TypeScript, diff checks, and the production web build. Live local-admin QA confirmed exact workspace and filtered totals above the page limit, contextual media/channel/source/rights counts, exact channel grouping, zero console warnings/errors, and no horizontal overflow at desktop or 390 px; the mobile facet stack was reduced from one column to a compact two-column layout.
- The complete project suite passes: 138 tests (65 API and 73 root integration tests).
- Media Library validation covers import confirmation and loopback enforcement, SHA-256 idempotency, workspace search, reviewed enrichment and recipe derivation, rights audits, file-hash campaign blocking, automatic Douyin/OpenMontage handoff, missing-content handling, and a real pinned-FFmpeg derivative run producing an original, thumbnail, proxy, and audio file. The API suite passes 62 tests, the root integration suite passes 73 tests, and web/desktop production builds pass. Local-admin browser QA confirms active navigation, ready local processing, the honest transcription status, no console errors, no horizontal overflow at 1280 px or 700 px, and the responsive single-column breakpoint.
- Campaign/calendar validation covers workspace isolation, archived-campaign locking, timezone-aware plans, approval content lock, explicit and loopback-only export, complete ZIP contents, audit events, and idempotent package reuse. A fresh SQLite database upgrades through `20260726_0009`; the production web build includes `/campaigns` and `/library`. Browser QA created and refreshed campaigns under isolated local-admin data with no console errors, while the API suite exercised approval and export without external publication.
- The practical root console was checked in the running Next.js app through both `localhost` and `127.0.0.1`; the sign-in state exits loading reliably, fits a 1280px viewport without horizontal overflow, and the private-LAN development-origin allowlist matches the unified runner.
- Tool catalog coverage includes complete listing, explicit confirmation, loopback-only mutation, pinned checkout, activation, and Windows-safe isolated uninstall.
- Production builds for Next.js and Electron, TypeScript checks, ESLint, Ruff, JSON validation, CLI listing, and diff checks pass. The `/tools` page exposes the catalog cards, the locally installed/active providers, guarded lifecycle controls, and Agent Reach diagnostics.
- Tool-setup API tests cover sanitized Douyin readiness, credential-name-only Last 30 Days reporting, explicit launcher confirmation, and rejection of unknown actions. The Next.js production build, TypeScript, ESLint, and Ruff checks pass for the guided setup interface.

- Opportunity validation covers required/optional CSV fields, monetary normalization, restrictions, idempotent re-import, workspace isolation, research evidence handoff, exact scoring contributions, ranked listing, affiliate URL propagation, and opportunity-linked campaign creation. Browser QA imported an offer and produced a persisted nine-factor opportunity card under local admin; its isolated demo records were removed afterward.

- Attribution validation covers confirmation and role gates, HTTPS-only destinations, country routing, parameter preservation and collision rejection, visitor-query isolation, privacy HMACs, workspace isolation, click matching, idempotent approval/reversal imports, unavailable-offer failure, and multi-currency summaries. A fresh SQLite database upgrades through `20260726_0010`; the web/desktop build includes `/attribution`; local API and route health checks return 200. The in-app browser could not revisit the loopback page because its URL policy blocked the action, so no new visual claim is made for this slice.

- Sticky page-context validation: ESLint passes and the Next.js production compiler completes. The production build then stops on the pre-existing Media Library `Uint8Array<ArrayBufferLike>[]`/`BlobPart[]` TypeScript error at `apps/web/app/library/page.tsx:134`; this sticky-header change does not touch that helper. The in-app browser could not access loopback (`ERR_BLOCKED_BY_CLIENT`), so no new visual-runtime claim is recorded.

- `update.cmd --no-pause` stopped before mutation against the current dirty workspace, then passed local-only end-to-end validation against a temporary tracked remote in both already-current and one-commit-behind states. The latter fast-forwarded exactly one commit through `git pull --ff-only --prune`; the temporary repositories were removed afterward.

- Publishing-engine session (2026-08-05): `npm run check` passes 231 tests with ESLint, TypeScript and Ruff. New suites cover the three engines' payloads, the `.env` writer, the Douyin streaming batch (interleaving, no-double-ingest, keep-going-on-failure) and Buffer's per-network metadata. The streaming tests were confirmed to fail when the batch is collapsed back into a single invocation, so they detect the regression rather than merely passing.
- Live checks this session: a Zernio key saved through the API reached a scratch `.env` without the secret appearing in the response, an unconfirmed save returned 400 and an unknown engine 422, and an engine switch persisted to `PUBLISHING_PROVIDER`. The Library assets endpoint returned 694 assets with no `publishable` or `rights_status` key and facets of channels/platforms/media_kinds only, and selecting an asset rendered all three handoff links. A Library import that previously failed `422 literal_error` now reaches business logic. TikTok Creative Center parsed live hashtag and video rows with posts and views intact. Twenty live Ad Library cards measured 0.00px between the Research and Source centres, identical bar geometry on every card, and no overflow.
- Two environment traps cost time and are worth knowing. Next dev HMR appends updated CSS after existing rules, so cascade results are wrong until a hard reload; a padding fix appeared broken twice before reloading proved it correct. And `document.hasFocus()` is false in a hidden browser pane, so `:focus` styling cannot be verified there at all.
- Engine/quota/hosting session (2026-08-09): the API suite passes 624 tests with TypeScript, ESLint, the production web build and 959/959 strings translated. 34 failures in `test_face_blur`, `test_face_swap`, `test_face_identity` and `test_effect_render` pre-date the session and are `insightface`/`onnxruntime` missing from the venv, not regressions. New suites cover plan inference per engine, the 30-day-window rule that stops a Team account reading as Free, exhaustion (including the two things that must never block a publish), route selection around a spent engine, and the storage access check.
- Live checks this session: Buffer returned `RateLimit-Policy` with a 3,000-request 30-day quota and no `RateLimit` header at all, so the plan reads Free by measurement; the R2 access check reached stage 2 and reported the refused key, which turned out to be the account ID saved as the access key ID. Forcing an engine into an exhausted state marked its three pages unavailable with the count attached while Zernio's TikTok stayed available, then the force was reverted.
- **UI changes this session were not verified by eye, and that is a real gap.** The browser automation could not get any page past its loading state: chunks fetch 200, HMR connects, no console errors, and zero API calls follow - the visible text is server-rendered, so React never hydrated. That is consistent with Chrome starving an occluded window of scheduler work, and it is not something waiting or reloading resolves. The web app has no automated tests of its own (`npm test` runs pytest), so `tsc`, ESLint and `next build` are the only gates a UI change passes. Anything shipped today that touches rendering deserves a look in a real, focused browser window.
- Two tests were reading the developer's own `.env` and passed or failed depending on who ran them: both assert Buffer refuses a local file with no public URL, which stops being true the moment R2 is configured. `test_buffer_requires_a_public_media_url` and the autopilot preview test now pin `media_hosting.status` instead.
- The backend reloader had stopped working: edits to the API, and a touch of `main.py`, left a worker from hours earlier still serving, so a change had to be chased by killing the process. A fresh process with identical arguments reloads correctly, which puts the fault in the watch being lost rather than never set up. `scripts/dev.py` now runs the backend with `WATCHFILES_FORCE_POLLING=1`; that is a mitigation, not a root cause.
- Restarting the backend repeatedly trips `dev.py`'s restart guard (5 in 60s) and takes the whole runner down with it, orphaning the frontend. Worth knowing before reaching for a restart to work around something else.

## Session close, 2026-08-10

Since this file is no longer committed, everything durable from this session was
written into `README.md` and `docs/architecture/` instead - ADR 0011 for the
fourth engine, the two media capabilities and the reversal on returning
credential values, ADR 0015 for the sub-ID rules. What follows is only state.

Done: sub IDs for affiliate networks, WoopSocial as a fourth engine, its
validate pre-check and 100 MB guard, the two-capability media rule, a Pinterest
board picker, masked credentials with a gated reveal across all three
credential surfaces, and the reloader mitigation.

`.venv/Scripts/python.exe -m pytest services/api/tests tests` passes 857.
Ruff, TypeScript, ESLint, the production web build and 974/974 translations are
clean. Note the interpreter: the system Python has no `insightface`, and running
the suite with it fails 34 face and effect tests that pass in the venv. `npm
test` uses the right one.

Two things are waiting on the operator rather than on code, and both are in
"Next recommended action" below: the R2 credentials, and looking at any of this
session's interface work in a real browser. Nothing shipped today has been seen
by eye. The automation tab never completes React hydration - chunks fetch 200,
HMR connects, no API call follows - and a clean restart of both dev servers
reproduced it exactly, which rules the servers out and leaves the browser
environment as the cause.

The dev runner currently running was started from an agent session and will stop
when that session does. Start `npm run dev` yourself before relying on it.

## Next recommended action

Fix the R2 credentials, then take the campaign autopilot through one real
end-to-end run. The order matters now: the credentials block the run.

**The blocker.** The saved R2 settings are in the wrong fields.
`R2_ACCESS_KEY_ID` holds the account ID - byte-identical to `R2_ACCOUNT_ID` -
and `R2_SECRET_ACCESS_KEY` holds `https://405e37fc…`, the S3 API endpoint URL.
Everything on the R2 bucket page is a 32-character hex string or a URL and the
fields do not say which is which, so this is the mistake the form invites. The
access check on `/publish` reports it in one line; saving now refuses both
shapes outright. The replacements come from R2 → API → Manage API tokens, with
Object Read & Write on the bucket. Until this is fixed, media hosting cannot
work, and Buffer - which has no upload endpoint and fetches the file when the
post goes out - cannot publish a local clip at all.

Then the run itself, each step being where a real problem would surface:

1. **Set posting slots** in the Schedule area on `/campaigns`. Autopilot refuses
   to invent a schedule, so with none it posts nothing and says exactly that.
2. **Check the engines.** Bundle.social still answers HTTP 403. Buffer returns
   three channels (Facebook `Naceto Books`, Instagram and Threads
   `halcyonbooks.official`) on a measured Free plan. Zernio's key works and now
   has one TikTok channel, `Tiêu Dùng Thông Minh 24h`. WoopSocial is built but
   has never been given a key, so nothing about it has met the live API - and
   it is the one engine that takes an upload and so does not need R2 at all,
   which makes it the shortest path to a first real post while the storage
   credentials are still wrong.
3. **Add a destination** on `/campaigns`, activate the campaign, queue a clip
   from the Library and approve it.
4. **Preview** before switching on. It creates nothing and mints no tracking
   code, and it is the first place a composed caption is seen whole.
5. **Switch on with delivery = draft** and confirm a draft appears in the
   engine's own dashboard. Nothing reaches an audience at this setting.

What to watch for, since none of it has met a live engine:

- `campaign_runner` calls `create_publish_job`, which builds a preview and can
  raise on validation. A destination that an engine refuses is reported in
  `last_note` and skipped rather than stopping the run - correct behaviour that
  has never actually been seen happen.
- A queue item's title now reaches the engines, so Reddit and Pinterest have the
  field they require, but no post has been sent to either.
- The disclosure and the link are composed per network at post time. The first
  live post is the first time that composition meets a real caption limit.
- Nothing has ever been out of quota, so the destinations that grey out when an
  engine is spent have only been seen by forcing the condition, never by
  reaching it.

Also outstanding, unrelated:

- Three truncated MP4s from 2026-08-05 (`moov atom not found`, 2.5-5.8 MB, still
  on disk) sit permanently in the Library's "Needs attention". Deleting media is
  destructive, so they have been left for the operator to decide on.
- `C:` is at roughly 4.2 GB free, down from 4.9 GB, and is the best available
  explanation for the instability described under Validation.
- Arabic layout on Library and Publish deserves a look from someone who reads it.

## Campaign command center, 2026-08-15

`/campaigns` is now the normal end-to-end campaign deployment workspace. Its
compact Facebook Ads Manager-inspired layout keeps the campaign rail on the
left and exposes one work area at a time: Media, Accounts, Schedule, or
Settings. The Media area uses the shared Library filters (search, effects,
channel, source, and length), visual thumbnails, persistent cross-filter batch
selection, effect rendering, shared campaign copy, and per-item edits. Accounts
loads every connected publishing engine in place. Schedule embeds posting-time
editing, presets, dry-run preview, readiness, and the guarded Deploy campaign
action. One-off approvals remain available in a collapsed advanced section.

Browser QA on the local-admin workspace confirmed 906 searchable videos, the
eight-item applied-effects filter, selection persisting after an effect-filter
change, connected Buffer and Zernio accounts loading without leaving Campaigns,
three posting slots, and deployment remaining disabled while required setup is
incomplete. Targeted ESLint and TypeScript pass. Full `eslint .` still inspects
generated `.next-dev` chunks and is therefore noisy outside the touched source.

Campaign one-off plans no longer carry their old TikTok/Instagram/YouTube/
Douyin/Other selector. Opening the advanced section reads the same live
multi-engine account inventory as Publish, removes quota-unavailable accounts,
deduplicates platforms, and offers only what can currently be reached. The API
plan schema now accepts every platform defined by publishing while retaining
Douyin and Other for existing records. A contract test prevents the two lists
from drifting again. Local browser QA returned Facebook, Instagram, Threads,
and TikTok from the connected Buffer and Zernio accounts.

## Connected campaign workflow, 2026-08-15

Campaigns, Library, Publish, and Attribution now have a continuous handoff. The
campaign workspace has focused Media, Accounts, Schedule, and Settings areas,
searchable affiliate offers, a visual filtered Library, batch non-destructive
effects, multi-account assignment, shared copy plus per-clip edits, posting
times, previews, and deployment. One-off plans select
Library media instead of asking for a raw path. Approved plans open Publish by
campaign/plan id; Publish fetches and restores the media, title, caption,
disclosure, affiliate placement, and schedule. Attribution shows a focused
campaign performance panel with plan/link/click/commission metrics and a
top-link bar chart, plus a link back to the selected campaign.

The scheduler now resolves `CampaignQueueItem.asset_id` immediately before it
creates a post and chooses the newest `edited` or legacy `blurred` Library cut.
This is what makes an effect render that finishes after queueing safe. The API
also exposes a membership-scoped single-plan endpoint for Publish handoff.

Atomic commits already made in this session:

- `f69c057 Publish campaign media from latest library edit`
- `95dc79a Expose campaign plan handoffs to Publish`
- `daccdf1 Hand off latest edited media across workflows`

Validation completed: campaign scheduler 20/20; campaign API 3/3; web library
rules 97/97; targeted ESLint; TypeScript; Next.js production build. Browser QA
at `127.0.0.1:3001` confirmed the workflow graph, compact searchable offer
popover, Library multi-select with edited-cut labels, and campaign-focused
Attribution panel. Full `eslint .` remains noisy because `.next-dev` generated
chunks are being included; the touched source files themselves are clean.

## Campaign source integration, 2026-08-16

Campaign setup now reuses authoritative data instead of asking an operator to
retype it. New campaigns select an optional imported Attribution offer. One-off
approval plans select an exact available account from Publish (account plus
engine), a saved workspace posting time, an optional Attribution offer, and a
Library clip. The plan persists `provider`, `integration_id`,
`destination_label`, and `offer_id`; its affiliate URL remains a snapshot for
the immutable approval record. Opening an approved plan in Publish restores the
saved account when it remains connected and names the corrective action when it
does not.

Migration `20260816_0022` adds those source identities and widens the database
platform constraint to every network exposed by Publish, including Threads.
Campaign creation now creates its Autopilot settings row immediately and carries
the selected offer into it. Opportunity-created campaigns do the same. The
Autopilot account picker excludes quota-unavailable accounts and identifies its
Publish and Attribution sources in place.

Validation: a fresh SQLite database migrated from zero through
`20260816_0022`; the live local database upgraded from `20260815_0021`; 28
focused campaign, Autopilot, and opportunity API tests pass; Ruff, targeted
ESLint, and TypeScript pass. In-app browser QA at `127.0.0.1:3001` confirmed
that one-off plans list the live Buffer Facebook/Instagram/Threads accounts and
Zernio TikTok account, present the three saved posting times as upcoming slots,
contain no raw affiliate URL or arbitrary date field, and that new Campaigns
use an Attribution offer selector.

## Compact cross-workspace interface audit, 2026-08-16

Audited Discover, Download, Library, Attribution, Publish, Campaigns, and Tools
in the running local application. The shared top navigation now uses one Lucide
icon vocabulary with text labels at desktop widths and accessible icon-first
navigation at narrow widths. Repeated close, chevron, refresh, search, view,
archive, link, publishing, setup, and media actions no longer rely on raw text
glyphs. Library runtime state is condensed into accessible status icons beside
the title, with details available on focus or hover.

Discover is now a compact command center instead of a landing-page hero, and
Tools cards no longer reserve landing-page-sized vertical space. Campaign,
Library, Publish, and tool actions use the shared action-icon registry, while
selects retain predictable native interaction. The README records these shared
interface conventions.

Validation completed: TypeScript and targeted ESLint pass; Next.js production
compilation succeeds before the environment blocks its final worker spawn with
`EPERM`. Node's test runner is blocked by the same Windows sandbox worker-spawn
restriction. Browser QA loaded every primary route without an application
error and visually checked the compact Library and Tools layouts at the active
1265×710 desktop viewport.

## Repeat effect-render progress synchronization, 2026-08-16

Fixed Library effect renders disappearing between the editor request and the
shared four-second job poll. Library now owns the shared job workspace whenever
its workspace selector changes. The jobs provider accepts effect jobs returned
by render, batch render, preview, and cancellation requests and merges them into
the live stream immediately; polling remains authoritative for subsequent
progress and completion. Preview cards show an indeterminate progress element
before the renderer reports its first numeric fraction.

Live browser QA changed an existing Cover-a-face effect and started a repeat
render. The dialog closed, the selected thumbnail immediately showed Applying,
the detail card showed active effect activity, and Notifications showed the new
job advancing through Finding faces. TypeScript and targeted ESLint pass.

## Attribution table layout audit, 2026-08-16

Attribution product search and bulk selection now occupy one stable toolbar.
The selection count and disabled actions remain in the same slot at zero, so
checking a product no longer inserts a row or moves the table. The select-all
state is calculated from the currently filtered rows rather than the total
number selected elsewhere. Filtered searches update the product count and show
an explicit zero-result row. Product names now carry a disclosure chevron, the
generic Links heading is clarified as Publish links, and the former
design-oriented “Everything on one row” eyebrow is now Affiliate catalog.
Desktop and 680px live-browser checks confirmed the table's document position
is identical before and after selecting a row, with no page-level horizontal
overflow. TypeScript and targeted ESLint pass.

The stable-layout requirement is now mandatory rule 8 under Interface work in
`SOP.md`. Future UI changes must reserve action/status space, avoid pushing
primary content during ordinary state changes, and verify document position at
desktop and narrow widths. Intentional user-opened disclosures and dialogs are
the documented exception.

Attribution selection now uses the reusable native `SelectionCheckbox` in
`apps/web/app/ui/selection-checkbox.tsx`. It is 18×18px with a five-pixel
radius, explicit hover/focus/checked/disabled styling, reduced-motion support,
and a real indeterminate state for partial select-all. Live browser QA verified
the size, checked accent, and mixed-state drawing; TypeScript and targeted
ESLint pass.

Primary Attribution product rows now vertically center every cell, including
the checkbox, title/subtitle, numeric metrics, link counts, and commission.
Rows have a consistent 52px minimum rhythm. Expanded detail rows remain
top-aligned because they contain multi-line offer and tracking-link sections.

The full Attribution row audit replaced the text-flow `>` glyph with the shared
Lucide disclosure icon and pins it to the trailing edge of the product cell.
Every tested row now shares the same chevron x-coordinate at desktop and narrow
widths; opening a row rotates it in place. Product titles clamp to two lines,
count columns center, hover/selected/expanded/focus states are consistent, and
expanded offer/link badges and buttons are grouped at the row end instead of
floating independently. Detail columns stack below 700px and disclosure buttons
expose `aria-controls`. Live browser QA covered collapsed, expanded, 1280px, and
680px states with no page-level horizontal overflow. TypeScript and targeted
ESLint pass.

## Build brief: autonomous Campaign operating loop, 2026-08-16

This is future work, not a description of functionality that is already live.
The goal is to turn Campaigns into a low-supervision control room that can use
Discover evidence, Library media, Attribution offers, and connected Publish
accounts to plan, rehearse, deliver, measure, and improve a campaign. The safe
default is **run by exception**: the operator approves a reusable policy
envelope and an exact first-week rehearsal, then reviews only low-confidence or
out-of-policy decisions.

The product must optimize the probability of relevant reach, meaningful
discussion, and attributable revenue. It must not promise virality, manufacture
audience engagement, fabricate testimonials, or attach an irrelevant affiliate
offer merely because its commission is attractive.

### Reuse the existing deterministic core

Do not replace the current scheduler, offer matcher, publishing adapters, or
durable jobs with one opaque AI agent. Build a planner above them and a learning
loop around them.

- `campaign_offer_matcher.py` already ranks usable offers from campaign intent,
  approved copy, hashtags, Library metadata, creative analysis, transcripts,
  restrictions, commission, and measured EPC. Low-confidence offers are visible
  for review but excluded from unattended posts. Keep relevance as a hard
  prerequisite and commercial quality as a tie-breaker.
- `campaign_autopilot.py` already composes platform-aware affiliate routes.
  Link-friendly platforms use the post, Instagram/TikTok use disclosed bio
  guidance, and thread-capable destinations can use disclosed replies for
  additional products. Treat provider capabilities and disclosure policy as
  hard constraints.
- `campaign_scheduler.py` already supplies posting slots, per-account caps,
  per-destination recycle windows, queue rotation, measured destination ranking,
  and deterministic exploration.
- `campaign_runner.py` is the irreversible boundary and durable publishing jobs
  persist across sessions. Preserve this separation.
- `autopilot-panel.tsx` already has a filtered Library picker, post packages,
  connected destinations, product recommendations, preflight, and an exact
  seven-day preview.
- Publish already knows each engine's live accounts, post types, title/caption
  limits, threads, first comments, approval holds, and media-transfer rules.
- Attribution already records governed links, clicks, conversions, refunds, and
  commission. Library already supplies source metadata, transcripts, creative
  analysis, hooks, products shown, creator/platform information, and rendered
  versions.

### Target operator flow

The common path should take four short steps.

1. **Goal**
   - Choose Reach, Discussion, Revenue, or a weighted Balanced objective.
   - Set audience, markets, languages, duration, brand voice, excluded topics,
     and prohibited claims.
   - Choose an authority level: Assist, Auto-draft, Run by exception, or
     Autonomous. Run by exception is the recommended default.
2. **Sources**
   - Start from selected Discover evidence, an existing Library collection, or
     a goal for which TrendRelay recommends eligible Library media.
   - Allow a Discover topic to be watched for a bounded period.
   - Show content runway immediately, for example: `38 eligible clips / 21 days`.
   - Apply rights, effect/render, transcript, and media-readiness requirements.
3. **Distribution and revenue**
   - Recommend connected accounts with a concise fit reason and confidence.
   - Exclude accounts that fail provider health, format, locale, market, quota,
     or required post-type checks.
   - Use smart affiliate matching by default, show the exact link route per
     destination, and allow an organic fallback when no offer clears relevance.
4. **Rehearse and run**
   - Render an exact seven-day plan: frozen media version, account, time, root
     post, first comment, thread/replies, product, disclosure, link location,
     rationale, confidence, and provider warnings.
   - Group only exceptions at the top.
   - Primary action: **Approve this week and run by exception**.

After launch the campaign lifecycle should read `Draft -> Learning -> Running ->
Paused -> Archived`. Replace overlapping status, Autopilot-switch, readiness,
and Deploy concepts with one lifecycle control and one campaign/global kill
switch.

### Campaign information architecture

Use four permanent views rather than exposing every setting at once.

1. **Overview** is the default daily control room: campaign health, pause/run,
   next three posts, today's delivery, content runway, needs-attention inbox,
   and reach/discussion/revenue summaries.
2. **Content and Conversation** contains the media pool, generated variants,
   root/comment/thread package inspector, text locks, regeneration, product
   overrides, and later a separate inbox for real audience conversations.
3. **Distribution and Revenue** merges the current Destinations and Monetization
   decisions because account, product, and placement depend on one another. Show
   account health, audience fit, supported formats, cadence, link route, bio
   readiness, exploration status, reach, discussion, EPC, conversions, and
   commission.
4. **Timeline and Results** combines proposed previews and committed jobs in one
   chronology: `Proposed -> Preparing -> Ready -> Queued -> Scheduled ->
   Published -> Replies posted -> Measuring`, plus failed, uncertain, paused,
   cancelled, and retry states.

Every timeline item must expand to show the complete content sequence, exact
destination/time, product and route, job progress, actual result, and **Why
TrendRelay chose this**. Keep change, lock, replace, pause, retry, and open-post
actions close to that explanation.

Remove the duplicate hidden one-off-plan form from Campaigns after equivalent
handoff coverage is confirmed. Publish remains the one-off composer and the
single source of truth for connections and reusable posting times. Its jobs
still appear in the Campaign timeline. Campaigns may own cadence policy but
must not duplicate Publish's connection or complete schedule editors.

### Planner decision loop

For each viable combination of `content version x copy variant x account x post
type x product x placement x time`, run these stages in order:

1. **Hard gates**: persistent media rights, immutable/render-ready media,
   account/engine health, supported post type, provider limits, offer
   availability, market/platform restrictions, link health, disclosure and
   synthetic-media requirements, claims policy, language/market fit, duplicate
   and fatigue cooldown, daily cap, and campaign budget.
2. **Candidate score**: content-to-campaign relevance, fresh Discover fit,
   content-to-product relevance, account/audience/platform/format fit, expected
   discussion, expected reach/retention, attributable revenue, time fit,
   freshness, fatigue, risk, confidence, and a bounded exploration bonus.
3. **Compose**: generate platform-specific root copy, title/description,
   hashtags, a genuine question where appropriate, first brand-owned comment,
   ordered thread/replies, CTA, and clear disclosure. Provider capability and
   affiliate-placement rules remain deterministic.
4. **Rehearse**: freeze all publication inputs and run the existing provider
   preflight. Route only exceptions outside the approved policy envelope to the
   operator.
5. **Execute and reconcile**: deliver through durable jobs, retain provider post
   IDs/permalinks, and reconcile uncertain outcomes before retrying so an
   ambiguous response cannot produce a duplicate post.
6. **Observe and learn**: collect provider-supported views, watch time,
   completion, likes, comments, shares, saves, clicks, conversions, refunds,
   commission, and failures at useful windows such as 2h, 24h, and 7d. Learn
   creative, account, time, product, and placement effects independently.

Cold-start choices must remain deterministic and explainable. After sufficient
observations, a contextual bandit or equivalent explore/exploit policy is
appropriate. Never display sparse estimates as measured predictions.

Objective scoring should use the campaign's declared priority:

- Reach: views, retention/completion, shares, saves, and audience growth.
- Discussion: unique commenters, reply depth, comment velocity, and meaningful
  responses rather than raw engagement-bait counts.
- Revenue: qualified clicks, approved conversions, net commission, and refunds.
- Balanced: explicit weights with minimum relevance, quality, and safety gates.

### Content packages and conversation boundaries

Create one canonical message/intent for an asset, then generate separate
platform/account variants. Persist the root post, first comment, ordered
thread/replies, CTA, product IDs, placement, disclosure, locale, evidence,
generator/model/prompt version, confidence, policy decision, and edit history.
Allow users to lock any node and regenerate one node or the entire package.

Planned replies owned by the publishing account are part of a post package.
Replies to real users are a different workflow. Do not auto-reply to audience
comments until the relevant provider offers authenticated comment reading,
moderation, rate limits, deduplication, and identity-safe reply endpoints.
Initially, collect real comments into a Conversation inbox and suggest answers.
Later, allow only bounded FAQ/acknowledgement automation. Complaints, refunds,
privacy, harassment, regulated claims, legal/medical/financial topics, and
low-confidence cases always escalate. DM automation is out of scope.

Every promotional unit needs a clear disclosure close to the endorsement. A
bio may hold the clickable destination without being the only disclosure.
Never manufacture comments, testimonials, controversy, or audience identities.

### Discover integration

The current Discover idea basket is ephemeral and its direct Campaign request
persists only the synthesized name, objective, audience, markets, and languages.
Unify it with the existing evidence-backed Opportunity-to-Campaign path.

Add a persistent `CampaignSignal` or `CampaignEvidence` record containing source
URL/provider, topic/post/creator, source kind, region/language, observed metrics,
trend shape/velocity, collection time, freshness/expiry, evidence text, proposed
angles, and provenance. Add **Use in existing Campaign**, **Create smart
Campaign**, and **Watch this signal** actions in Discover.

A watched signal may propose a new angle when it accelerates and retire a
trend-led variant when it fades or becomes saturated. Match signals to owned
Library assets using transcripts, metadata, creator/platform affinity, creative
analysis, effects, hooks, structure, and source performance. Discover evidence
is inspiration, not proof of media rights. If no eligible owned media exists,
create a Studio production brief rather than downloading/reposting external
content automatically.

### Correctness and data prerequisites

Complete these before live unattended publishing:

1. Introduce a first-class `PublicationExecution` that separates reservation
   from provider-confirmed publication. Suggested states are `proposed`,
   `preparing`, `ready`, `reserved`, `queued`, `provider_accepted`, `published`,
   and `measured`, with `failed`, `uncertain`, `cancelled`, and `paused`
   branches. Only reconciled publication updates `times_posted`, rest windows,
   or learning data.
2. Freeze asset-version ID and SHA-256, effect recipe/version, content variant,
   offer IDs, placement, tracking-link IDs, destination capability snapshot,
   and scheduled time. Preview and delivery must use the same frozen inputs.
   Never silently fall back to an unedited original when preparation fails.
3. Mint attribution links per publication execution and placement while keeping
   campaign, destination, offer, and product dimensions. Current aggregation is
   too coarse to learn clip, copy, time, or root-versus-reply effects.
4. Persist remote post ID, permalink, delivery/reconciliation state, failure
   class, and timestamped native-performance snapshots.
5. Restore a persistent usage-rights gate. A valid local path is not proof that
   TrendRelay may publish the media.
6. Add automatic pause/circuit breakers for repeated authorization failures,
   provider outages, uncertain deliveries, broken links, unavailable products,
   missing media, disclosure failures, unusual rejection rates, high negative
   sentiment, expired signals, insufficient content runway, and account
   rate-limit warnings.

For Instagram/TikTok-style bio routes, prefer a stable disclosed campaign
landing page containing publication-specific product cards where provider and
affiliate rules permit. Do not silently rotate a shared redirect or claim that
TrendRelay updated a profile when the connected engine cannot verify it.

### Agent-sized implementation plan

Keep each phase independently testable and commit it atomically with a
descriptive message. Do not combine schema/control-plane work with a wholesale
visual rewrite in one commit.

#### P0 - trustworthy execution

- Add `PublicationExecution`, frozen publication inputs, reconciliation states,
  and provider remote identifiers.
- Change queue bookkeeping so reservations and failed jobs do not count as
  published or start a recycle interval.
- Add publication/placement-level tracking links and performance joins.
- Add persistent rights/readiness gates and circuit breakers.
- Tests: idempotent retry, uncertain provider outcome, failed post not counted,
  preview/delivery asset hash equality, edited render not silently replaced,
  link attribution dimensions, worker restart recovery, and pause behavior.

#### P1 - useful guarded autonomy

- Persist Discover evidence and unify Discover/Opportunity campaign creation.
- Add the four-step campaign setup flow and authority levels.
- Add provider-neutral, grounded content variants with provenance and locks.
- Add explainable account recommendations and the exception inbox.
- Consolidate Campaigns into Overview, Content and Conversation, Distribution
  and Revenue, and Timeline and Results without duplicating Publish settings.
- Tests: evidence survives handoff, no generation without grounded inputs,
  low-confidence products remain organic/review-only, unsupported provider
  nodes are omitted or handed off explicitly, and exact seven-day rehearsal.

#### P2 - closed-loop optimization

- Ingest native post-performance snapshots from providers that support them.
- Expand destination ranking beyond EPC to objective-weighted account, content,
  format, time, and discussion evidence.
- Add fatigue/novelty control, winner scaling, stop-loss rules, and bounded
  exploration.
- Tests: sparse data stays unranked, objective weights change choices, failures
  never become positive observations, unavailable offers pause/replace safely,
  and exploration remains deterministic/reproducible.

#### P3 - discussion intelligence

- Add planned timed/conditional brand-owned thread nodes where the provider can
  deliver them safely.
- Ingest real audience comments into a Conversation inbox with sentiment,
  deduplication, moderation state, and suggested responses.
- Keep auto-replies disabled until provider, moderation, identity, and rate-limit
  contracts are covered by integration tests.

#### P4 - limited full autonomy

- Graduate only healthy whitelisted accounts from drafts to guarded scheduling
  and then live publishing after an operator-visible learning period.
- Add campaign, destination, and workspace-level budgets/caps and kill switches.
- Add portfolio allocation only after post-level measurements are trustworthy.

### First milestone definition of done

The first agent should target **Guarded Campaigns**, not full comment autonomy.
It is done when an operator can start from Discover or Library, complete the
four-step setup, inspect a frozen seven-day platform-native rehearsal, approve a
policy envelope, and let the campaign create drafts or schedules while only
exceptions require review. The timeline must explain each account, product,
placement, and time choice; low-confidence or irrelevant offers must fall back
to organic; jobs must persist across restarts; and a failed or uncertain
provider response must never be counted as a successful post.

Primary implementation touchpoints:

- `apps/web/app/campaigns/autopilot-panel.tsx`
- `apps/web/app/campaigns/page.tsx`
- `apps/web/app/discover/campaign-idea-composer.tsx`
- `apps/web/app/discover/discover-dashboard.tsx`
- `services/api/src/trendrelay_api/autopilot_models.py`
- `services/api/src/trendrelay_api/campaign_autopilot_api.py`
- `services/api/src/trendrelay_api/campaign_autopilot.py`
- `services/api/src/trendrelay_api/campaign_offer_matcher.py`
- `services/api/src/trendrelay_api/campaign_scheduler.py`
- `services/api/src/trendrelay_api/campaign_runner.py`
- `services/api/src/trendrelay_api/publishing_api.py`
- `services/api/src/trendrelay_api/attribution_models.py`
- `services/api/src/trendrelay_api/opportunities_api.py`
- `services/api/src/trendrelay_api/media_models.py`

Before implementation, read `docs/design/campaign-autopilot.md`, ADR 0011 on
governed publishing, ADR 0012 on the campaign/manual boundary, ADR 0013 on
explainable opportunities, ADR 0014 on immutable media, ADR 0015 on attribution,
and ADR 0017 on Discover. Preserve the current rules that irreversible actions
remain behind `campaign_runner.py`, provider capabilities come from the actual
delivering engine, and all autonomous choices remain explainable and auditable.

## P0 trustworthy execution: built, 2026-08-16

Commit `3b8cdaa` delivers the whole P0 phase of the brief above.

- `publication_models.PublicationExecution` (migration `20260816_0027`)
  separates reservation from provider-confirmed publication. The full state
  vocabulary from the brief is declared; the runner currently moves through
  `ready -> queued -> published / failed / uncertain / cancelled`. Frozen
  columns hold the Library version id and sha256, effect ids, composed
  title/caption/comment/thread, placement, reason, offers, minted links, the
  destination snapshot, and the provider's remote post ids and permalinks.
- Queue bookkeeping is honest: `record_scheduled` is reservation-level only
  (it still advances `posts_scheduled`, which drives exploration), and
  `record_published` - called from `reconcile_executions` on a settled
  execution - is the only place rest intervals, rotation and `times_posted`
  move. The planner reads held slots, held items and daily-cap pressure from
  pending executions; an `uncertain` execution (timeout-class failure, or a
  pruned job record) keeps holding both, because the post may exist.
- Delivery integrity: media is frozen at plan time by version id and stored
  hash, re-verified by content hash before the job is created, and a missing
  or changed file fails the execution by name and pauses the queue item.
  There is no silent fallback to the unedited original any more.
- Per-publication links: caption/comment posts mint a fresh tracking link per
  post (`mint_post_link`), carrying the content hash in the sub-ID content
  slot per ADR 0015. Bio routes keep the stable destination link. Destination
  ranking aggregates all three link generations, so per-post links do not
  scatter account measurement.
- Circuit breakers: three recent auth-class refusals or two uncertain
  deliveries pause the campaign with the reason in `last_note`. Reconciliation
  runs at the top of every worker tick, before planning.
- `GET /campaigns/{id}/autopilot/executions` serves the timeline for P1's UI.
- 13 new tests in `test_publication_executions.py` cover the brief's matrix:
  reservation-not-post, confirmed publication, failure frees the slot,
  uncertain holds it, idempotent re-planning, frozen-hash delivery equality,
  changed/missing media failing by name, per-post vs bio links, both
  breakers, and reconciliation from a fresh session (worker restart). Full
  suite: 1582 passed; ruff clean; fresh database migrates to `20260816_0027`.

Deliberate deferral: the brief's "restore a persistent usage-rights gate" is
not in this commit. Usage rights were retired as a product concept (see
Decisions in force) and re-enforcing them without the P1 policy envelope would
re-block every import at `unknown` - the exact dead end that led to the
retirement. It should return as part of the P1 policy envelope with a real way
to satisfy it.

Next: P1 - persist Discover evidence (`CampaignSignal`), unify the
Discover/Opportunity campaign creation paths, the four-step setup with
authority levels, and the four-view consolidation. The executions endpoint
above is the data source for Timeline and Results.

## P1-P4 built through the guarded spine, 2026-08-17

Every phase of the brief now has its backend spine and the operator surface
for the guarded loop. Commits: `b87433c` (authority + exception inbox),
`023a114` (account recommendations, tab-grid fix), `c380fbd` (objectives,
measurement boundary, stop-loss, offer-availability guard), `df9e00e`
(conversation inbox, graduation, weekly cap, kill switch), `44b9ec6`
(operator controls in the panel). Signals were built in a parallel session
(`c65e085`, `signals_api.py` - watch, attach, status, staleness).

- **Authority levels** on `CampaignAutopilot` (migration `20260817_0029`):
  assist holds every post, auto-draft forces engine drafts in the publish
  request itself, run by exception (default, equals prior behaviour) holds
  only what trips a rule, autonomous holds only hard gates. A low-confidence
  pinned product holds at every level. Held posts are `proposed` executions
  with `held_reason`; `GET .../autopilot/exceptions`, `POST
  .../executions/{id}/approve|dismiss`. Approval re-verifies media and
  delivers the frozen record through the same `_publish_execution` builder
  the tick uses.
- **Account recommendations** (`campaign_accounts.py`, POST behind the same
  confirmation as Publish discovery): per-account reasons - engine
  deliverability, link placement per network, measured history across every
  campaign (5 settled conversions = the ranking bar) - and no invented
  audience-fit figures.
- **Objective-weighted ranking** (migration `20260817_0030`, `priority` on
  autopilot): revenue (EPC), reach (views/post), discussion (comments/post),
  balanced (normalised blend of qualifying axes only), each refusing to rank
  under its evidence bar. Stop-loss parks a destination at 50 clicks with
  nothing settled; exploration keeps its way back and remains deterministic.
- **Measurement boundary** (`campaign_measurement.py`): snapshots at
  2h/24h/7d against provider-confirmed posts only; a failed delivery can
  never be measured. `PROVIDER_METRIC_READERS` is no longer empty and no
  longer optional: Zernio, Buffer and Bundle.social all read back, and an
  engine without a reader must carry `no_metrics_reason` explaining why its
  API cannot - exactly one of the two, enforced by a test. WoopSocial is the
  only one excused, on the evidence of its own OpenAPI document. A read that
  fails returns `None` and leaves the window due; it never writes zeros,
  because a captured window is never captured again. Buffer's reader also
  yields to publishing when its 250-a-day budget runs low.
- **Conversation inbox** (migration `20260817_0031`,
  `campaign_conversation.py`): comments ingested idempotently where a
  provider can be read (none today, said honestly), routed by readable
  keyword rules (refund first - it has a clock), en+vi markers,
  over-matching by design. No reply endpoint, no reply function, and a test
  that must be deleted before one can drift in. Triaging is
  answered/dismissed only.
- **Limited autonomy fences**: autonomous authority is earned
  (`graduation_block`: 10 provider-confirmed posts, no unresolved
  uncertain), an optional rolling `weekly_post_cap` across all destinations,
  and a workspace kill switch (`POST /campaigns/autopilot/kill-switch`,
  owner-only) that stops every campaign with the reason written on each.
- Full suite 1655 passed; ruff clean on all touched files; fresh database
  migrates from empty through `20260817_0031`; live DB upgraded; panel
  controls and exception list verified in the running browser.

Still open, and why, for whoever continues:

- The four-step setup wizard and the full four-view consolidation are
  interface work over now-existing APIs; the parallel campaign-frontend
  stream owns that area (it removed the one-off plan form and unified the
  timeline this same day).
- Generated content variants with provenance/locks: no reviewed generation
  provider exists and "Autopilot never writes copy" is a decision in force;
  the queue item's operator-authored root/comment/thread with engine
  validation is the current content package.
- Portfolio allocation (P4) stays unbuilt by the brief's own gate: it waits
  for trustworthy post-level measurements, which need a first metrics
  reader.
- The usage-rights gate deferral from P0 stands.

## Placement, language, and honest comments, 2026-08-17

Commit `dde26b7`, from the operator's audit of the posting flow.

- **Post-then-comment**: only Buffer can post a first comment, and only on
  Facebook, Instagram and LinkedIn (`first_comment_deliverable` in
  `publishing.py` is the one place that knows). Threads gets replies through
  Buffer's thread array instead. Every other engine's delivery preview now
  states that it drops a first comment or a thread, instead of dropping them
  silently between preview and delivery.
- **Link placement is configurable per destination** (`link_placement` on
  `campaign_destinations`, migration `20260817_0032`): 'auto' keeps the
  network's own decision and stays the recommendation; caption/first
  comment/bio are the operator's call, honoured with the trade-off written
  into the placement reason. A first-comment override through an engine that
  cannot deliver one falls back loudly. The scheduler, the composer, the
  runner's link minting (bio = stable link, post placements = per-post link)
  and the destination view all read one resolution. The panel has the select
  per destination row; the view returns both the stored setting and what it
  resolves to.
- **Post language** (`post_language` on `campaign_autopilot`): composed
  scaffolding - disclosure default, bio hint, product labels - follows the
  campaign's languages at creation (all three creation paths) instead of
  defaulting to English. `LOCALISED_TEXTS` in `campaign_autopilot.py` holds
  en and vi; extending a language is adding an entry. Changing the language
  swaps a still-default disclosure/bio hint; operator-written text is never
  touched. Panel has the selector.
- 16 new tests in `test_campaign_link_placement.py`. Full suite: campaign,
  publishing and attribution suites all green; 6 concurrent failures in
  captions/recolour/still-effects belong to the parallel captions stream.
- Live database upgraded to `20260817_0032`.

## Campaigns walked as a paying operator, 2026-08-17

The flow above was then audited in the browser as a paid user would see it
(commits `0addae8`, `cc17905`). What held: settings each carry a one-line
consequence, placement badges sit on the account they govern with the reason
as a sentence, the preview names what each engine will drop. What did not,
now fixed:

- Existing campaigns predating `post_language` still spoke English at a
  Vietnamese audience; migration `20260817_0033` backfills language (and the
  still-default disclosure/bio hint - operator-edited text is never touched)
  from the campaign's own `languages`. Verified against the live database:
  the one real campaign flipped to vi.
- The Timeline tab counted only calculated preview posts, so it said `0
  upcoming` while committed jobs still waited to go out; queued/running
  committed jobs now count.
- The card titled `Upcoming posts` kept delivered jobs on screen; retitled
  `Posting timeline`.
- A dead local API surfaced as the browser's bare `Failed to fetch`;
  `explainFailure` in the panel now names what did not answer.
- `.autopilot-placement-choice` had no CSS (label ran into the select), and
  `1 planned posts` lost its plural.

Known and deliberately left: the same last-run sentence can appear twice on
the Timeline tab (once as last-run status, once as the preview's refusal -
different facts, same wording); the panel's own chrome is hardcoded English
regardless of the app locale switcher, a pre-existing, panel-wide condition
separate from post language. Web/API servers were down for the latter half
of the walk (they are the user's; dev runner uses backend 8011, frontend
3001 - the unrelated squatters on 8000/8080 are harmless).

## Posts carry the network's own link; internal tracking retired, 2026-08-17

Operator direction: posts attach the Shopee-provided short link
(`https://s.shopee.vn/...`) directly; tracking happens on the network's
side for now, so no internal tracking in the posting path. Commit
`49ebcf8`, ADR 0022 (supersedes the posting half of ADR 0015).

- `offer_link_url` in `campaign_autopilot_api.py` is the one place a post's
  link comes from: the offer's `affiliate_url` verbatim, https-validated.
  The runner's `link_for` no longer mints (`link_url_for`/`mint_post_link`
  deleted; restorable from history); executions record
  `{offer_id, placement, url, tracking_link_id: None}`.
- Previews and the deploy preflight compose with the real link - no more
  `preview.invalid` placeholders, and captions are length-checked as they
  will actually post. The panel's link mask and `tracking_code` field went
  with it.
- `carries_tracking_link` in `publishing.py` counts the network's short
  hosts (`attribution_shopee.SHORT_HOSTS`) as tracked alongside legacy
  `/c/` links; the Publish attribution note says the network's report does
  the counting. NOTE: the parallel session's commit `482edfe` swept the
  detector half of this change into their commit; the note text and the
  rest are in `49ebcf8`.
- Retired, not removed: redirector, TrackingLink, sub-IDs, click/conversion
  models and hand-made Attribution links all still work; old executions
  still reconcile (the scheduler's ranking guard skips `None` link ids and
  simply accrues no new evidence).
- Full API suite 1693 passed (captions/recolour/still-effects excluded as
  the parallel stream's in-flight area).

## One lever, one timeline, 2026-08-17

Operator feedback: "why so many complicated overlapping steps" - approve
each item, press Deploy, and switch on were three confirmations of one
decision, and the page told the posting story twice (Committed pipeline +
Content Calendar). Commits `58f4840` (timeline links, dedupe, dropdown
restyle) and `79cca45` (the redesign):

- **Content arrives ready.** `add_queue_item` creates `state="approved"`;
  approval lives at the execution layer (authority dial + exception inbox),
  where a frozen post is what gets approved. `draft` remains as a parking
  brake; migration `20260817_0034` freed rows never deliberately parked.
- **The switch is the deploy.** `save_autopilot` on enabled False→True
  activates the campaign and runs it immediately; empty queue arms instead
  of erroring; archived campaigns refused. The deploy bar and the
  "review then deploy" switch-refusal are gone from the panel. The
  `/autopilot/deploy` endpoint remains for API compatibility.
- **One timeline.** The page passes its hand-planned calendar entries into
  the panel as `plansSlot`, rendered inside the Posting timeline card; the
  standalone CONTENT CALENDAR section is gone. Committed jobs and plans
  both show their media via the Publish media-preview endpoint
  (`/publishing/media/preview?path=`) instead of a file path. Committed
  entries link: title → engine-reported permalink, account label →
  `profile_url` (handle-shaped labels only, `campaign_autopilot.py`).
  Retries dedupe by (destination, date, caption), newest wins.
- Verified live in the browser: video players in both entry kinds, account
  links, calendar inside the timeline. Old committed captions still show
  legacy `localhost/c/` links - historical posts, expected.
- Live DB at `20260817_0034`; the user's two parked packages are now in
  rotation (their delivery mode is engine drafts).

## Operator directives (standing), recorded 2026-08-18

The operator's instructions from the 2026-08-17 sessions, distilled so no
future agent re-litigates them. The same list, with what each supersedes,
is in `docs/design/campaign-autopilot.md` § "Operator directives".

1. **Direct affiliate links only.** Posts carry the network's own short
   link exactly as imported into Attribution (`https://s.shopee.vn/...`) -
   in Publish, in Campaigns, everywhere. Tracking happens on the network's
   side for the time being; internal tracking (the `/c/` redirector path in
   campaigns) stays retired until the operator says otherwise. ADR 0022.
2. **Post then comment** on link-viewable networks (Facebook, Threads):
   first comment where the engine delivers it, reply thread on Threads,
   and a loud preview note from any engine that cannot.
3. **Post language is a setting** - posts, comments, threads and replies
   follow the campaign's language; never default silently to English.
4. **Link placement**: network decides by default, per-destination override
   (caption / first comment / bio) honoured with its trade-off stated, or
   refused loudly when undeliverable.
5. **Content-first, one gate at the execution layer** (refined 2026-08-18,
   commit `e196781`): content from Discover or Library → auto-matched
   affiliate products → comments composed around the links → every frozen
   post **waits for approval before it reaches an engine**. Below earned
   autonomy nothing pushes without a person; auto-draft's approval delivers
   an engine draft only; autonomous still holds what the completeness check
   refuses. Approve refuses unfinished posts (placeholder copy, products
   without their link in the text, engine-refused requests) with the list of
   what to fix, leaving the post held. The scheduler skips unwritten
   packages. The Post automatically switch remains the deploy; the deleted
   queue-approve/deploy ceremony may not return.
6. **One timeline**: planned and committed posts in one calendar with
   playable media (Publish parity) and delivery status; succeeded entries
   link to the live post or account page; no duplicated entries or
   repeated messages.
7. **Paid-user finish**: reasons on every automatic decision, consequences
   on every override, modern control styling, no raw file paths where a
   preview can play, compact supporting chrome.
8. **Tabs earn their existence** (recorded 2026-08-18): prefer none; where
   tabs must exist, each does exactly one thing, none overlap, and left to
   right they read as a logical, coherent flow. Campaigns' three follow the
   pipeline: Queue (packages in rotation) → Posts (what leaves, approvals
   included) → Setup (every knob). Labels that could describe each other's
   contents fail the test.

## Improve pass against the directives, 2026-08-18

Commits `9f29371`, `620c4df`, `dace85e`, walked live as a paid user:

- The page hero said "Plan once. Approve once. Publish anywhere." after the
  approve-once ceremony was deleted; all seven locales now describe the real
  flow (content in → products matched → posted with a clickable link). The
  locale files carried the parallel captions stream's uncommitted hunks, so
  only the hero hunks were staged (patch-per-file via `git apply --cached`).
- Content queue cards show their thumbnail (reusing `AssetThumbnail` +
  a lighter `.autopilot-queue-thumb` wrapper), display titles drop the file
  extension (`displayTitle`, display-only), and the API's `needs_copy` flag
  - sent but never rendered - now warns that the placeholder body posts
  unless somebody writes copy.
- Delivered timeline rows resolve `asset_id` through the queue item
  (`preview_autopilot` deployed builder) so they show the frame that posted
  instead of an empty play placeholder.
- The summary strip's "delivery warnings" counted only preflight refusals
  and showed 0 above rows marked failed; failed deliveries now count.
- Noted, left alone: the parallel session rebuilt the day-grouped timeline
  (TimelineEntry, stat strip, engine names per row) - coordinate before
  touching it.

## Media preflight: refuse what the network will refuse, 2026-08-18

Commit `5d33e78`. The live data held three failed Threads jobs, all the
same 2160×3840 clip, all refused by Buffer relaying Meta's "Video width
must be no more than 1920px for Threads" - after delivery, three times.

- `PLATFORM_MAX_VIDEO_WIDTH` in `integrations/publishing.py` encodes only
  limits an engine has actually enforced (threads: 1920). Probing uses
  `media_library.probe_media` through `_video_dimensions`, cached per
  (path, mtime); unknown dimensions refuse nothing.
- `video_fits_platform(platform, video_path)` is the one source of truth.
  `_validate_request` raises on an unfit video, which covers Publish
  one-offs, the campaign deploy preflight (`_would_be_accepted`) and the
  delivery guard in one place.
- The planner (`plan_campaign` selection loop) skips an eligible item whose
  clip the destination's network refuses, notes it ("Skipped on {label}:
  …"), and takes the next item that fits - the same wide clip is fine on
  TikTok, so one queue keeps feeding both instead of manufacturing the
  same failed job every tick. Carousel items skip the check.
- Tests: three validation cases in `test_publishing_providers.py`, routing
  in `test_campaign_scheduler.py`. Full suite 1732 passed. The live skip
  note cannot show yet - both queue items are inside their 30-day rest
  window - but the failing clip was probed directly (2160×3840) and the
  check catches it.

## Approval is the gate, and only finished posts pass it, 2026-08-18

Commit `e196781`, from the operator's audit directive: approve before any
engine push, and approving must mean the post is complete.

- `_hold_reason` (campaign_runner): every authority below `autonomous`
  holds every post as `proposed` - run_by_exception no longer publishes
  unattended. Autonomous (graduation-gated, kill-switchable) remains the
  one pass-through, and even it holds what `finalization_problems`
  refuses (content check, `engine_check=False`).
- `finalization_problems(autopilot, execution, engine_check=True)`: empty/
  placeholder caption, products attached with no affiliate link (or link
  absent from the post's own text on non-bio placements), and - at the
  approve gate - the engine's own `_validate_request` verdict (covers
  caption limits, media width caps, hosting). `approve_execution` refuses
  with the list and leaves the post held, not failed.
- The scheduler skips `PLACEHOLDER_BODY` packages with a note instead of
  letting them hold slots. `PLACEHOLDER_BODY` moved to
  `campaign_autopilot.py` (leaf) so scheduler/runner/API share it.
- Panel: authority options and the inbox header now say the truth
  ("Nothing reaches an engine before it is approved here, exactly as
  frozen"). No migration needed - behavior is code-side; the user's
  run_by_exception campaign holds from the next tick.
- Tests rewritten to the new contract (`test_campaign_authority`), pipeline
  suites run under earned autonomy, plus unfinished-approval-refused and
  unwritten-package-skipped cases. Full suite 1735 passed.
- Live browser verification blocked at the moment: the parallel session is
  mid-edit in `app/layout.tsx` (script-in-component hydration error, blank
  page) - theirs, not this slice's.

## Products chosen where the package is made, 2026-08-18

Commit `e4465d8`, from the operator: Add to queue silently skipped the
product decision. The add form now carries a Products step - Smart match
default (with an honest note when the campaign has offers off), or Pin
products, which loads `offer-recommendations` (fit score, reasons,
confidence) into checkboxes capped at five; pins ride `offer_ids` on the
queue create, the submit button counts them, and Choose-another/submit
reset the draft state. Verified end to end in the browser: package added
with the pinned Shopee offer, queue card shows the product chip, and the
next plan carries `1 PRODUCT` with per-network placement on both accounts.

Coordination near-miss, worth remembering: the first commit of this slice
swept the parallel session's in-flight `accepts_carousel` hunks into it.
Recovered with `git reset --soft HEAD~1` and a byte-safe patch script that
classified hunks by marker strings and split the one mixed hunk
(`scratchpad/stage_products2.py` pattern). When both sessions edit the
same file, `git add <file>` is never safe - stage by hunk.

## One page, approval up front, previews like Publish, 2026-08-18

Operator: combine the Content and Distribution & revenue tabs, neat and
efficient for maximised autonomy, with the approval flow included and post
previews at parity with Publish. Built and verified live; most of the diff
rode into the parallel stream's commit `fd62b88` (their sweep this time -
each session has now swept the other once), with the approval-row CSS in
`da608af`.

- The panel is one page: the three tab panes are gone, every area renders
  in one flow, and the old tab strip is an at-a-glance stat nav whose
  buttons scroll to `campaign-area-{media,revenue,schedule}` anchors.
  `jumpTo` keeps its lazy loads; the exceptions load with the page.
- "Needs your approval" is a card directly under the campaign header:
  every held post renders through Publish's own `PostPreview` (gated
  player streaming the frozen media via `/publishing/media/preview`,
  account header, the composed caption exactly as it will read) beside
  its schedule, first comment/replies, hold reason, and Approve/Dismiss.
  Approve still refuses unfinished posts with the list of what to fix.
- `_execution_view` now carries `media_path`, `image_paths`, `post_type`
  so an approval can show the post rather than describe it.
- `.campaign-approval-list .post-preview-frame { inline-size: 100% }`
  matters: without a poster the frame has no intrinsic width and collapses
  invisibly.
- Verified live: two real held posts (TikTok bio-placement product line;
  Threads caption carrying the direct `s.shopee.vn` link), player streams.
  Deliberately did NOT click Approve - that publishes to the operator's
  live accounts and is their call.
- Task #7 pending: enhance the Timeline against `References\Posts`
  reference designs (operator request; folder is local-only).

## Calendar view and the three-pane restructure, 2026-08-18

Tasks #7 and #8, both done and verified live. Commits: calendar in
`0da9446`; the restructure's hunks were swept into the parallel stream's
`7a4e415` (the ongoing shared-file hazard).

- **Calendar** (task #7): `References\Posts` holds five screenshots of the
  operator's own Buffer/Zernio accounts. Parity gap was the calendar: the
  Posting timeline now has a List | Calendar switch - month grid in the
  schedule timezone, day chips (time + network icon + title) coloured by
  outcome, today outlined, "+N more" past three, ‹ Today › month nav
  (`TimelineCalendar` in autopilot-panel.tsx, CSS in console.css).
  Per-post engagement metrics were deliberately NOT added: the engine
  metric readers are empty on purpose and zeros would fake parity.
- **Three panes** (task #8, operator: "now a mess... endless scroll...
  dead simple"): the one-page flow became three panes named for jobs -
  **Posts** (timeline + list/calendar; default for active campaigns),
  **Content** (queue + add; default otherwise), **Setup** (all settings,
  destinations, product matching - out of the way once set). The
  **approvals card stays above the panes**, never hidden. `jumpTo`
  translates the readiness rows' old names (media/accounts/settings/
  schedule) onto panes.
- The user's held Threads post (first comment carrying the direct
  s.shopee.vn link) rendered correctly through every step; approval was
  deliberately left unclicked - live accounts are the operator's call.
- New untracked `CAMPAIGNS-FLOW.md` at repo root is the parallel
  session's; leave it to them.

## Editable approvals, publish-now, and the first-comment verification, 2026-08-18

Tasks #10 and #11, both closed. Commit `ad18832` (mine); the first-comment
capability itself is the parallel stream's `19047cf` + follow-ups, verified
here rather than rebuilt.

- **Held posts are editable** (task #10): `PATCH
  /{campaign}/autopilot/executions/{id}` rewrites title/caption/first
  comment/replies while `proposed` - the approval promise holds by
  construction (the operator wrote it). Media stays frozen. The approve
  gate still refuses an edit that blanks copy or drops the link. Panel:
  Edit opens the inline form; Approve · Publish now · Edit · Dismiss.
- **Publish now**: approve with `publish_now: true` → delivery "now" at
  the current time (confirmed in the UI first). Auto-draft authority still
  delivers a draft - its promise survives even an explicit now.
- **First-comment affiliate links verified end to end** (task #11):
  `first_comment_deliverable` = Buffer × (facebook/instagram/linkedin via
  the GraphQL `firstComment` field, gated by Buffer's paid plan) ∪
  (threads/twitter/mastodon/bluesky via the thread array, free - the first
  comment rides as the LAST reply). Campaigns composes product links into
  first_comment per placement (live-verified on the operator's Threads
  held post carrying the s.shopee.vn link); Publish has the field plus the
  AffiliateLink product-attachment component with per-platform placement,
  and its helper text names exactly where the text lands (comment / next
  reply / skipped). Nothing needed building; the tests are parametrized in
  `test_campaign_link_placement.py` (FOLLOW_UP_BY_NETWORK).

## The first-comment message stopped blaming the network, 2026-08-18

Operator bug report: a Facebook page selection in Publish said "None of
the chosen networks take a first comment." Two real causes, both fixed
(commits `57ead66`, `fbab6e9`):

- **The plan**: Buffer Free withholds `firstComment` (identified by the
  3,000-req/30-day rate-limit figure). `provider_status` now returns
  `first_comment_locked_platforms` alongside `first_comment_platforms`.
- **The engine**: the operator's Facebook page runs through Zernio, which
  cannot send a first comment on any plan.

`commentBlockedNote` in publish/page.tsx computes one worded reason -
network / plan / engine - shown on the add-to-first-comment tooltip and
where the hidden field would have been (`.publish-plan-note`), in seven
languages (`commentPlanLocked`, `commentEngineLocked`). Each names its
remedy; on Facebook the caption is offered since links are clickable
there. Test: `test_a_plan_locked_first_comment_blames_the_plan_not_the_
network` pins the plan split (Free → locked, unnamed plan → available,
thread networks never withheld). Verified live against the Zernio page.

## The de-noise pass, and the tab rule, 2026-08-18

Task #9 done; commits `625be3b`, `102247e`. Operator: dead simple, no
fuss; then "these 2 tabs seem overlap" (Posts vs Content); then the rule
now recorded as directive 8 - tabs each do one thing, none overlap, left
to right a coherent flow.

- Cut: the static autopilot lede (the dynamic summary says it with real
  numbers), the timeline's "durable publishing jobs" lede, the
  Runs-by-itself eyebrow over "Autopilot", the page hero's intro
  paragraph, the what-Publish-owns lecture (now one line: "Need a one-off
  post instead?"), and the active-days/accounts stat numbers. Posting
  times moved into Setup with a one-line lede.
- Renamed and reordered: Content → **Queue** ("packages in rotation"),
  tabs now Queue → Posts → Setup (pipeline order); Posts stays the
  default pane for a running campaign.
- **Git hazard escalated**: a parallel-session commit overwrote HEAD and
  reverted the three-pane restructure while the working tree kept it;
  `625be3b` restored it. When diffing shows "my already-committed work"
  as new hunks, HEAD was clobbered - re-stage it, do not discard. All 17
  panel hunks that round were verified mine before wholesale staging.

## FeatureReach: which chosen networks a feature applies to, 2026-08-18

Operator goal: with several networks selected, show at a glance WHICH of
them a feature (first comment, thread, topic, visibility) reaches - in
Publish, Campaigns, and every applicable place. Commit `8fd2e45`.

- `FeatureReach` in `publishing-icons.tsx`: renders the chosen networks
  as a row of small `PlatformIcon` marks, carriers in colour, the rest
  greyed with a red diagonal strike (`.feature-reach` in styles.css).
  Returns null when fewer than two networks are chosen or every chosen
  network carries the feature - it only appears when there is a mix
  worth telling apart. `aria-label` says "Applies to X, Y"; each mark's
  title names the network or "Not on X".
- Publish attachments (publish/page.tsx): first-comment label
  (supported=carriers), thread head (threaders), Threads-topic label
  (topicTargets platforms), visibility label (tiktok/youtube subset).
- Campaigns: the edit-package dialog's First comment label and
  Replies/thread legend, driven by a new `follow_up_deliverable` bool in
  `_destination_view` (`first_comment_deliverable(provider, platform)`).
- Verified live both places: Publish with facebook+tiktok chosen shows
  visibility as tiktok-on / facebook-struck (first comment there shows
  the worded Zernio note instead, since carriers is empty - correct);
  the package editor with Threads·Buffer + TikTok·Zernio shows both
  follow-up fields as threads-on / tiktok-struck.
- page.tsx was mixed with parallel-session hunks (saveCredentials
  refactor, audio filter, onPick routing); staged via the classifier
  (scratchpad stage_reach.py, marker `FeatureReach`, kept 5 of 11
  hunks). The other four files were wholly mine.

## Douyin profile truncation audit, 2026-08-18

Operator report: their own browser shows a profile's 60-70 videos, the
system downloads ~20, and a "temp browser" opens mid-download showing
the profile with no videos. Commits `4638f6a`, `1391574`, `4343559`.

Root cause, verified against the live endpoint (probe script in
scratchpad): the saved session in `.data/douyin/cookies.json` is
anonymous - no `sessionid`, captured 08-08 with the login window closed
unsigned. Douyin serves an anonymous session exactly one page of
`/aweme/v1/web/aweme/post/` (20 items, `login_tip` risk flag set) and
answers page 2 with an empty list. The pinned provider then opens a
visible Chromium fallback on the profile, but seeds it with the cookie
jar minus login cookies (`_BROWSER_COOKIE_BLOCKLIST`), so it renders a
signed-out page and collects nothing. An absent `browser_fallback` key
means *enabled* to the provider - the July hardening (80fd0f6) removed
the CLI flag but never turned the fallback off.

- scripts/douyin.py: generated config now says
  `browser_fallback: {enabled: false}`; `cookie_readiness` carries
  `signed_in`; `check` names the anonymous one-page limit.
- integrations/douyin.py: `cookie_status` carries `signed_in`;
  connected message differentiates; a finished profile download under
  an anonymous session appends the limit + remedy to its summary.
- dashboard.tsx (classifier commit, markers signed_in/anonymousSession):
  connected-but-anonymous renders a warning callout "Signed out of
  Douyin" with the backend message and a "Log in to Douyin" action.
- douyin_cookie_capture.py ANONYMOUS_MESSAGE now names the profile
  truncation, not just topic search.
- Unmasked pre-existing test breakage (separate commit `4638f6a`):
  every job_factory test errored since 08-16 - PublicationPlan's FK to
  product_offers needs `opportunity_models` imported before create_all;
  and the duplicate-ingest test pinned naive published_at rendering,
  which the rebuilt venv's SQLAlchemy round-trips tz-aware.
- The remedy for the operator: Refresh session on Download and log in;
  the capture flow upgrades cookies.json in place.
- docs/third-party/douyin-downloader.md: session-strength section;
  corrected the stale "media downloads never use a browser fallback"
  claim (upstream does at this revision; TrendRelay disables it).

### Follow-up: anonymous profiles recovered via the browser, `cf2e7c3`

Operator pushed back - it used to fetch whole profiles anonymously, and
their manual browser still shows the full list scrolled while signed
out. Investigated exhaustively (probes in scratchpad):

- The 20-cap is **server-side and immovable client-side**: page 2 of
  `/aweme/post/` returns a bare `{"status_code": 0}` for the pinned
  client, current upstream (worktree probe), AND a session with cookies
  freshly harvested from a live browser (valid `__ac_signature`). Not
  stale cookies, not signing, not client version. Only `sessionid`
  (login) lifts it. **Upgrading the provider does not help** - tested.
- The grid keeps loading in a *real* browser because its requests carry
  Douyin's own JS-minted msToken/a_bogus and a genuine fingerprint; our
  synthetic API client is detected on the cursored page.
- New `scripts/douyin_profile_enum.py`: headed browser, a
  MutationObserver (installed via add_init_script, before page scripts)
  that hides the sign-up prompt the instant it mounts so the scroll
  never freezes - the vendored fallback only closed captchas, which is
  why it harvested ~8. Harvests DOM `/video/`+`/note/` links, sniffed
  `/aweme/post/` responses, and ids in the HTML.
- `scripts/douyin.py` `expand_profiles`: for an anonymous `/user/` post
  fetch, harvest ids and download each as a per-video link (works
  anonymously). **Floor guard**: used only if the harvest clears ~20
  (the plain API page); a smaller harvest = throttled below a plain
  fetch, so the profile link is kept. Never worse than before.
- **Not consistent**: the harvest measured 8-26 across back-to-back runs
  because Douyin throttles repeated automation; a warmed human session
  sees all ~100. Headless is worse (empty feed) - a `--headless` flag
  exists on the enum for experiments but the flow stays headed. So
  "works every time anonymously" is **not achievable**; login remains
  the dependable path and all messaging now says best-effort, not
  "whole profiles download".
- Messaging softened from the warning-callout version above: the
  dashboard callout is now an informational "Douyin connected (signed
  out)" (not a warning), connection/check/capture/summary all describe
  best-effort browser recovery + login for reliability.
- Provider `browser_fallback` stays disabled - ours replaces it.

## Douyin anti-bot: root cause = msToken, and the browser-free bypass, 2026-08-19

Long live debugging with the operator. The full write-up is in
`docs/third-party/douyin-downloader.md` (Anti-bot root cause section);
summary of what was PROVEN:

- The anonymous 20-cap is the **msToken**. Captured (raw CDP + Network
  domain) an automation-navigated tab's `/aweme/post/` request has NO
  `msToken`; a hand-**duplicated** tab's has one and pages the whole
  profile. In an automated browser (Playwright, raw CDP, real Chrome via
  CDP, undetected-chromedriver - all four) the msToken cookie never
  mints, or a stale one is refused (65 token-bearing requests fired,
  videos stayed 26).
- The flag is **per-tab devtools attachment + programmatic navigation**,
  not the browser instance nor the IP (same IP: operator's hand-driven
  Chrome/Edge page fully). `Target.setAutoAttach` to all tabs re-flags
  even the duplicates.
- Params-only replay fails: `a_bogus`/`x-secsdk-web-signature` must be
  freshly computed per request; adding verifyFp/fp/webid/timestamp/
  from_user_page to our client still returns empty page 2.

**The bypass (browser-free, no login), researched:** the mature scrapers
do not read msToken from a browser - they **mint a real one over HTTP**
from the mssdk `common/report` endpoint (POST encrypted `strData`, token
returns in cookies, 164/184 chars). Our provider's `MsTokenManager`
makes a *fake* random one, which is exactly what Douyin refuses.
Reference: **F2 (`Johnserf-Seed/f2`)** - `TokenManager.gen_real_msToken()`
+ `ABogusManager` do this for `aweme/post`, pure Python, no browser.
Plan: port `gen_real_msToken` + current `a_bogus` into
`api_client.get_user_post`, regenerate per batch (~1 min rotation), drop
the browser enumerator. Watch for: IP/session sensitivity (residential
IP should pass), and `x-secsdk-web-signature` as the possible last gate.
Verify F2's licence before vendoring.

## Discover posts-first workspace audit, 2026-08-22

- Discover now opens on `Hot posts`, followed by `Topics`, `Ads & signals`, and
  `Opportunities`. The duplicated Overview surface was removed so real public
  posts are the first useful result instead of being hidden behind another tab.
- The compact header owns readiness, signal import, one Research/Ads search
  scope, and country. TikTok-specific Hashtag/Video controls moved beside the
  TikTok board because they do not affect the rest of Discover.
- The hot-post board loads 44 live public posts in the current fixture and
  uses source-fair ordering: each network's strongest post appears before a
  second post from one network. It shows 15 initially, collapses provider notes,
  and only mounts the workspace-research ranking when that data exists.
- Mixed-board positions are one unique sequence rather than repeated provider
  ranks. Provider rank remains in the accessible description, and posts without
  a thumbnail receive an initial derived from title, creator, then source.
- Topics lead with the five hottest Douyin terms and expand on demand. The
  consolidated topic analysis and TikTok Creative Center remain available
  below it, with setup and coverage details collapsed until needed.
- Ads & signals cards shrink and wrap safely, the Opportunities scoring CTA is
  visually primary, and pending rows use a denser layout. All new visible and
  accessible copy is localized in en/vi/zh/ja/fr/ru/ar.
- Live verification covered all four views at 1280 px, 390 px, and 320 px. The
  Discover document and its view navigation have zero horizontal overflow at
  320 px. The first desktop post moved from roughly 502 px to 396 px from the
  page top; topic analysis moved from roughly 1,980 px to 759 px.
- TypeScript and targeted ESLint pass; the source-fair tests pass 12/12 and all
  web tests pass 232/232. README was not changed because product scope, setup,
  and architecture did not change. Unrelated dirty work remains unstaged.

## Responsive workspace audit, 2026-08-22

- Audited Discover, Download, Library, Attribution, Publish, Campaigns, and
  Tools in the live app at 320px, 390px, and 768px after asynchronous content
  loaded. All seven routes now have zero document-level horizontal overflow.
- Intentional narrow-screen scrolling remains bounded to the Discover view
  strip, Download queue filters, and Attribution product table.
- Shared cards now permit shrinking and page-specific compact padding through
  `--ui-card-padding`. Shared SearchSelect popovers accept logical inset and
  width variables; the global timezone and language menus remain legible and
  contained down to 320px, including RTL-compatible anchoring.
- Fixed phone layouts for Attribution filters/bulk actions, Discover search and
  news filters, Publish engine cards, and fully loaded Campaign timeline rows,
  toolbar controls, delivery fields, and tabs. The mobile notification list and
  Add products/New campaign dialogs were also verified with no horizontal
  overflow; notifications retain vertical scrolling.
- Verification: live 320/390/768 sweep; 320px timezone/language, notification,
  product-dialog and campaign-dialog checks; TypeScript passes; all 230 web
  tests pass, including palette and stylesheet ownership guards. The repository
  lint command currently scans generated `.next-dev` output and also reports
  pre-existing source issues, so it is not a clean gate for this CSS-only change.
- README was not changed because the project goal, stack, structure, setup, and
  scope did not change. Unrelated dirty work remains unstaged.

## Global workspace context, 2026-08-22

- Workspace selection is now application state rather than separate page state.
  `WorkspaceProvider` loads the available workspaces once, restores the last
  valid selection from local storage, and drives every workspace-scoped page
  plus the shared job feed.
- The one workspace switcher is stacked with the account identity in the
  top-right toolbar. Its compact menu also owns timezone, language, and remote
  sign-out, leaving the notification bell as the only adjacent global action.
- Removed the repeated workspace dropdowns from Download, Discover, Library,
  Attribution, Publish, and Campaigns. The selection therefore stays stable
  across navigation instead of silently returning to the first workspace.
- `DESIGN.md` and README now record that app-wide context belongs to the global
  toolbar and must not be reimplemented per page.
- Verification: targeted ESLint and TypeScript pass; all 230 web tests pass.
  Live local-admin checks confirmed the stacked Workspace / Local admin control,
  timezone and language inside its menu, no page-level Library selector, and no
  horizontal overflow with the menu open at 320 px. Commit: `0a73e57`.

## Campaign mobile timestamp spacing, 2026-08-22

- The Campaign list timeline now reserves a 76px time rail below 620px rather
  than 58px. This leaves at least 10px between the longest displayed time
  (`12:00 PM`) and the card edge; shorter times retain 16px.
- Live local-admin checks passed at the reported 539px width and at 320px. The
  timeline retains no document-level horizontal overflow at 320px.
- `DESIGN.md` now requires fixed mobile metadata rails to account for their
  longest formatted value plus normal container inset.
- TypeScript and all 232 current web tests pass. Commit: `495a65d`.

## Tab and Library loading audit, 2026-08-22

- The Library list endpoint no longer executes three related-record queries for
  every loaded asset. Versions, transcripts, and latest analyses are fetched in
  three page-wide queries, reducing a 100-asset page from roughly 300 related
  queries to three while retaining the existing single-asset serializer path.
- Opening Library no longer starts a 200-job download reconciliation scan and
  then repeats the full Library refresh. Normal Douyin downloads already queue
  their media for Library ingestion; the explicit `Refresh downloads` action
  remains for legacy or externally changed files.
- Library keeps up to 12 recent workspace/filter/sort snapshots in memory.
  Returning from another tab can paint the last useful list immediately while
  the API revalidates assets, counts, jobs, and media readiness in the
  background. The cache is intentionally discarded when the app closes.
- The main tab audit found Campaigns already loads its two primary datasets in
  parallel and Attribution loads its five primary datasets in parallel. The
  severe query fan-out and mount-time maintenance scan were Library-specific.
- `DESIGN.md` now makes immediate stale-while-refresh tab restoration, batched
  list queries, and no mount-time mutating maintenance work a quality gate.
- Verification: Library API tests pass 4/4, including a fixed three-query
  regression test across 20 assets; Ruff, targeted Library ESLint, TypeScript,
  and all 234 web tests pass. A live local-admin Library with 2,531 assets
  loaded 100 cards with correct facets and no stuck busy state.

## Library screen-density audit, 2026-08-22

- The apparent blank row below the media categories came from the global `nav`
  margin leaking into `library-category-bar`. Library now explicitly owns that
  margin and uses one 7px rhythm between search, categories, facets, and actions.
- The eyebrow was removed from the visual header and its title, readiness, and
  concise purpose now share one desktop row. The browser sticky offset matches
  this shorter header and remains 6px below it while scrolling.
- The inactive selection control is shorter, tablet controls no longer stack
  prematurely, and phone facets use a contained two-column grid. Desktop's
  first media card moved from about 475px to 395px; the 390px toolbar dropped
  from 400px to 310px.
- Live checks passed at 1280px, 390px, and 320px with aligned desktop columns,
  functioning sticky behavior, and no document-level horizontal overflow.

## All-tab loading audit, 2026-08-22

- Download, Discover, Attribution, Publish, Campaigns, and Tools now keep bounded
  memory-only snapshots of their last useful data. Returning to a tab restores
  that view immediately, coalesces duplicate refreshes, and revalidates in the
  background. Library retains its purpose-built snapshot added in `6b5afa0`.
- Attribution's five workspace reads and Campaigns' two primary reads remain
  parallel but no longer throw their useful state away on navigation. Campaign
  offers, Download media status, Discover provider/category status, Publish
  slots/products/connection/accounts, and the Tools registry receive the same
  stale-while-refresh behavior.
- Tools now consumes the global workspace provider instead of issuing another
  `/api/workspaces` request. The shared notifications provider coalesces refresh
  calls, polls active work every 2.5 seconds, backs off to 12 seconds when idle,
  and stops network fan-out while the document is hidden.
- Publish's initial connection endpoint no longer probes the hosted provider;
  account discovery performs the one useful authentication pass. The local
  endpoint fell from about 1,681 ms to 18 ms on its first measured call and 9 ms
  thereafter.
- The unified development runner prepares Discover, Library, Attribution,
  Publish, Campaigns, and Tools in a background thread after opening Home. This
  removes Next's first-click route compilation without delaying browser launch;
  production already contains compiled routes.
- Live data-ready checks with one clean browser tab: Download 254 ms, Library
  794 ms for 2,531 assets, Attribution 634 ms for 190 products, Publish 151 ms,
  Campaigns 258 ms, and Tools 146 ms. Discover rendered 3/3 sources ready.
- Verification: all 237 web tests pass, 56 focused API/runner tests pass,
  targeted ESLint, TypeScript, Ruff, and diff whitespace checks pass. README
  documents the development warmup behavior. Commit: `d2cc981`.

## Extensible Library selection actions, 2026-08-22

- The Library selection bar now exposes one accessible `Edit selected` command
  menu instead of reserving permanent space for every editing workflow. A
  central registry declares each action's media compatibility and safe batch
  boundary, so another future action can join without redesigning the toolbar.
- Effect stacks, captions, and voiceover are available from the same menu.
  Captions queue compatible video/audio items four at a time and report partial
  failures. Bulk voiceover is bounded to 25 selected items, uses each item's own
  reviewed transcript, never voices machine drafts, shows billed character
  demand, and limits mixed video/audio selections to audio output.
- Images remain valid effect targets but are explicitly skipped for captions
  and voiceover. Successful queueing removes only completed IDs from the
  selection, leaving failures selected for correction.
- The shared action menu supports native button/menu semantics, arrow, Home,
  End, Escape, Tab, click-outside handling, and a fixed overlay position that
  never shifts the media list. Live checks kept the menu inside 390px and 320px
  viewports with no horizontal document overflow.
- Verification: targeted ESLint and TypeScript pass; the seven selection and
  locale tests pass. The full 241-test web run reached 240 passes and one known
  unrelated stylesheet ownership failure (`search-select` is currently defined
  by pre-existing work in two stylesheets). Commit: `03fbd36`.

## Library effect meaning and processing tags, 2026-08-22

- The broad face-obscuring outcome is now labeled `Faces covered — any method`;
  the exact `Cover a face with an object` entry remains the specific overlay
  recipe. This makes it clear why the counts can differ.
- Library cards and detail metadata now show independent workflow tags for
  reviewed/draft speech transcripts, reviewed/draft on-screen text, captions,
  and voiceover alongside exact visual-effect tags.
- A new Processing facet filters those artifact categories without pretending
  transcripts or captions are visual effects. Facet counts use two grouped SQL
  queries and compose with channel, source, media-kind, search, and effect
  filters; correlated subqueries are explicitly scoped to the media asset.
- The labels are translated across all seven locales. Verification: 31 focused
  API tests, 9 web filter/i18n tests, TypeScript, targeted ESLint, and Ruff pass.
  Live inspection before the final correlation fix showed the new facet and
  chips populated from real local data; the corrected composition is covered by
  a dedicated regression test. Commit: `d1c00bc`.

## On-screen text generation audit and recovery, 2026-08-22

- The reported `RapidOCR 3.9.2 / ONNX Runtime 1.28.0` setup row was an older
  one-attempt durable job whose worker disappeared while fetching the pinned
  source. The local environment had ONNX Runtime but did not yet contain the
  RapidOCR package or models.
- Running the supported pinned setup completed successfully and activated the
  OCR tool. A real 4.41-second Douyin video then completed through the normal
  worker, FFmpeg frame extraction, RapidOCR inference, and database persistence.
  Its machine draft is `transcript_2e373db867a3441ab0e2679a6babd90c`
  from job `mediaai_63c0ef7c4c355469aea5cfba`.
- Setup and enrichment jobs now use three attempts, shorter renewable leases,
  and worker-start policy upgrades for legacy rows. A stalled setup is joined
  rather than launching a competing package install; failed or cancelled work
  resumes the same durable request with the current retry policy.
- Deterministic handled failures such as an OCR pass finding no text stop
  immediately and surface a `Retry reading` action. The spare attempts are
  retained for unexpected worker termination instead of being silently spent
  on the same deterministic error.
- Live Library verification found the tested video by title, confirmed its
  `On-screen text draft` tag, and displayed the machine draft under Reviewed
  on-screen text with provider `rapidocr@3.9.2:onnxruntime@1.28.0`, `Show`, and
  `Use as reviewed` controls.
- Verification: Ruff passes; focused API/durable-worker tests pass 84/84;
  targeted web ESLint and TypeScript pass. Commit: `aa0bdbc`.

## Exact notification destinations, 2026-08-22

- Media notification groups no longer inherit only the newest job's link.
  Their destination is derived from every distinct affected asset: one result
  opens that exact item, while two or more open an explicit, shareable Library
  scope.
- The Library API accepts up to 200 explicit asset IDs and intersects them with
  the active workspace and every ordinary filter. This also makes direct links
  reliable for assets older than the first 100 normally loaded.
- Library displays a compact `From notifications` status with a `Show all
  media` exit. Query changes are reactive, so moving from one notification to
  another while already in Library replaces the scope without a reload.
- Live verification used the real 40- and 100-item face-covering batches, a
  three-item transcript group, and a direct caption result. The exact counts,
  selected direct asset, same-page scope switch, and clear action all worked.
- Verification: 28 focused API tests, all 244 web tests, targeted ESLint, Ruff,
  and a complete Next production build pass. Commit: `2b9065d`.

## Compact notification context in Library, 2026-08-22

- Notification destinations now carry the stable action title, with the
  drawer's duplicate `· N items` suffix removed before it reaches the URL.
- Library matches that action against its live job feed and shows the action,
  settled/total count, retry count, and a thin progress line in the existing
  temporary-filter row. Progress is live rather than a stale URL snapshot.
- The row remains 42px high at desktop and 390px widths. Its progress bar hides
  at the narrow breakpoint, the action title ellipsizes, and the numeric
  progress plus `Show all media` remain available without horizontal overflow.
- Live verification on the real batch showed `Cover a face with an object ·
  39/100 done · 1 to retry`; 244 web tests, TypeScript, targeted ESLint, and the
  complete Next production build pass. Commit: `ac06d04`.

## Adaptive media processing performance, 2026-08-22

- H.264 work now probes real encoder execution once per worker and selects
  NVENC, Quick Sync, AMF, or VideoToolbox before falling back to libx264. Each
  actual render also retries in software when a hardware session fails. Effects,
  face/model remuxes, and burned captions share the policy. This RTX 2060
  selected `h264_nvenc`; focused render coverage passed 100/100. Commit:
  `b61617d`.
- Local faster-whisper now resolves `auto` through CTranslate2's actual CUDA
  count and supported compute types, batches speech chunks, and reuses the model.
  RapidOCR reuses its ONNX sessions. The installed runtime initialized the base
  model on CUDA/FP16; focused AI coverage passed 65/65. Commit: `92fb45d`.
- Effect, caption, and enrichment batches use a bounded adaptive thread lane.
  This 20-thread machine selected three workers; six simulated 200ms native jobs
  completed in 0.407s instead of about 1.2s serially. Focused batch coverage
  passed 29/29. Commit: `91ff88a`.
- Voiceover generation is registered by the worker and runs in a fixed two-request
  lane so paid ElevenLabs requests do not stampede. This landed with the durable
  worker recovery work in `399a3f9`.

## Resilient concurrent GPU face renders, 2026-08-25

- Face-blur and face-overlay renderers now replay their already-tracked second
  pass with libx264 if a hardware FFmpeg stream exits or breaks its pipe after a
  successful capability probe. The fallback does **not** repeat face detection,
  so it preserves the same coverage while avoiding a failed batch item when a
  concurrent NVENC/QSV/AMF session is temporarily unavailable.
- `open_h264_stream_writer(..., force_software=True)` makes that fallback explicit
  and testable. Live capability check on this machine still selects `h264_nvenc`.
- Verification: Ruff passed; `test_video_encoding` + `test_worker_pool` passed
  7/7; face blur, media-AI performance, captions, and voiceover passed 85/85.
  The wider face-overlay suite has three unrelated existing failures for the
  unfinished `cap_3d` catalogue item. Commit: `3559c59`.

## Autonomous campaigns and live approval counts, 2026-08-25

- Autonomous now matches its UI promise: finished posts never enter the
  approval inbox solely because their product match is low-confidence. The
  match evidence remains on the planned post; non-autonomous levels still hold
  weak matches for review.
- A campaign run releases approval rows created under the former rule. Complete
  frozen posts are queued at their original/future slot (or now when overdue),
  incomplete rows become actionable failures, and provider-capacity holds are
  cancelled so the approved queue item can retry later without an approval.
- Switching an already-running campaign to Autonomous triggers a run
  immediately, so old badges do not wait for the scheduler interval.
- The Campaigns sidebar count is synchronized from the live exception response
  on load, polling, approval, and dismissal. A single approval also refreshes
  the parent campaign snapshot.
- Verification: 131 focused campaign authority/API tests passed; targeted web
  ESLint and TypeScript passed. The commit stages only these hunks because the
  same shared files contain unrelated in-progress campaign-format work. Commit:
  `dbd2e10`. Batch decisions also refresh the same exception source before
  returning, committed separately as `52b89b7`.

## Campaign approval action tooltips, 2026-08-25

- The six per-post approval controls now explain their distinct consequences
  on pointer hover and keyboard focus: scheduled approval, immediate publish,
  copy-only editing, product/link review, temporary skip, and persistent pause.
- A shared `Tooltip` primitive keeps the real button as the trigger, attaches
  its explanation through `aria-describedby`, and includes reduced-motion and
  browser-native fallbacks.
- Targeted ESLint and the complete web TypeScript check passed. The atomic
  commit contains only the tooltip primitive, its shared styling, and the
  Campaigns approval-row wrappers; other campaign work remains unstaged.
  Commit: `a2133ff`.

## Per-campaign performance overview, 2026-08-28

- Every campaign now has a remembered Overview alongside Queue & setup and
  Schedule. It opens by default for new browser profiles and uses the existing
  workspace timezone.
- Today, 7-day, 28-day, and 90-day views aggregate the newest stored native
  provider snapshot per published post. The endpoint does not call providers
  again when the dashboard opens and does not double-count cumulative 2h, 24h,
  and 7d observations.
- Scorecards cover views, engagement, content published, and engagement rate;
  the chart switches between views, engagement, and publishing volume.
- Top posts are limited to the selected period and can be ranked by views,
  total engagement, likes, comments, shares, or saves. Provider coverage and
  integrations that cannot return analytics are stated explicitly.
- Live verification used real campaign data and confirmed the range and sort
  controls, zero horizontal overflow at 390px, and no browser console issues.
  Ruff passed; the focused API tests passed 3/3; the complete campaign API file
  passed 88/88; targeted ESLint and TypeScript passed; the full Next production
  build passed. Commit: `2fe2303`.

## Facebook-reference analytics presentation, 2026-08-28

- Reviewed all three screenshots under `References/Facebook Professional
  Dashboard`, not only the public Meta documentation. The follow-up takes two
  concrete patterns from the supplied mobile UI while retaining TrendRelay's
  own design tokens and information architecture.
- The active KPI card now uses a full, layout-stable accent outline like the
  detailed Facebook analytics view. Top posts are thumbnail-led cards in a
  horizontal ranked strip, titled for the selected metric and still sortable
  by views, engagement, likes, comments, shares, or saves.
- The analytics response now includes each publication's Library asset id so
  thumbnails come from authenticated local media rather than third-party URLs.
- Live verification on real campaign data showed 10 cards, correct thumbnail
  loading, no page-level horizontal overflow at 390px, and no browser console
  issues. Focused API tests passed 3/3; Ruff, targeted ESLint, TypeScript, and
  the complete Next production build passed. Commit: `6f8c8e3`.

## Campaign chart inspection and live-post links, 2026-08-28

- The performance trend no longer renders a distracting dot for every sample.
  Moving anywhere across a date's horizontal region selects its nearest sample,
  showing one guide, one active marker, and a compact tooltip with date, views,
  engagement, and published count. Keyboard users can inspect with arrow keys,
  Home, and End.
- Top-post thumbnails are clickable and include an explicit "Open on ..."
  action when the provider stored a real publication permalink. Historical
  Buffer rows without a permalink intentionally remain unlinked; TrendRelay
  does not fabricate a social URL from Buffer's internal id.
- Live desktop and 390px checks confirmed proximity hover, keyboard inspection,
  responsive containment, and no console errors. Targeted ESLint, TypeScript,
  focused API tests (3/3), Ruff, and the complete Next production build passed.
  Commit: `96d8c3c`.

## Draggable Top posts carousel, 2026-08-28

- The Top posts strip supports grab-and-drag navigation with mouse, pen, and
  touch-sized pointers. A movement threshold distinguishes a drag from a click,
  so dragging a thumbnail never accidentally opens its social permalink.
- The strip retains vertical page panning on touch devices, temporarily disables
  snap while held, and restores proximity snapping on release. Left/right arrow
  keys provide equivalent navigation when the list itself has focus.
- Live desktop and 390px interaction checks confirmed horizontal movement,
  unchanged URLs after dragging, no page-level overflow, and no console errors.
  Targeted ESLint, TypeScript, and the complete Next production build passed.
  Commit: `3d8b1bb`.

## Remembered campaign Overview controls, 2026-08-28

- Each workspace campaign now remembers its analytics date range, active KPI/
  chart metric, and Top posts ranking in local browser preferences. Stored
  values are validated against the live option sets before they are restored.
- The Overview remounts when the selected campaign changes, preventing an
  unsaved/default campaign from inheriting another campaign's controls while
  still restoring its own saved choices when the operator returns.
- Live verification selected 90 days + Engagement, reloaded, switched to a
  campaign with 28 days + Views, then returned and recovered 90 days +
  Engagement. No browser warnings appeared. Targeted ESLint, TypeScript, and
  the complete Next production build passed. Commit: `59b66c3`.

## Fixed-size campaign chart marker, 2026-08-28

- Replaced the active SVG circle, which inherited the chart's non-uniform
  responsive scaling, with a fixed-size HTML overlay positioned at the same
  data coordinates. The point is now an 8px outlined marker at every viewport.
- The proximity hit region, guide line, tooltip, and keyboard inspection remain
  unchanged. Live desktop and 390px checks measured exactly 8x8px with no
  console errors. Targeted ESLint, TypeScript, and the complete Next production
  build passed. Commit: `680b6ea`.

## Mobile Campaign Overview audit, 2026-08-28

- Audited the full Overview inside the real Campaigns shell at 320px and 390px,
  including range controls, KPI grid, edge-date chart inspection, Top posts,
  sorting, and data coverage.
- Mobile tooltips are centered and width-bounded so first/last dates never clip.
  The active marker is clamped inside the plot, which removes the small page
  overflow it could create at the final data point.
- KPI names wrap instead of losing meaning to ellipses. The 375px Top posts
  heading keeps "Sort by" intact; narrower screens retain the stacked layout.
  Analytics controls are 40px touch targets, stay in one row at 390px, and
  stack Refresh cleanly at 320px.
- Both audited widths had exact page containment and no console errors. The
  complete Next production build passed. Commit: `fc1f4ec`.

## Automatic face objects for effect batches, 2026-08-28

- Audited the face-object path end to end. The renderer and durable worker
  already support a different object in each job; the batch endpoint was the
  limiting layer because it copied one normalized recipe to every asset.
- Batch Effects now defaults face-object steps to "Choose the best object
  separately for each item." Each asset is matched from its hashtags, caption,
  visual reading, OCR, transcript, title, and creative analysis. The existing
  measured trust threshold is retained; weak coincidences are not promoted.
- When nothing is trustworthy, the job explicitly records `safe_fallback` and
  uses the full-face Smiley. Content matches record their object, score, matched
  terms, and evidence sources. Resolved values are stored in each asset recipe,
  durable job payload, response result, audit entry, and batch summary.
- Turning automatic selection off preserves the old shared-gallery workflow.
  Preview is disabled only while automatic mode is active because the actual
  object differs per item and is resolved at queue time.
- The batch editor was checked live at desktop and 390x844. Mobile has no
  horizontal overflow, the selector is a full-width touch target, settings use
  one column, and footer actions remain visible.
- Verification: Ruff passed; web ESLint passed; 62 affected API tests passed;
  the complete Next production build passed. Commit: `40e9036`.

## Scheduled-post modal action cleanup, 2026-08-29

- Removed the duplicate Save post button from the bottom of the Campaigns
  scheduled-post editor. The single header action still submits
  `campaign-edit-content` through its `form` attribute.
- Live desktop and 390px checks found exactly one submit action, no horizontal
  overflow, and no browser warnings. Targeted ESLint and diff checks passed.
- Commit: `ed4423b`.

## Campaign image validation and text-only posts, 2026-08-29

- Fixed scheduled image posts being rejected as missing an approved MP4: the
  Campaigns acceptance preview now carries frozen `image_paths` into the same
  `PublishRequest` validation used by delivery.
- The scheduled-post editor can now stage and save **Remove media**, producing
  an explicit text-only queue item while preserving the current media until
  Save post is clicked. Replacing media switches the item back automatically.
- Added a durable `text_only` queue-item flag and migration `20260829_0055` so
  intentional text posts remain distinct from posts still waiting for media.
  Text-only delivery is limited to provider/platform surfaces that support it;
  media-required Instagram, TikTok, YouTube, and Pinterest formats still fail
  early with a useful validation message.
- Verified the actual editor at desktop and 390x844 with no horizontal
  overflow. Web lint and typecheck passed, Ruff passed, Alembic reports one
  head, and 339 affected API/migration tests passed.
- Commit: `1d9f165`.

## MCP contract freshness and generated-file diagnostics, 2026-08-29

- Root cause of an assistant reporting that `create_campaign_post` still
  required media: the MCP process had been alive since August 26. SOP Markdown
  is read from disk on every request, but MCP tool registrations are imported
  once, so the new SOP described `upload_media`, `set_post_media`, and text-first
  drafts while the old in-memory server exposed the previous contract.
- The unified dev runner now watches the tunnel/MCP Python contract as well as
  the API. MCP is restarted with source changes, and the MCP child watches its
  supervisor PID so a forced Windows shutdown cannot leave a stale listener.
- Upload tools now accept the common temporary `download_url`, `file_url`,
  `image_url`, and `url` attachment-object fields. A bare generated-file id or
  another runtime's mounted path receives an explicit transfer-boundary error
  instead of looking like an unsupported image.
- Fixed MCP post-context media-type resolution for lightweight preview objects
  that do not carry a scheduled execution's `media_path` field.
- Refreshed the active MCP listener in place on port 58291. Its live status now
  lists `upload_media` and `set_post_media`. Ruff and 132 targeted MCP/lifecycle
  tests pass.
- Commit: `e9fd7bf`.

## Copy-only previews and destination-aware campaign threads, 2026-08-29

- Repaired the interrupted `PostPreview` conditional that broke the Next.js
  build, and made deliberate copy-only posts render without an invented media
  placeholder in campaign rehearsals and held approvals.
- Split multi-post thread support from the broader first-comment capability.
  Campaign destination and execution payloads now report the exact
  provider/network answer: Facebook through Zernio keeps first comments but
  does not expose reply inputs, while Bluesky through Zernio and supported
  Buffer networks retain threads.
- The scheduled-post modal hides reply controls when no connected account can
  publish them, names why, and clears legacy hidden replies on save. Approval
  cards do the same; legacy held replies are safely omitted at approval so old
  rows stop failing validation.
- The scheduler, held-post recomposition, and live composition preview strip
  replies per destination instead of freezing content that its engine will
  refuse. Zernio/Bluesky thread validation remains supported.
- Verification: web ESLint passed; the complete Next production build passed;
  131 targeted API tests passed. Live checks covered the real Facebook/Zernio
  campaign editor at desktop and 390x844, with no browser errors.

## Copy-only publishing notifications, 2026-08-29

- Publish notifications now reserve the media thumbnail frame only when the
  durable request carries a video path or at least one image path. Deliberate
  copy-only posts render as compact text rows instead of showing an empty
  46x46 media placeholder.
- Destination platform icons remain in the metadata line because they identify
  where the text post is scheduled; they are not treated as media thumbnails.
- Verification: web ESLint, TypeScript typecheck, and the complete Next
  production build passed.
- Commit: `6c05b93`.

## Copy-only campaign queue rows, 2026-08-29

- Campaign queue rows now use the queue item's explicit `text_only` state to
  omit the entire media preview cell. This fixes copy-only posts that still
  showed an empty play frame even though composer, approval, and notifications
  already treated them as text-only.
- Copy-only rows use reduced desktop and mobile grid templates so the copy
  expands into the reclaimed space. Media-later posts retain their empty frame
  because it correctly signals an attachment is still required.
- Verification: web ESLint and TypeScript passed; all 390 web tests passed.
  The live NightClubzz copy-only row had zero thumbnail descendants, no mobile
  horizontal overflow at 390x844, and no browser errors.
- Commit: `d05bff5`.

## Media-ingestion notification thumbnails, 2026-08-30

- Successful Library ingestion notifications now retain the completed job's
  `result.asset_id`, allowing the notification context to load the same
  authenticated 46px thumbnail and asset summary used by other media work.
- The fix is limited to the ingestion-job adapter; notification layout and
  copy-only publishing behavior are unchanged.
- Verification: web ESLint and TypeScript passed; all 390 web tests passed.

## Download-batch Library deep links, 2026-08-30

- The batch-level **Open library** action on Download now opens a temporary
  Library view scoped to the exact Douyin download job, matching the focused
  navigation used by notifications. Individual artifact links remain direct
  asset links.
- Batch scope is evaluated by the Library API using the durable download job
  id rather than embedding asset ids in the URL. This supports downloads with
  thousands of files while preserving server-side counts, facets, paging, and
  select-all behavior.
- Download ingestion now retains every batch id when an already-known asset is
  downloaded again, so either historical batch still finds the shared asset.
- Verification: web ESLint and TypeScript passed; the link assertion passed;
  Ruff passed; 2 focused API regressions passed.

## Compact Library arrangement controls, 2026-08-30

- Group now sits beside Sort in a dedicated arrangement cluster instead of
  consuming its own row beneath the five content filters.
- Desktop keeps media types, Sort, and Group on one line. At 600px and below,
  the media types retain a full-width touch row while Sort and Group share the
  next row equally, avoiding horizontal overflow.
- Verification: web ESLint and TypeScript typecheck passed. Live browser
  verification was unavailable because the local browser harness could not
  locate its runtime-assets directory.
- Commit: `5647f48`.

## Paginated MCP copy queue, 2026-08-30

- `list_posts_needing_copy` now accepts MCP-visible `limit` and `offset`
  arguments. The default is 50 and the validated hard maximum is 250.
- It returns `posts`, `total`, `limit`, `offset`, `returned`, `more`, and
  `next_offset`; ordering is deterministic across page boundaries.
- The MCP guide and `campaigns.fill-needs-copy` SOP explain read-only traversal
  and why agents must refresh from offset zero after writes remove completed
  rows from the result set. The SOP is version 2.
- Verification: 7 focused pagination/schema tests passed and Ruff passed. The
  full MCP file reached 116 passes; 7 upload fixtures errored because this
  sandbox denied pytest's Windows temporary directory, not due to assertions.
- Commit: `eeeee05`.

## Dismissible transient notifications, 2026-09-05

- Added a shared `DismissibleStatus` banner with token-based success/error
  styling, long-copy wrapping, keyboard focus treatment, and an accessible
  localized close control.
- Publish action confirmations and failures can now be dismissed, including
  the engine-setup confirmation shown above the publishing cards.
- Reused the same interaction for transient action feedback in sign-in,
  password update, MFA, device approval, workspace management, invitation
  acceptance, opportunity scoring, and the campaign-idea dialog. Persistent
  configuration guidance remains visible because it describes current page
  state rather than a completed action.
- Verification: web TypeScript, ESLint, localization scan, and `git diff
  --check` passed. The full web suite reached 408/410; its two failures are
  pre-existing dirty-worktree palette/duplicate-class violations in unrelated
  campaign CSS (`#111923`/`#18232e` and `campaign-entry-actions`).

## Stable campaign switching, 2026-09-05

- Reproduced the campaign rail bounce: the keyed Autopilot workspace collapsed
  from roughly 1,429px to 491px during its first API read, briefly forcing the
  sticky sidebar against the bottom of its containing grid. A scrolled page
  could also have its scroll position clamped while the document was short.
- Campaign selection now preserves the outgoing workspace's measured height
  until the incoming Autopilot panel has committed either real content or its
  terminal failure state. The campaign list stays interactive and the new
  panel still shows its honest loading state.
- Settlement is keyed to the incoming campaign id so rapid consecutive clicks
  cannot let an older request release a newer transition's reserved height.
- Verification: web TypeScript, ESLint, and `git diff --check` pass. Live
  desktop measurement kept the sticky sidebar at 174px through campaign
  switches; before the fix it moved to 164.5px and back.
