import asyncio

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from trendrelay_api import __version__
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
from trendrelay_api.production_api import router as production_router
from trendrelay_api.publishing_api import router as publishing_router
from trendrelay_api.tool_registry import (
    ToolRegistryError,
    install_tool,
    list_tools,
    set_active,
    uninstall_tool,
)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=__version__,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)
app.include_router(foundation_router)
app.include_router(device_pairing_router)
app.include_router(publishing_router)
app.include_router(production_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=(
        r"^http://(?:localhost|127\.0\.0\.1|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}):3000$"
        if settings.environment != "production"
        else None
    ),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


class Confirmation(BaseModel):
    confirm_external_action: bool = False


class Activation(BaseModel):
    active: bool


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


@app.get("/api/tools", tags=["tools"])
async def tools() -> dict[str, object]:
    return {"tools": await asyncio.to_thread(list_tools)}


@app.get("/api/tools/agent-reach/diagnostics", tags=["tools"])
async def agent_reach_diagnostics() -> dict[str, object]:
    return {"diagnostics": await asyncio.to_thread(diagnostic_report)}


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


@app.get("/api/research/status", tags=["research"])
async def research_status() -> dict[str, object]:
    return {"provider": await asyncio.to_thread(provider_status)}


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
