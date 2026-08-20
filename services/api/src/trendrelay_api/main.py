import asyncio
import os
import subprocess
from pathlib import Path
from typing import Annotated, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from trendrelay_api import __version__
from trendrelay_api.attribution_api import router as attribution_router
from trendrelay_api.auth import LOCAL_ADMIN_EMAIL, LOCAL_ADMIN_ID, local_auth_allowed
from trendrelay_api.campaign_autopilot_api import router as campaign_autopilot_router
from trendrelay_api.campaigns_api import router as campaigns_router
from trendrelay_api.catalog_api import router as catalog_router
from trendrelay_api.config import get_settings
from trendrelay_api.device_pairing import router as device_pairing_router
from trendrelay_api.foundation import router as foundation_router
from trendrelay_api.integrations.agent_reach import diagnostic_report
from trendrelay_api.integrations.last30days import (
    ResearchRequest,
    create_job,
    get_job,
    list_jobs,
    provider_status,
    run_job,
)
from trendrelay_api.integrations.meta_ads_collector import (
    MetaAdLibrarySearchRequest,
)
from trendrelay_api.integrations.meta_ads_collector import (
    provider_status as meta_ads_collector_status,
)
from trendrelay_api.integrations.meta_ads_collector import (
    search_ads as search_meta_ad_library,
)
from trendrelay_api.integrations.meta_ads_kit import (
    MetaBriefingRequest,
)
from trendrelay_api.integrations.meta_ads_kit import (
    provider_status as meta_ads_provider_status,
)
from trendrelay_api.integrations.meta_ads_kit import (
    run_briefing as run_meta_ads_briefing,
)
from trendrelay_api.integrations.news_feeds import DESKS, collect_news
from trendrelay_api.integrations.popular_posts import PERIODS as POST_PERIODS
from trendrelay_api.integrations.popular_posts import (
    collect_posts,
    live_bluesky_reader,
    live_hackernews_reader,
    live_reader,
    live_youtube_reader,
)
from trendrelay_api.integrations.tiktok_creative import (
    TikTokTrendRequest,
    TikTokUnavailable,
    fetch_tiktok_trends,
)
from trendrelay_api.integrations.tiktok_creative import (
    provider_status as tiktok_provider_status,
)
from trendrelay_api.integrations.trend_consolidation import SHAPES, WINDOWS
from trendrelay_api.integrations.trend_consolidation import rank as rank_topics
from trendrelay_api.integrations.trend_sources import collect as collect_sightings
from trendrelay_api.integrations.trend_sources import live_readers, live_trends_reader
from trendrelay_api.media_api import router as media_router
from trendrelay_api.media_library_api import router as media_library_router
from trendrelay_api.opportunities_api import router as opportunities_router
from trendrelay_api.production_api import router as production_router
from trendrelay_api.publishing_api import router as publishing_router
from trendrelay_api.signals_api import router as signals_router
from trendrelay_api.tool_registry import (
    documentation_for,
    PROJECT_ROOT,
    ToolRegistryError,
    install_tool,
    list_tools,
    set_active,
    uninstall_tool,
)
from trendrelay_api.tool_setup import (
    launch_setup_action,
    setup_report,
)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=__version__,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)
app.include_router(foundation_router)
app.include_router(attribution_router)
app.include_router(campaigns_router)
app.include_router(signals_router)
app.include_router(campaign_autopilot_router)
app.include_router(catalog_router)
app.include_router(device_pairing_router)
app.include_router(publishing_router)
app.include_router(media_router)
app.include_router(media_library_router)
app.include_router(opportunities_router)
app.include_router(production_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=(
        r"^http://(?:localhost|127\.0\.0\.1|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}):(3000|3001)$"
        if settings.environment != "production"
        else None
    ),
    # The web app edits and removes records directly. Browsers preflight these
    # cross-origin requests, so omitting PATCH/DELETE makes a healthy endpoint
    # look like a network outage (the frontend only receives "Failed to
    # fetch"). Keep this list aligned with the methods exposed by the API.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    # A response header is invisible to a cross-origin reader unless it is
    # named here. Without this the blur preview read its face count as zero and
    # said no face was found over a picture of a blurred face.
    expose_headers=["X-Faces-Found", "X-Frame-Position", "X-Clip-Duration"],
)


