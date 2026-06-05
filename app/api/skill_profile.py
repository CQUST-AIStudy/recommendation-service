from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.responses import ApiError, api_success
from app.schemas.profile import (
    BatchSkillUpdateRequest,
    DecayRequest,
    DecayResponse,
    InitializeRequest,
    InitializeResponse,
    SkillStateResponse,
    SkillUpdateRequest,
)
from app import db as db_mod
from app.services import recommendation_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/profile", tags=["skill-profile"])


@router.post("/update")
def update_skill(request: SkillUpdateRequest):
    """练习后更新单个技能画像（BKT + 遗忘 + Wilson）。"""
    try:
        result = recommendation_service.update_skill_after_practice(
            student_id=request.student_id,
            tag_name=request.tag_name,
            is_correct=request.is_correct,
            practice_attempt_count=request.attempt_count,
        )
        return api_success(SkillStateResponse(
            student_id=result["student_id"],
            tag_name=result["tag_name"],
            mastery_score=result["mastery_score"],
            forgetting_score=result["forgetting_score"],
            confidence_score=result["confidence_score"],
            attempt_count=result["attempt_count"],
            success_count=result["success_count"],
        ).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Profile update failed: %s", e, exc_info=True)
        raise ApiError(500, f"Profile update failed: {e}") from e


@router.post("/batch-update")
def batch_update_skills(request: BatchSkillUpdateRequest):
    """批量更新多个技能画像。"""
    try:
        updates = [
            {"tag_name": u.tag_name, "is_correct": u.is_correct, "attempt_count": u.attempt_count}
            for u in request.updates
        ]
        results = recommendation_service.batch_update_skills(request.student_id, updates)
        return api_success([
            SkillStateResponse(
                student_id=r["student_id"],
                tag_name=r["tag_name"],
                mastery_score=r["mastery_score"],
                forgetting_score=r["forgetting_score"],
                confidence_score=r["confidence_score"],
                attempt_count=r["attempt_count"],
                success_count=r["success_count"],
            ).model_dump(by_alias=True)
            for r in results
        ])
    except ApiError:
        raise
    except Exception as e:
        logger.error("Batch profile update failed: %s", e, exc_info=True)
        raise ApiError(500, f"Batch update failed: {e}") from e


@router.post("/decay")
def decay_skills(request: DecayRequest):
    """定时遗忘衰减任务（非累积衰减）。"""
    try:
        count = recommendation_service.decay_all_skills(days_threshold=request.days_threshold)
        return api_success(DecayResponse(updated_count=count).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Decay failed: %s", e, exc_info=True)
        raise ApiError(500, f"Decay failed: {e}") from e


@router.get("/{studentId}")
def get_skill_profile(studentId: int):
    """获取学生完整技能画像。"""
    try:
        states = db_mod.find_all_skill_states(studentId)
        return api_success([
            SkillStateResponse(
                student_id=s["student_id"],
                tag_name=s["tag_name"],
                mastery_score=float(s["mastery_score"]),
                forgetting_score=float(s["forgetting_score"]),
                confidence_score=float(s["confidence_score"]),
                attempt_count=s["attempt_count"],
                success_count=s["success_count"],
            ).model_dump(by_alias=True)
            for s in states
        ])
    except ApiError:
        raise
    except Exception as e:
        logger.error("Get profile failed: %s", e, exc_info=True)
        raise ApiError(500, f"Get profile failed: {e}") from e


@router.post("/initialize")
def initialize_skill_profile(request: InitializeRequest):
    """初始化学生技能画像（为新学生创建默认技能状态）。"""
    try:
        from app.core.config import get_settings
        settings = get_settings()
        count = 0
        for tag_name in request.tag_names:
            existing = db_mod.find_skill_state(request.student_id, tag_name)
            if existing is None:
                state = {
                    "student_id": request.student_id,
                    "tag_name": tag_name,
                    "mastery_score": settings.bkt_p_initial * 100,
                    "forgetting_score": 0.0,
                    "confidence_score": 0.0,
                    "attempt_count": 0,
                    "success_count": 0,
                    "avg_attempts_to_success": None,
                    "last_practice_at": None,
                }
                db_mod.upsert_skill_state(state)
                count += 1
        logger.info("Initialized %d skills for student %d", count, request.student_id)
        return api_success(InitializeResponse(
            student_id=request.student_id,
            initialized_count=count,
        ).model_dump(by_alias=True))
    except ApiError:
        raise
    except Exception as e:
        logger.error("Initialize profile failed: %s", e, exc_info=True)
        raise ApiError(500, f"Initialize profile failed: {e}") from e
