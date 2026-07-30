"""HTTP entry point for the OMK touch display dashboard."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.view_models import get_display_view_model

APP_DIR = Path(__file__).parent

app = FastAPI(title="OMK Dashboard")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


@app.get("/display", response_class=HTMLResponse)
async def display(request: Request) -> HTMLResponse:
    """Render the touch-friendly dashboard with temporary data."""
    return templates.TemplateResponse(
        request=request,
        name="display.html",
        context={"dashboard": get_display_view_model()},
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a minimal liveness response."""
    return {"status": "ok"}