class Confirmation(BaseModel):
    confirm_external_action: bool = False


class Activation(BaseModel):
    active: bool


class SetupActionRequest(BaseModel):
    confirm_external_action: bool = False


def require_local_mutation(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Tool changes are local-machine only.")


def registry_error(error: ToolRegistryError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(error))


@app.get("/healthz", tags=["operations"])
async def health() -> dict[str, str]:
    return {
        "service": "trendrelay-api",
        "status": "ok",
        "version": __version__,
    }


@app.get("/api/auth/local-session", tags=["authentication"])
async def local_session(request: Request) -> dict[str, object]:
    if not local_auth_allowed(request):
        return {"enabled": False, "user": None}
    return {
        "enabled": True,
        "user": {"id": LOCAL_ADMIN_ID, "email": LOCAL_ADMIN_EMAIL},
    }


@app.get("/api/tools", tags=["tools"])
async def tools() -> dict[str, object]:
    return {"tools": await asyncio.to_thread(list_tools)}


@app.get("/api/tools/{tool_id}/documentation", tags=["tools"])
async def tool_documentation(tool_id: str) -> dict[str, str]:
    try:
        return await asyncio.to_thread(documentation_for, tool_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Tool not found.") from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/tools/{tool_id}/setup", tags=["tools"])
async def tool_setup(tool_id: str) -> dict[str, object]:
    try:
        return {"setup": await asyncio.to_thread(setup_report, tool_id)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Tool not found.") from error


@app.post("/api/tools/{tool_id}/setup/{action_id}", tags=["tools"])
async def run_tool_setup_action(
    tool_id: str,
    action_id: str,
    body: SetupActionRequest,
    request: Request,
) -> dict[str, object]:
    require_local_mutation(request)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Launching interactive authentication requires explicit confirmation.",
        )
    try:
        return {"result": await asyncio.to_thread(launch_setup_action, tool_id, action_id)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Setup action not found.") from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


class ToolSettings(BaseModel):
    """Settings to write, and the operator's word that they meant it."""

    #: Partial by design. A key left out keeps whatever is saved, which is what
    #: lets the secret field submit nothing and mean "leave the key alone"
    #: rather than "clear it" - the same rule the credential rows follow.
    values: dict[str, str] = Field(default_factory=dict, max_length=20)
    confirm_external_action: bool = False


@app.post("/api/tools/{tool_id}/settings", tags=["tools"])
async def save_tool_settings(
    tool_id: str,
    body: ToolSettings,
    request: Request,
) -> dict[str, object]:
    """Write a tool's own settings to the local .env.

    Only the assistant tunnel has any, and only the keys it declares: without
    that restriction this is "write any environment variable", behind a button
    meant for a tunnel id.
    """
    require_local_mutation(request)
    if tool_id != "mcp-server":
        raise HTTPException(status_code=404, detail="This tool has no editable settings.")
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Saving settings requires confirmation.")

    from trendrelay_api.env_store import EnvWriteError
    from trendrelay_api.integrations.mcp import tunnel

    try:
        written = await asyncio.to_thread(tunnel.save_settings, body.values)
    except tunnel.TunnelSettingsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except EnvWriteError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    # The refreshed report comes back with the save, so the form redraws from
    # what is now stored rather than from what was typed into it.
    return {
        "written": written,
        "setup": await asyncio.to_thread(setup_report, tool_id),
    }


@app.get("/api/tools/agent-reach/diagnostics", tags=["tools"])
async def agent_reach_diagnostics() -> dict[str, object]:
    return {"diagnostics": await asyncio.to_thread(diagnostic_report)}


class PathPayload(BaseModel):
    path: str


@app.post("/api/tools/open-folder", tags=["tools"])
async def open_folder(request: Request, body: PathPayload) -> dict[str, object]:
    require_local_mutation(request)
    if os.name != "nt":
        raise HTTPException(status_code=400, detail="Only supported on Windows.")

    path = Path(body.path).resolve()
    allowed_roots = {
        (PROJECT_ROOT / ".data" / "downloads").resolve(),
        (PROJECT_ROOT / ".data" / "media").resolve(),
        (PROJECT_ROOT / ".data" / "manual-packages").resolve(),
    }
    if not any(path == root or root in path.parents for root in allowed_roots):
        raise HTTPException(
            status_code=403,
            detail="Only TrendRelay media and package folders may be opened.",
        )
    if path.is_file():
        path = path.parent
    elif not path.is_dir():
        raise HTTPException(status_code=404, detail="Media folder does not exist.")

    subprocess.Popen(["explorer", str(path)])

    return {"status": "ok"}


@app.post("/api/tools/{tool_id}/install", tags=["tools"])
async def install(tool_id: str, body: Confirmation, request: Request) -> dict[str, object]:
    require_local_mutation(request)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Installation requires explicit confirmation.")
    try:
        return {"tool": await asyncio.to_thread(install_tool, tool_id)}
    except ToolRegistryError as error:
        raise registry_error(error) from error


@app.post("/api/tools/{tool_id}/uninstall", tags=["tools"])
async def uninstall(tool_id: str, body: Confirmation, request: Request) -> dict[str, object]:
    require_local_mutation(request)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Uninstall requires explicit confirmation.")
    try:
        return {"tool": await asyncio.to_thread(uninstall_tool, tool_id)}
    except ToolRegistryError as error:
        raise registry_error(error) from error


@app.post("/api/tools/{tool_id}/activation", tags=["tools"])
async def activate(tool_id: str, body: Activation, request: Request) -> dict[str, object]:
    require_local_mutation(request)
    try:
        return {"tool": await asyncio.to_thread(set_active, tool_id, body.active)}
    except ToolRegistryError as error:
        raise registry_error(error) from error


@app.get("/api/media-ai/providers", tags=["tools"])
async def media_ai_providers() -> dict[str, object]:
    """What can transcribe, translate or read a frame right now, and why not.

    Read from two places - the Tools page and the Library, which needs the same
    answer to say whether its transcription switch is a switch or a download -
    so it is one endpoint rather than the same three checks written twice.
    """
    # The module rather than its names: `provider_status` is already bound at
    # the top of this file by the research provider, which is a different thing
    # entirely.
    from trendrelay_api import media_ai

    providers, jobs = await asyncio.gather(
        asyncio.to_thread(media_ai.provider_status),
        asyncio.to_thread(media_ai.latest_setup_jobs),
    )
    return {"providers": providers, "setup_jobs": jobs}


@app.post("/api/media-ai/providers/{provider}/prepare", status_code=202, tags=["tools"])
async def prepare_media_ai_provider(
    provider: str, body: Confirmation, request: Request
) -> dict[str, object]:
    """Queue the download that makes a provider usable.

    Deliberately not done inline. This fetches a pinned checkout, a few hundred
    megabytes of wheels and a model, which is minutes of work - the interface
    gets a job to watch instead of a request that appears to hang.
    """
    require_local_mutation(request)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Downloading a provider runtime requires explicit confirmation.",
        )
    from trendrelay_api.media_ai import create_setup_job

    try:
        job = await asyncio.to_thread(
            create_setup_job, provider, actor_user_id="local-operator"
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"job": job}


@app.get("/api/research/status", tags=["research"])
async def research_status() -> dict[str, object]:
    last30days_status, reach_status, meta_ads_status, collector_status = await asyncio.gather(
        asyncio.to_thread(provider_status),
        asyncio.to_thread(diagnostic_report),
        asyncio.to_thread(meta_ads_provider_status),
        asyncio.to_thread(meta_ads_collector_status),
    )
    return {
        "provider": last30days_status,
        "providers": {
            "last30days": last30days_status,
            "agent_reach": reach_status,
            "meta_ads": meta_ads_status,
            "meta_ads_collector": collector_status,
        },
    }


@app.post("/api/research/meta-ads/library/search", tags=["research"])
async def meta_ads_library_search(
    body: MetaAdLibrarySearchRequest,
    request: Request,
) -> dict[str, object]:
    require_local_mutation(request)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Public Meta Ad Library search requires explicit confirmation.",
        )
    try:
        result = await asyncio.to_thread(search_meta_ad_library, body)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"result": result}


