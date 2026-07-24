from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.database import Base, engine
from app.routers import admin_router, auth_router, dashboard_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Dashboard Raviera API")

app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(dashboard_router.router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    def index():
        return FileResponse(str(FRONTEND_DIR / "index.html"))
