"""AutoQ-Trace API. Owner: Opus — the only place routers are registered."""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import errors
from app.core.config import settings
from app.features.auth_evidence.router import router as auth_evidence
from app.adapters.ports_impl import PgPorts
from app.features.alerts import relay
from app.features.alerts.router import router as alerts, ws_router
from app.features.catalog_sampling.router import router as catalog_sampling
from app.features.containment.router import router as containment
from app.features.dispatch.router import router as dispatch
from app.features.packaging.router import router as packaging
from app.features.offline_scanner.router import router as offline_scanner
from app.features.recall.router import router as recall
from app.features.report_workflow.router import router as report_workflow
from app.features.component_vision.router import router as component_vision  # Astra A1
from app.features.seal_vision.router import router as seal_vision  # Astra A2
from app.features.inspection_passports.router import router as inspection_passports
from app.features.passport_crypto.router import router as passport_crypto  # Astra A4
from app.features.scar_pdf.router import router as scar_pdf  # Astra A5
from app.features.spc.router import router as spc  # Astra A3
from app.ports import register_ports
from app.features.platform.router import router as platform

API = "/api/v1"



@asynccontextmanager
async def lifespan(app: FastAPI):
    task = None if os.environ.get("AUTOQ_DISABLE_RELAY") == "1" else asyncio.create_task(relay.run_forever())
    yield
    if task:
        task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title="AutoQ-Trace API", version=settings().app_version, openapi_url="/openapi.json", docs_url="/docs", lifespan=lifespan)
    errors.install(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings().cors_list, allow_credentials=False, allow_methods=["*"], allow_headers=["*"], expose_headers=["x-request-id"])
    register_ports(PgPorts())
    for r in (platform, auth_evidence, catalog_sampling, inspection_passports, containment, packaging, dispatch, offline_scanner, recall, component_vision, seal_vision, passport_crypto, spc, report_workflow, scar_pdf, alerts):
        app.include_router(r, prefix=API)
    app.include_router(ws_router)  # /ws/events (same origin, ticket auth)
    return app


app = create_app()