@app.get("/api/research/tiktok/status", tags=["research"])
async def tiktok_status() -> dict[str, object]:
    return {"provider": await asyncio.to_thread(tiktok_provider_status)}


@app.get("/api/research/tiktok/discovery/{category}", tags=["research"])
async def tiktok_discovery(
    category: str,
    request: Request,
    region: str = Query(default="US", min_length=2, max_length=2),
    period: int = Query(default=7),
    limit: int = Query(default=10, ge=1, le=50),
    refresh: bool = Query(default=False),
) -> dict[str, object]:
    require_local_mutation(request)
    try:
        trend_request = TikTokTrendRequest(
            category=category, region=region, period=period, limit=limit
        )
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=f"Invalid TikTok query: {error}") from error
    try:
        result = await asyncio.to_thread(fetch_tiktok_trends, trend_request, not refresh)
    except TikTokUnavailable as error:
        # The page rendered nothing usable; that is a provider state, not a bug.
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"result": result}


def _consolidated_trends(
    region: str, windows: tuple[int, ...], shapes: tuple[str, ...] | None, limit: int
) -> dict[str, object]:
    """One ranked list of topics, and everything the fetch could not do.

    Kept as a plain function so the blocking provider calls happen in a thread
    and the route stays readable.
    """
    tiktok_reader, douyin_reader = live_readers()
    collected = collect_sightings(
        region=region,
        windows=windows,
        limit=limit,
        tiktok_reader=tiktok_reader,
        douyin_reader=douyin_reader,
        trends_reader=live_trends_reader(),
    )
    topics = rank_topics(collected["sightings"], shapes=shapes)
    return {
        "region": collected["region"],
        "windows": collected["windows"],
        "sources": collected["sources"],
        # A caller can tell "nothing is trending" from "we could not look".
        "complete": collected["complete"],
        "notes": collected["notes"],
        "topics": topics,
        "topic_count": len(topics),
        "public_data_only": True,
    }


