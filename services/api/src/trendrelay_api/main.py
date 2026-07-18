from fastapi import FastAPI

from trendrelay_api import __version__
from trendrelay_api.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=__version__,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
)


@app.get("/healthz", tags=["operations"])
async def health() -> dict[str, str]:
    return {
        "service": "trendrelay-api",
        "status": "ok",
        "version": __version__,
    }
