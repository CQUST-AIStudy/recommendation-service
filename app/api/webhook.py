"""
Spider-Repo 集成端点 — Webhook 回调 + 手动刷新

端点:
- POST /webhook/spider-import  — spider-repo 爬取完成后回调
- POST /internal/refresh-student  — 手动刷新单个学生 PTA 技能画像
- POST /internal/refresh-class  — 手动刷新整个教学班
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.responses import ApiError, api_success
from app.schemas.webhook import (
    ClassIngestionResponse,
    IngestionResponse,
    RefreshClassRequest,
    RefreshStudentRequest,
    SpiderImportWebhook,
)
from app.services.pta_ingestion import ingest_pta_data_for_student, ingest_pta_data_for_class

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])
internal_router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/spider-import")
def handle_spider_import(request: SpiderImportWebhook):
    """
    spider-repo 爬取完成后的回调端点。

    当 spider-repo 完成一次 import_job 后，调用此接口通知
    recommendation-service 摄取新数据并更新学生技能画像。

    支持两种模式:
    1. 指定 student_ids/student_nos: 只刷新指定学生
    2. 指定 class_id: 刷新整个教学班
    """
    try:
        # 如果指定了具体学生，只刷新这些学生
        if request.student_ids or request.student_nos:
            results = []
            total_tags = 0
            for sid in request.student_ids:
                r = ingest_pta_data_for_student(student_id=sid)
                total_tags += r.get("tags_updated", 0)
                results.append(IngestionResponse(**r).model_dump(by_alias=True))
            for sno in request.student_nos:
                r = ingest_pta_data_for_student(student_no=sno)
                total_tags += r.get("tags_updated", 0)
                results.append(IngestionResponse(**r).model_dump(by_alias=True))

            return api_success({
                "mode": "students",
                "results": results,
                "total_tags_updated": total_tags,
            })

        # 如果指定了 class_id，刷新整个班级
        if request.class_id is not None:
            result = ingest_pta_data_for_class(request.class_id)
            return api_success(ClassIngestionResponse(**result).model_dump(by_alias=True))

        raise ApiError(400, "Must provide classId, studentIds, or studentNos")
    except ApiError:
        raise
    except Exception as e:
        logger.error("Spider import webhook failed: %s", e, exc_info=True)
        raise ApiError(500, f"Webhook processing failed: {e}") from e


@internal_router.post("/refresh-student")
def refresh_student(request: RefreshStudentRequest):
    """手动刷新单个学生的 PTA 技能画像。"""
    try:
        if request.student_id is None and request.student_no is None:
            raise ApiError(400, "Must provide studentId or studentNo")

        result = ingest_pta_data_for_student(
            student_id=request.student_id,
            student_no=request.student_no,
        )
        return api_success(IngestionResponse(**result).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Refresh student failed: %s", e, exc_info=True)
        raise ApiError(500, f"Refresh failed: {e}") from e


@internal_router.post("/refresh-class")
def refresh_class(request: RefreshClassRequest):
    """手动刷新整个教学班的 PTA 技能画像。"""
    try:
        result = ingest_pta_data_for_class(request.class_id)
        return api_success(ClassIngestionResponse(**result).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Refresh class failed: %s", e, exc_info=True)
        raise ApiError(500, f"Refresh failed: {e}") from e
