# TrendRelay

**Download authorized Douyin media, track large batches, and review every clip in a searchable local library.**

TrendRelay is a local-first Windows workspace for collecting reference media from Douyin. Paste a video, profile, collection, music, or copied share link; TrendRelay downloads it in the background, preserves the original source, and adds the resulting videos, images, and audio to your Library.

![TrendRelay Home showing a Douyin profile download](docs/assets/trendrelay-douyin-home.png)

## What works today

- **Paste and download** Douyin videos, profiles, collections, music pages, and copied share messages.
- **Download full profiles** with **all videos** selected by default, or set a smaller limit when needed.
- **Track large batches** with live video, image, audio, file-count, and disk-size progress.
- **Resume interrupted work** after session expiry, provider errors, or an application restart.
- **Keep source provenance** so every Library item can lead back to the Douyin URL that produced it.
- **Avoid duplicate work** through local metadata, file checks, and incremental downloading.
- **Review media locally** using thumbnails, filters, gallery/list views, and an in-page video player.
- **Transcribe and translate locally** with faster-whisper speech drafts and Argos caption language packs; machine text stays separate until it is reviewed.
- **Apply non-destructive effects** to one clip or a campaign-sized selection while keeping every original immutable.
- **Deploy campaigns from one workspace** by filtering and selecting Library clips, applying effects, assigning social accounts, writing captions, attaching imported affiliate offers, setting posting times, previewing, and launching without leaving Campaigns.
- **Hand approved plans to Publish** with media, copy, disclosure, affiliate placement, and schedule restored together.
- **Follow campaign performance** into Attribution with plan, tracking-link, click, and commission context.
- **Keep downloads private** in the local `.data/` directory, which is excluded from Git.

## From link to Library

1. Open **Home** and paste one or more Douyin links or share messages.
2. Choose the download options. Profiles default to **Published posts / all videos**.
3. Start the batch and follow the live counts under **Downloads**.
4. Refresh the Douyin session and resume the same link if Douyin interrupts a long run.
5. Open **Library** to search, filter, preview, and revisit the original source.

![TrendRelay Library showing downloaded video thumbnails and preview](docs/assets/trendrelay-library.png)

Library videos use poster thumbnails and load the video stream only after you choose to play it. This keeps browsing fast and avoids triggering external download managers while changing filters or moving between items. Use the previous/next controls or arrow keys to navigate; press Space to play or pause.

Open the transcription status control in Library to set up or switch on local speech transcription and caption translation. Provider downloads run as recoverable background jobs, and completed runtimes are reused after a restart. A stale Hugging Face login is not required for the public faster-whisper model. Automatic transcripts are saved as machine drafts so they can be checked before they become reviewed text; translated caption tracks preserve the original transcript.

## Quick start on Windows

### Requirements