@app.get("/api/research/posts/popular", tags=["research"])
async def popular_posts(
    request: Request,
    region: str = Query(default="US", min_length=2, max_length=2),
    period: int = Query(default=7),
    limit: int = Query(default=20, ge=1, le=50),
    platform: Literal[
        "all", "tiktok", "youtube", "bluesky", "hackernews"
    ] = Query(default="all"),
) -> dict[str, object]:
    """The posts doing best in one country, and who made them.

    TikTok supplies a selected time window. YouTube supplies its current
    regional popular chart and says so on each row rather than inheriting the
    TikTok window. ``all`` uses every configured provider.

    Every source below is selectable on its own. Bluesky and Hacker News were
    added to the provider list and to the interface's dropdown but not here, so
    picking either answered 422 - and they are the two that need no key, which
    makes them the likeliest to be picked. `test_every_advertised_source_can_be
    _asked_for_on_its_own` holds the two lists level.
    """
    require_local_mutation(request)
    if period not in POST_PERIODS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown period {period}. Expected one of {', '.join(map(str, POST_PERIODS))}.",
        )
    youtube_key = get_settings().youtube_data_api_key.get_secret_value().strip()
    providers = [
        {"id": "tiktok", "label": "TikTok", "available": True, "reason": None},
        {
            "id": "youtube",
            "label": "YouTube",
            "available": bool(youtube_key),
            "reason": None if youtube_key else "Add YOUTUBE_DATA_API_KEY to enable this source.",
        },
        # Always available: the only post source needing neither a key nor a
        # signed-in session, and the only one that is not short-form video.
        # The nearest readable substitute for Threads, which publishes no
        # popular feed at all. Public and unauthenticated, needing no key.
        {"id": "bluesky", "label": "Bluesky", "available": True, "reason": None},
        # Narrow - a technology and startup audience - but reliably early
        # on anything software, hardware or business-model shaped.
        {"id": "hackernews", "label": "Hacker News", "available": True, "reason": None},
    ]
    if platform == "youtube" and not youtube_key:
        raise HTTPException(status_code=409, detail=providers[1]["reason"])

    if platform == "all":
        # Everything that can answer. A source needing a key it does not have
        # is left out rather than added and then reported as a failure.
        platforms = tuple(
            item["id"] for item in providers if item["available"]
        )
    else:
        platforms = (platform,)
    result = await asyncio.to_thread(
        collect_posts,
        region=region,
        period=period,
        limit=limit,
        tiktok_reader=live_reader(),
        youtube_reader=live_youtube_reader(youtube_key) if youtube_key else None,
        bluesky_reader=live_bluesky_reader(),
        hackernews_reader=live_hackernews_reader(),
        platforms=platforms,
    )
    result["platform"] = platform
    result["providers"] = providers
    if not result["posts"] and not result["complete"]:
        # Nothing answered, which is a provider state rather than a quiet week.
        raise HTTPException(status_code=503, detail=" ".join(result["notes"]))
    return result


