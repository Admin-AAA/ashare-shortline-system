"""APIRouter that requires an explicit success response contract."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.datastructures import DefaultPlaceholder

from app.schemas.common import JsonResponse


class ContractRouter(APIRouter):
    def add_api_route(self, path: str, endpoint: Any, **kwargs: Any) -> None:
        response_model = kwargs.get("response_model")
        if response_model is None or isinstance(response_model, DefaultPlaceholder):
            kwargs["response_model"] = JsonResponse
        super().add_api_route(path, endpoint, **kwargs)
