"""One error shape for the whole API: ``{"error": {"code", "message", "fields"?}}``.

``code`` is stable and machine-readable (the web app switches on it); ``message`` is plain language for people.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        fields: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers
        self.fields = fields


def _body(code: str, message: str, fields: dict[str, str] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return {"error": error}


def _field_name(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header", "cookie")]
    return ".".join(parts) or "request"


def _friendly(error: dict[str, Any]) -> str:
    kind = str(error.get("type", ""))
    if kind == "missing":
        return "This field is required."
    if kind == "value_error":
        # Our own validators: pydantic prefixes "Value error, ".
        return str(error.get("msg", "")).removeprefix("Value error, ")
    if "email" in str(error.get("msg", "")).lower():
        return "Enter a valid email address."
    if kind == "string_too_long":
        return "This is too long."
    if kind == "string_too_short":
        return "This field is required."
    if kind == "extra_forbidden":
        return "This field is not allowed."
    return "This value is not valid."


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            _body(exc.code, exc.message, exc.fields),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields: dict[str, str] = {}
        for error in exc.errors():
            fields.setdefault(_field_name(tuple(error.get("loc", ()))), _friendly(error))
        return JSONResponse(
            _body("validation_error", "Check the highlighted fields.", fields), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed"}
        message = {404: "Not found.", 405: "Method not allowed."}.get(
            exc.status_code, str(exc.detail)
        )
        return JSONResponse(
            _body(codes.get(exc.status_code, "http_error"), message),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