@app.get("/api/research/news", tags=["research"])
async def research_news(
    request: Request,
    desk: str = Query(default="all"),
    limit: int = Query(default=6, ge=1, le=20),
) -> dict[str, object]:
    """What is happening, and how many newsrooms agree that it is.

    No key and no session: these are public syndication feeds, which is why
    this source is always available where most of the others are not.
    """
    require_local_mutation(request)
    if desk != "all" and desk not in DESKS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown desk {desk}. Expected one of all, {', '.join(DESKS)}.",
        )
    result = await asyncio.to_thread(collect_news, desk=desk, limit=limit)
    if not result["covered"] and not result["breaking"] and not result["complete"]:
        # Every newsroom refused, which is a provider state rather than a
        # quiet news day. A quiet day still answers, with empty shelves.
        raise HTTPException(status_code=503, detail=" ".join(result["notes"]))
    return result


@app.get("/api/research/trends/consolidated", tags=["research"])
async def consolidated_trends(
    request: Request,
    region: str = Query(default="US", min_length=2, max_length=2),
    shape: Annotated[list[str] | None, Query()] = None,
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, object]:
    """Trending topics merged across sources and ranked for what to make next.

    Every window is always fetched, because the shape of a topic is read from
    which windows it appears in: asking for only the last 7 days would make
    every topic `single` and take the evergreen reading away entirely. `shape`
    narrows what comes back afterwards, which is the time control - `durable`
    for evergreen ideas, `emerging` for something quick.
    """
    require_local_mutation(request)
    wanted = tuple(shape or ())
    unknown = [value for value in wanted if value not in SHAPES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown trend shape {unknown[0]!r}. Expected one of {', '.join(SHAPES)}.",
        )
    result = await asyncio.to_thread(
        _consolidated_trends, region, WINDOWS, wanted or None, limit
    )
    if not result["topics"] and not result["complete"]:
        # No sources answered at all: a provider state rather than an empty week.
        raise HTTPException(status_code=503, detail=" ".join(result["notes"]))
    return result


@app.post("/api/research/meta-ads/briefing", tags=["research"])
async def meta_ads_briefing(body: MetaBriefingRequest, request: Request) -> dict[str, object]:
    require_local_mutation(request)
    if not body.confirm_external_action:
        raise HTTPException(
            status_code=400,
            detail="Meta Ads briefing requires explicit confirmation.",
        )
    try:
        briefing = await asyncio.to_thread(run_meta_ads_briefing, body)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"briefing": briefing}


@app.get("/api/research/jobs", tags=["research"])
async def research_jobs(
    workspace_id: str = Query(default="local", min_length=1, max_length=80),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    return {"jobs": await asyncio.to_thread(list_jobs, workspace_id, limit)}


@app.get("/api/research/jobs/{job_id}", tags=["research"])
async def research_job(job_id: str) -> dict[str, object]:
    try:
        return {"job": await asyncio.to_thread(get_job, job_id)}
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Research job not found.") from error


@app.post("/api/research/jobs", tags=["research"], status_code=202)
async def start_research(
    body: ResearchRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    require_local_mutation(request)
    if not body.confirm_external_action:
        raise HTTPException(status_code=400, detail="Research requires explicit confirmation.")
    if body.mock and settings.environment == "production":
        raise HTTPException(status_code=400, detail="Mock research is disabled in production.")
    job = await asyncio.to_thread(create_job, body)
    background_tasks.add_task(run_job, job["id"], body)
    return {"job": job}
