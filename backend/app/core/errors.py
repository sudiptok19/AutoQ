"""Fail-closed error envelope: {error:{code,message,details}} (CONTRACT §1, §10.5)."""
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("autoq")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def _body(code: str, message: str, details: dict[str, Any], request: Request) -> dict:
    rid = getattr(request.state, "request_id", None)
    return {"error": {"code": code, "message": message, "details": {**details, **({"request_id": rid} if rid else {})}}}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        resp = await call_next(request)
        resp.headers["x-request-id"] = request.state.request_id
        return resp

    @app.exception_handler(ApiError)
    async def api_error(request: Request, e: ApiError):
        return JSONResponse(_body(e.code, e.message, e.details, request), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, e: RequestValidationError):
        errs = [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()]
        return JSONResponse(_body("VALIDATION", "Request validation failed", {"errors": errs}, request), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_exc(request: Request, e: StarletteHTTPException):
        code = {401: "UNAUTHENTICATED", 403: "ROLE_FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(e.status_code, f"HTTP_{e.status_code}")
        return JSONResponse(_body(code, str(e.detail), {}, request), status_code=e.status_code)

    @app.exception_handler(OperationalError)
    async def db_down(request: Request, e: OperationalError):
        log.exception("database unavailable")
        return JSONResponse(_body("DB_UNAVAILABLE", "Database unavailable", {"component": "postgres", "remediation": "Start it with `make db`."}, request), status_code=503)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, e: Exception):
        log.exception("unhandled error")
        return JSONResponse(_body("INTERNAL", "Unexpected server error", {}, request), status_code=500)
