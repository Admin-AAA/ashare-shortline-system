"""Shared wire contracts for API responses that have not yet earned a domain DTO."""
from __future__ import annotations

from pydantic import BaseModel, JsonValue, RootModel


class JsonResponse(RootModel[JsonValue]):
    """Explicit JSON response boundary; never emits an untyped OpenAPI response."""


class ErrorResponse(BaseModel):
    detail: JsonValue


class HealthResponse(BaseModel):
    ok: bool
    database: str
    application: str
    migration: dict[str, JsonValue]


class AuthStatusResponse(BaseModel):
    enabled: bool
    authenticated: bool


class OkResponse(BaseModel):
    ok: bool
