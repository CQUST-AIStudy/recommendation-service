from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, recommendation, skill_profile, webhook
from app.core.config import get_settings
from app.core.responses import ApiError, api_error_response
from app.db import mysql_client

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="Recommendation Service",
    version="0.1.0",
    description="AI-driven student skill profiling & personalized LeetCode recommendation microservice",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApiError)
async def api_error_handler(_request: Request, exc: ApiError):
    return api_error_response(exc.status_code, exc.message, exc.code)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    return api_error_response(422, str(exc), 422)


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception):
    logging.getLogger(__name__).error("Unhandled error: %s", exc, exc_info=True)
    return api_error_response(500, f"Service internal error: {exc}", 500)


app.include_router(health.router)
app.include_router(skill_profile.router)
app.include_router(recommendation.router)
app.include_router(webhook.router)
app.include_router(webhook.internal_router)


@app.on_event("shutdown")
def shutdown_db_pool():
    mysql_client.close_all()
