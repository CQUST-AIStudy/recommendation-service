from __future__ import annotations

import logging

from fastapi import APIRouter

from app import db as db_mod
from app.core.responses import ApiError, api_success
from app.schemas.recommendation import (
    ExposureRequest,
    FeedbackRequest,
    FeedbackResponse,
    GenerateRequest,
    GenerateResponse,
    RecommendItemResponse,
    RecommendProblemInfo,
    ResultResponse,
    SyncRequest,
)
from app.services import recommendation_service
from app.services.feedback import record_feedback

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/recommendation", tags=["recommendation"])


@router.post("/generate")
def generate_recommendation(request: GenerateRequest):
    """
    生成个性化推荐（异步，返回 requestId 供轮询）。
    """
    try:
        request_id = recommendation_service.generate_recommendation(
            student_id=request.student_id,
            limit=request.limit,
            scene=request.scene,
        )
        return api_success(GenerateResponse(
            request_id=request_id,
            status="pending",
        ).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Generate recommendation failed: %s", e, exc_info=True)
        raise ApiError(500, f"Generate failed: {e}") from e


@router.get("/result/{requestId}")
def get_recommendation_result(requestId: str):
    """轮询推荐结果。"""
    try:
        req = db_mod.get_request(requestId)
        if req is None:
            raise ApiError(404, f"Request {requestId} not found")

        status = req.get("status", "pending")
        created_at = str(req["created_at"]) if req.get("created_at") else None
        finished_at = str(req["finished_at"]) if req.get("finished_at") else None

        if status != "completed":
            resp = ResultResponse(
                request_id=requestId,
                status=status,
                student_id=req.get("student_id"),
                scene=req.get("scene"),
                request_limit=req.get("request_limit"),
                created_at=created_at,
                finished_at=finished_at,
                error_message=req.get("error_message") if status == "failed" else None,
                items=[],
            )
            return api_success(resp.model_dump(by_alias=True))

        # 加载推荐结果
        raw_items = db_mod.find_recommend_items(requestId)
        items = []
        for row in raw_items:
            problem_info = None
            if row.get("title_main"):
                problem_info = RecommendProblemInfo(
                    problem_id=row["problem_id"],
                    title=row.get("title_main", ""),
                    difficulty=row.get("difficulty", ""),
                    source_url=row.get("source_url"),
                    estimated_minutes=row.get("estimated_minutes", 30),
                )

            items.append(RecommendItemResponse(
                rank_no=row["rank_no"],
                problem_id=row["problem_id"],
                score_total=float(row.get("score_total", 0)),
                score_need_match=float(row.get("score_need_match", 0)),
                score_difficulty_fit=float(row.get("score_difficulty_fit", 0)),
                score_success_prob=float(row.get("score_success_prob", 0)),
                score_novelty=float(row.get("score_novelty", 0)),
                score_quality=float(row.get("score_quality", 0)),
                reason_text=row.get("reason_text", ""),
                problem=problem_info,
            ))

        return api_success(ResultResponse(
            request_id=requestId,
            status="completed",
            student_id=req.get("student_id"),
            scene=req.get("scene"),
            request_limit=req.get("request_limit"),
            created_at=created_at,
            finished_at=finished_at,
            items=items,
        ).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Get result failed: %s", e, exc_info=True)
        raise ApiError(500, f"Get result failed: {e}") from e


@router.post("/exposure")
def record_exposure(request: ExposureRequest):
    """记录推荐曝光。"""
    try:
        # 从 requestId 获取 studentId
        req = db_mod.get_request(request.request_id)
        if not req:
            raise ApiError(404, f"Request {request.request_id} not found")

        success = record_feedback(
            request_id=request.request_id,
            student_id=req["student_id"],
            problem_id=request.problem_id,
            action="exposure",
            session_id=request.session_id,
        )
        return api_success(FeedbackResponse(success=success).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Record exposure failed: %s", e, exc_info=True)
        raise ApiError(500, f"Record exposure failed: {e}") from e


@router.post("/feedback")
def record_feedback_action(request: FeedbackRequest):
    """记录用户行为反馈（click/start/complete/skip/dislike）。"""
    try:
        action = request.action.strip().lower()
        valid_actions = {"click", "start", "complete", "skip", "dislike"}
        if action not in valid_actions:
            raise ApiError(400, f"Invalid action: {action}. Must be one of {valid_actions}")

        # 从 requestId 获取 studentId
        req = db_mod.get_request(request.request_id)
        student_id = req["student_id"] if req else 0

        success = record_feedback(
            request_id=request.request_id,
            student_id=student_id,
            problem_id=request.problem_id,
            action=action,
            session_id=request.session_id,
        )
        return api_success(FeedbackResponse(success=success).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Record feedback failed: %s", e, exc_info=True)
        raise ApiError(500, f"Record feedback failed: {e}") from e


@router.post("/sync")
def generate_recommendation_sync(request: SyncRequest):
    """同步生成推荐（直接返回推荐结果列表，不走异步轮询）。"""
    try:
        request_id = recommendation_service.generate_recommendation(
            student_id=request.student_id,
            limit=request.limit,
            scene="default",
        )
        # 立即查询结果（generate 内部已同步完成）
        req = db_mod.get_request(request_id)
        if req is None or req.get("status") != "completed":
            return api_success(ResultResponse(
                request_id=request_id,
                status="pending",
                items=[],
            ).model_dump(by_alias=True))

        raw_items = db_mod.find_recommend_items(request_id)
        items = []
        for row in raw_items:
            problem_info = None
            if row.get("title_main"):
                problem_info = RecommendProblemInfo(
                    problem_id=row["problem_id"],
                    title=row.get("title_main", ""),
                    difficulty=row.get("difficulty", ""),
                    source_url=row.get("source_url"),
                    estimated_minutes=row.get("estimated_minutes", 30),
                )
            items.append(RecommendItemResponse(
                rank_no=row["rank_no"],
                problem_id=row["problem_id"],
                score_total=float(row.get("score_total", 0)),
                score_need_match=float(row.get("score_need_match", 0)),
                score_difficulty_fit=float(row.get("score_difficulty_fit", 0)),
                score_success_prob=float(row.get("score_success_prob", 0)),
                score_novelty=float(row.get("score_novelty", 0)),
                score_quality=float(row.get("score_quality", 0)),
                reason_text=row.get("reason_text", ""),
                problem=problem_info,
            ))

        return api_success(ResultResponse(
            request_id=request_id,
            status="completed",
            items=items,
        ).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Sync recommendation failed: %s", e, exc_info=True)
        raise ApiError(500, f"Sync recommendation failed: {e}") from e
