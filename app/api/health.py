from __future__ import annotations

from fastapi import APIRouter

from app.core.responses import api_success

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    return api_success({"status": "ok", "service": "recommendation-service"})
