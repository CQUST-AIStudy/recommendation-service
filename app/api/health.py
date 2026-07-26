from __future__ import annotations

from fastapi import APIRouter

from app import db as db_mod
from app.core.config import get_settings
from app.core.responses import api_success

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    return api_success({"status": "ok", "service": "recommendation-service"})


@router.get("/ready")
def readiness_check():
    settings = get_settings()
    with db_mod.query() as cursor:
        cursor.execute("SELECT COUNT(*) AS n FROM leetcode_problem_bank")
        problem_count = int(cursor.fetchone()["n"])
        cursor.execute("SELECT COUNT(*) AS n FROM leetcode_problem_tag")
        tag_count = int(cursor.fetchone()["n"])
        cursor.execute("SELECT COUNT(DISTINCT problem_id) AS n FROM leetcode_problem_tag")
        tagged_problem_count = int(cursor.fetchone()["n"])

    embedding_count = 0
    embedding_error = None
    if settings.embedding_enabled:
        try:
            embedding_count = db_mod.find_embedding_count(
                settings.embedding_model_name,
                settings.embedding_model_revision,
                settings.embedding_preprocessing_version,
                settings.embedding_expected_dim,
            )
        except Exception as exc:
            embedding_error = str(exc)

    return api_success({
        "status": "ready",
        "service": "recommendation-service",
        "problemCount": problem_count,
        "tagCount": tag_count,
        "taggedProblemCount": tagged_problem_count,
        "tagCoverage": (tagged_problem_count / problem_count) if problem_count else 0.0,
        "embedding": {
            "enabled": settings.embedding_enabled,
            "ready": not settings.embedding_enabled or embedding_count > 0,
            "model": settings.embedding_model_name,
            "revision": settings.embedding_model_revision,
            "preprocessingVersion": settings.embedding_preprocessing_version,
            "dimension": settings.embedding_expected_dim,
            "count": embedding_count,
            "coverage": (embedding_count / problem_count) if problem_count else 0.0,
            "error": embedding_error,
        },
    })
