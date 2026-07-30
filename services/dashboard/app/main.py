"""HTTP entry point for the OMK touch display dashboard."""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.data.parquet_repository import ParquetRepository
from app.view_models import get_display_view_model

APP_DIR = Path(__file__).parent

app = FastAPI(title="OMK Dashboard")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def get_repository() -> ParquetRepository:
    """Create a repository using the configurable processed-data root."""
    return ParquetRepository(Path(os.environ.get("OMK_PROCESSED_DATA_ROOT", "data/processed")))


@app.get("/display", response_class=HTMLResponse)
async def display(request: Request) -> HTMLResponse:
    """Render the touch-friendly dashboard from processed measurements."""
    return templates.TemplateResponse(
        request=request,
        name="display.html",
        context={"dashboard": get_display_view_model(get_repository())},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a minimal liveness response."""
    return {"status": "ok"}