- [Git](https://git-scm.com/downloads)
- [Node.js 22 or newer](https://nodejs.org/)
- [Python 3.12 or newer](https://www.python.org/downloads/)

### Install and run

```powershell
git clone https://github.com/meap158/TrendRelay.git
cd TrendRelay
.\start.cmd
```

The first run installs and verifies the application dependencies, creates the Python environment, applies database migrations, starts the local services with hot reload, and opens TrendRelay in your browser. Setup prints four numbered stages and keeps reporting progress during longer downloads; it stops with a useful network error instead of waiting indefinitely. TrendRelay's lockfile and CI are validated with npm 11.16, and a version-pinned install-script allowlist covers its reviewed Electron, media, compiler, and resolver runtimes. If an install is interrupted or incomplete, running `start.cmd` again detects and repairs it automatically.

Social publishing runs entirely against a hosted API. Pick Bundle.social, Zernio, Buffer or WoopSocial on `/publish`, paste that engine's API key into the form, and TrendRelay writes it back to the local `.env`. Several can be switched on at once, and one post addresses destinations across all of them. No local publishing service is installed or supervised.

Buffer has no upload endpoint, so it needs media already hosted at a public URL; the other three accept the approved local file directly.

If the browser does not open automatically, visit [http://127.0.0.1:3001](http://127.0.0.1:3001).

### Running it as an app rather than a dev server

By default the web front end is Next.js in development mode: it compiles each
page the first time you open it and reloads the browser when a file changes.
That is what you want while editing the code and is the slower, heavier way to
use TrendRelay day to day.

To serve a compiled build instead:

```powershell
.\start.cmd --production
```

It compiles once, then serves what it compiled — pages open without a wait,
memory stays flat, and there is no bundler watching the tree. The trade is
exactly that last part: **code changes do not appear until the next launch**,
which rebuilds automatically when it sees a source file newer than the last
build. Hot reloading and a production server are alternatives rather than
settings, because Fast Refresh *is* the development bundler watching your
files; a production server has none resident to do it.

Use the default while working on TrendRelay, and `--production` when using it.

If setup is interrupted, run `.\start.cmd` again. Completed work is reused. To verify an existing Python environment without downloading anything, run:

```powershell
.\.venv\Scripts\python.exe scripts\bootstrap.py --check
```

### Connect Douyin

Use **Refresh session** on Home and complete the Douyin login in the opened browser. The equivalent command is:

```powershell
npm run douyin -- connect
```

The session is stored locally under `.data/douyin/`. Refresh it only when Douyin rejects a download or the saved session expires.

### Import Shopee Product Offers

Open **Attribution**, choose **Add products**, and use **Shopee CSV export**:

1. Select **Open Shopee Product Offer**. Only this explicit action opens the real Shopee Affiliate page in your normal browser; background checks never open a window.
2. Sign in directly with Shopee, select up to 100 products, and export the CSV file from **Hoa hồng Sản phẩm / Product Offer**.
3. Choose that `.csv` in TrendRelay. Review the readable, new, existing, duplicate, and invalid-row counts, then import.

This supported path does not give TrendRelay a Shopee cookie and does not depend on automated browsing or passing a CAPTCHA. CSV files are decoded as UTF-8 (with or without a BOM), limited to 5 MB and 100 products, and validated before import. Shopee's CSV has no image URL, so Shopee rows do not show or reserve an empty thumbnail. Its `Link ưu đãi` column already contains the commission-bearing affiliate URL; TrendRelay stores and exposes that exact URL without automatically creating another redirect. First-party tracking remains available as a separate, explicit campaign action when measurement is actually required. Existing `.xlsx` exports remain accepted as a compatibility fallback. For a small batch, the same dialog also accepts up to 100 HTTPS Shopee product links.

## From Library to publishing and revenue

1. Open **Campaigns**, create or activate a campaign, and select one or more clips from the Library.
2. Optionally choose **Apply effects** to render one non-destructive effect stack across the selected clips.
3. Add shared campaign copy, then edit individual captions and hashtags where a clip needs different wording.
4. Assign one or more connected social accounts. Choose an imported affiliate offer; TrendRelay creates destination-specific tracking links and places them according to each network.
5. Open **Schedule** in Campaigns, add posting times, preview the next day, approve the queue, and choose **Deploy campaign**. Draft delivery is the safest first live run.
6. For a one-off governed post, choose its clip from Library, its exact connected account from Publish, its posting time from the saved Schedule, and—when relevant—its imported offer from Attribution. Opening an approved plan in Publish restores the destination account together with its clip, copy, disclosure, affiliate placement, and schedule.
7. Choose **Measure revenue** to open Attribution focused on that campaign, including its plan count, tracking links, clicks, commission, and top-link chart.

Campaigns uses four compact work areas—**Media**, **Accounts**, **Schedule**, and **Settings**—with readiness shown beside them. The visual Library picker supports search plus effect, channel, source, and length filters; selection remains intact while filters change. **Measure revenue** remains the direct handoff to Attribution after deployment.

Campaigns does not ask operators to retype data that already exists elsewhere in TrendRelay. Destination choices are exact available accounts from Publish (including Threads, Facebook, Instagram, or TikTok when connected), posting choices are saved workspace times, affiliate choices are imported Attribution offers, and media comes from Library. The chosen account, publishing engine, and offer identity remain attached to a one-off plan so its Publish handoff does not ask the same questions again.

## Download behavior

TrendRelay stores downloaded files under `.data/downloads/douyin/` and automatically registers them in the media Library. Library refresh reconciles items removed from disk. Use **Clear missing files** on Home to remove download records whose files are gone; records that still reference on-disk media are kept.

For command-line batch downloads:

```powershell
npm run douyin -- batch "https://www.douyin.com/user/..." --limit 0
```

`--limit 0` means all available items. The Home interface selects this behavior by default for profile downloads.

## Local-first by design

- Downloads, cookies, databases, thumbnails, proxies, and job state stay in `.data/`.
- Provider source and isolated runtimes stay in `.tools/`.
- Both directories, along with `.env`, are excluded by `.gitignore`.
- Original media is kept immutable; generated previews and derivatives are stored separately.
- Acquired media defaults to **reference only** until reuse rights are reviewed.

Never commit real cookies, access tokens, downloaded media, customer data, or generated databases.

## Project status

TrendRelay is an active local-first product. Douyin acquisition, durable jobs, the media Library and effects, campaign planning, multi-engine publishing, Shopee CSV offer import, and first-party attribution are connected and covered by automated checks. External publishing still depends on the operator connecting at least one supported engine and following that engine's account and quota requirements.

Research integrations, ad collection, opportunity scoring, and additional production automation remain under active development.

## Tech stack

- **Web interface:** Next.js 16, React 19, TypeScript, and accessible local UI primitives.
- **API and workers:** Python 3.12+, FastAPI, SQLAlchemy, Alembic, and durable local job workers.
- **Media:** FFmpeg/ffprobe plus isolated Python integrations for downloading, inspection, and non-destructive effects.
- **Desktop:** Electron as an optional native window over the same local services.
- **Storage:** local SQLite and `.data/` media by default; optional public object storage only for publishing engines that fetch media by URL.
- **Quality:** Pytest, Node's test runner, TypeScript, ESLint, Ruff, and production Next.js builds.

## Interface conventions

- Primary workspaces use the same compact top navigation, page heading, workspace control, status language, and action hierarchy.
- Familiar actions use the shared Lucide icon vocabulary; icons supplement readable labels and icon-only controls retain accessible names and tooltips.
- Dense operational screens favor short toolbars, compact cards, sticky context only where it helps, and responsive icon-first navigation on narrow screens.
- Table search, selection counts, and bulk actions share a stable toolbar slot; changing selection state must not insert controls that shift the rows below.
- Native browser controls remain where they provide the clearest accessible interaction; custom styling follows [DESIGN.md](DESIGN.md) rather than replacing predictable behavior for decoration alone.

## Development

Run the web and API development environment:

```powershell
npm ci
python -m venv .venv
.\.venv\Scripts\python.exe scripts\bootstrap.py
.\.venv\Scripts\python.exe scripts\db.py upgrade
npm run dev
```

Validate changes with:

```powershell
npm run release:check
```

The main code lives in:

```text
apps/web       Next.js interface
services/api   FastAPI control plane and media catalog
scripts        Development supervisor and Douyin integration
workers        Background media and publishing workers
```

See [DESIGN.md](DESIGN.md) for interaction principles, [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance, and [SECURITY.md](SECURITY.md) for private vulnerability reporting.

## Responsible use

Only download, retain, and reuse media you are authorized to access. Douyin integrations may rely on browser-facing or reverse-engineered interfaces that can change without notice and may be restricted by platform terms. Review the [third-party notices](docs/third-party/README.md) before redistribution.

No project-level software license has been selected yet. Until one is added, TrendRelay-authored code remains under default copyright while incorporated dependencies retain their respective licenses.
