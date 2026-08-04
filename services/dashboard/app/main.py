"""HTTP entry point for the OMK touch display dashboard."""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.data.latest_repository import LatestRepository
from app.data.parquet_repository import ParquetRepository
from app.view_models import get_display_view_model

APP_DIR = Path(__file__).parent

app = FastAPI(title="OMK Dashboard")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def get_parquet_repository() -> ParquetRepository:
    """Create the repository for daily Parquet totals."""
    return ParquetRepository(Path(os.environ.get("OMK_PROCESSED_DATA_ROOT", "data/processed")))


def get_latest_repository() -> LatestRepository:
    """Create the repository for collector-maintained latest records."""
    return LatestRepository(Path(os.environ.get("OMK_LATEST_DATA_ROOT", "data/latest")))


def get_dashboard_view_model():
    """Build one consistent snapshot for both HTML and polling API responses."""
    return get_display_view_model(get_latest_repository(), get_parquet_repository())


@app.get("/display", response_class=HTMLResponse)
async def display(request: Request) -> HTMLResponse:
    """Render the dashboard from latest JSON and daily Parquet totals."""
    return templates.TemplateResponse(
        request=request,
        name="display.html",
        context={"dashboard": get_dashboard_view_model()},
    )


@app.get("/api/display")
async def display_api() -> dict[str, str | bool]:
    """Return the current dashboard snapshot for in-page refreshes."""
    return get_dashboard_view_model().as_dict()


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a minimal liveness response."""
    return {"status": "ok"}
