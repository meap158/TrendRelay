import asyncio

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from trendrelay_api import __version__
from trendrelay_api.config import get_settings
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
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
