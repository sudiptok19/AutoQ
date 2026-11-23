"""AutoQ-Trace API. Owner: Opus — the only place routers are registered."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import errors
from app.core.config import settings
from app.features.auth_evidence.router import router as auth_evidence
from app.features.catalog_sampling.router import router as catalog_sampling
from app.features.platform.router import router as platform

API = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(title="AutoQ-Trace API", version=settings().app_version, openapi_url="/openapi.json", docs_url="/docs")
    errors.install(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings().cors_list, allow_credentials=False, allow_methods=["*"], allow_headers=["*"], expose_headers=["x-request-id"])
    for r in (platform, auth_evidence, catalog_sampling):
        app.include_router(r, prefix=API)
    return app


app = create_app()
