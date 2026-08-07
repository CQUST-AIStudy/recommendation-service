from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Query

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
from app.services.knowledge_tags import canonicalize_tag_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/recommendation", tags=["recommendation"])


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _build_result(req: dict, raw_items: list[dict] | None = None) -> ResultResponse:
    request_id = str(req["request_id"])
    status = str(req.get("status") or "pending")
    common = {
        "request_id": request_id,
        "status": status,
        "student_id": req.get("student_id"),
        "scene": req.get("scene"),
        "request_limit": req.get("request_limit"),
        "created_at": str(req["created_at"]) if req.get("created_at") else None,
        "finished_at": str(req["finished_at"]) if req.get("finished_at") else None,
        "error_message": req.get("error_message") if status == "failed" else None,
    }
    if status != "completed":
        return ResultResponse(**common, items=[])

    problem_ids = [int(row["problem_id"]) for row in raw_items or []]
    tags_by_problem = db_mod.find_tags_for_problems(problem_ids)
    skill_by_tag = {
        canonicalize_tag_name(str(state.get("tag_name") or "")): state
        for state in db_mod.find_all_skill_states(int(req.get("student_id") or 0))
        if state.get("tag_name")
    }
    items = []
    for row in raw_items or []:
        tag_names: list[str] = []
        problem_info = None
        if row.get("title_main"):
            tag_rows = tags_by_problem.get(int(row["problem_id"]), [])
            tag_names = [str(tag["tag_name"]) for tag in tag_rows if tag.get("tag_name")]
            problem_info = RecommendProblemInfo(
                problem_id=int(row["problem_id"]),
                title=str(row.get("title_main") or ""),
                difficulty=str(row.get("difficulty") or ""),
                source_url=row.get("source_url"),
                estimated_minutes=int(row.get("estimated_minutes") or 30),
                tags=tag_names,
            )

        matched_tag = row.get("matched_tag") or (tag_names[0] if tag_names else None)
        forgetting_score = -1.0
        last_practice_at = None
        if matched_tag:
            skill_state = skill_by_tag.get(canonicalize_tag_name(str(matched_tag)))
            if skill_state:
                forgetting_score = _as_float(skill_state.get("forgetting_score"), -1.0)
                last_practice = skill_state.get("last_practice_at")
                last_practice_at = str(last_practice) if last_practice else None

        items.append(RecommendItemResponse(
            rank_no=int(row.get("rank_no") or 0),
            problem_id=int(row["problem_id"]),
            score_total=_as_float(row.get("score_total")),
            score_need_match=_as_float(row.get("score_need_match")),
            score_difficulty_fit=_as_float(row.get("score_difficulty_fit")),
            score_success_prob=_as_float(row.get("score_success_prob")),
            score_novelty=_as_float(row.get("score_novelty")),
            score_quality=_as_float(row.get("score_quality")),
            score_semantic=_as_float(row.get("score_semantic")),
            reason_text=str(row.get("reason_text") or ""),
            matched_tag=str(matched_tag) if matched_tag else None,
            recall_source=row.get("recall_source"),
            problem=problem_info,
            forgetting_score=forgetting_score,
            last_practice_at=last_practice_at,
        ))
    return ResultResponse(**common, items=items)


@router.post("/generate")
def generate_recommendation(request: GenerateRequest, background_tasks: BackgroundTasks):
    """
    生成个性化推荐（异步，返回 requestId 供轮询）。
    """
    try:
        request_id = recommendation_service.create_recommendation_request(
            student_id=request.student_id,
            limit=request.limit,
            scene=request.scene,
        )
        background_tasks.add_task(
            recommendation_service.process_recommendation,
            request_id,
            request.student_id,
            request.limit,
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

        raw_items = db_mod.find_recommend_items(requestId) if req.get("status") == "completed" else []
        return api_success(_build_result(req, raw_items).model_dump(by_alias=True))
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
        if not req:
            raise ApiError(404, f"Request {request.request_id} not found")
        student_id = req["student_id"]

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
            scene=request.scene,
        )
        # 立即查询结果（generate 内部已同步完成）
        req = db_mod.get_request(request_id)
        if req is None:
            raise ApiError(500, "Recommendation request was not persisted")
        raw_items = db_mod.find_recommend_items(request_id) if req.get("status") == "completed" else []
        return api_success(_build_result(req, raw_items).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Sync recommendation failed: %s", e, exc_info=True)
        raise ApiError(500, f"Sync recommendation failed: {e}") from e


@router.get("/pta-errors")
def get_pta_high_frequency_errors(
    student_no: str = Query(..., min_length=1),
    min_errors: int = 5,
    class_id: int | None = Query(None, ge=1),
):
    """返回某个学生 PTA 累计错误 ≥ min_errors 的高频错题列表。

    本接口按学号（student_no）查询，内部自动解析为 student_profile.id 后查询提交记录。
    当提供 class_id 时，只返回该班级下 offering 的错题，避免跨课程数据泄漏。
    调用方（Java 网关）应从登录会话获取当前学生的学号，不得由学生客户端指定他人学号。
    """
    try:
        from app.services.wrong_question_features import load_pta_error_context_by_no
        ctx = load_pta_error_context_by_no(student_no, min_errors, class_id=class_id)
        return api_success({
            "student_no": student_no,
            "min_errors": min_errors,
            "total_count": len(ctx.get("pta_items", [])),
            "items": ctx.get("pta_items", []),
        })
    except ApiError:
        raise
    except Exception as e:
        logger.error("PTA error query failed: %s", e, exc_info=True)
        raise ApiError(500, f"PTA error query failed: {e}") from e
