"""
推荐流水线协调器 — 6步推荐流水线的入口服务

Step 1: 画像快照
Step 2: 反馈上下文
Step 3: 多路召回
Step 4: 排序打分
Step 5: 多样性重排
Step 6: 理由生成 + 持久化
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from app import db as db_mod
from app.core.config import get_settings
from .feedback import build_feedback_context, record_feedback as _record_feedback
from .ranking import (
    diversity_rerank,
    generate_reason_text,
    rank_and_score,
)
from .recall import collect_candidates
from .unified_state import UnifiedStateEngine

logger = logging.getLogger(__name__)

# 缓存引擎实例
_engine: UnifiedStateEngine | None = None


def get_engine() -> UnifiedStateEngine:
    """获取统一知识状态引擎单例（可供其他模块调用）。"""
    global _engine
    if _engine is None:
        s = get_settings()
        from .bkt import BKTEngine
        from .ebbinghaus import EbbinghausEngine
        from .wilson import WilsonEngine

        _engine = UnifiedStateEngine(
            bkt=BKTEngine(s.bkt_p_transfer, s.bkt_p_guess, s.bkt_p_slip, s.bkt_p_initial),
            ebbinghaus=EbbinghausEngine(
                s.ebbinghaus_s_base, s.ebbinghaus_s_max, s.ebbinghaus_s_min,
                s.ebbinghaus_alpha, s.ebbinghaus_beta, s.ebbinghaus_lambda,
                s.ebbinghaus_delta, s.ebbinghaus_practice_reduction,
            ),
            wilson=WilsonEngine(s.wilson_z),
        )
    return _engine


def _get_weights() -> dict[str, float]:
    s = get_settings()
    return {
        "need_match": s.weight_need_match,
        "difficulty_fit": s.weight_difficulty_fit,
        "success_prob": s.weight_success_prob,
        "novelty": s.weight_novelty,
        "quality": s.weight_quality,
        "repeat_penalty": s.weight_repeat_penalty,
    }


def generate_recommendation(student_id: int, limit: int = 20, scene: str = "default") -> str:
    """
    异步生成推荐（在当前实现中同步执行，返回 request_id）。

    Returns
    -------
    str
        request_id
    """
    request_id = str(uuid.uuid4())

    # 创建请求记录
    db_mod.create_request(request_id, student_id, scene, limit)

    try:
        items = _generate_recommendation_sync(student_id, limit, request_id)
        if not items:
            db_mod.fail_request(request_id, "no recommendation generated")
        else:
            db_mod.complete_request(request_id)
    except Exception as exc:
        db_mod.fail_request(request_id, str(exc))
        logger.error("Recommendation failed: request_id=%s student_id=%s: %s", request_id, student_id, exc)

    return request_id


def _generate_recommendation_sync(student_id: int, limit: int, request_id: str) -> list[dict[str, Any]]:
    """完整的6步推荐流水线。"""
    settings = get_settings()

    # Step 1: 画像快照
    skill_profile = db_mod.find_all_skill_states(student_id)
    if not skill_profile:
        logger.info("Student %s has no skill profile, using fallback", student_id)
        return _fallback_recommendations(student_id, limit, request_id)

    # Step 2: 反馈上下文
    feedback_ctx = build_feedback_context(student_id)

    # Step 3: 多路召回
    candidates = collect_candidates(
        skill_profile,
        feedback_ctx,
        limit,
        weak_ratio=settings.recall_weak_ratio,
        diff_ratio=settings.recall_difficulty_ratio,
        explore_ratio=settings.recall_exploration_ratio,
    )

    if not candidates:
        return _fallback_recommendations(student_id, limit, request_id)

    # 预加载题目标签
    problem_ids = list(candidates.keys())
    problem_tags_map: dict[int, list[dict[str, Any]]] = {}
    for pid in problem_ids[:100]:
        try:
            problem_tags_map[pid] = db_mod.find_tags_for_problem(pid)
        except Exception:
            problem_tags_map[pid] = []

    # Step 4: 排序打分
    weights = _get_weights()
    ranked = rank_and_score(
        list(candidates.values()),
        skill_profile,
        feedback_ctx.get("score_adjustments"),
        weights,
        problem_tags_map,
    )

    # Step 5: 多样性重排
    reranked = diversity_rerank(
        ranked, limit, problem_tags_map,
        max_tag_ratio=settings.diversity_max_tag_ratio,
    )

    # Step 6: 理由生成 + 持久化
    items = []
    for i, scored in enumerate(reranked):
        problem = scored["problem"]
        reason = generate_reason_text(problem, scored["score_need_match"], skill_profile)

        item = {
            "request_id": request_id,
            "student_id": student_id,
            "rank_no": i + 1,
            "problem_id": scored["problem_id"],
            "score_total": scored["score_total"],
            "score_need_match": scored["score_need_match"],
            "score_difficulty_fit": scored["score_difficulty_fit"],
            "score_success_prob": scored["score_success_prob"],
            "score_novelty": scored["score_novelty"],
            "score_quality": scored["score_quality"],
            "reason_text": reason,
            "reason_json": None,
        }
        items.append(item)

    # 批量入库
    db_mod.insert_recommend_items(items)
    return items


def _fallback_recommendations(student_id: int, limit: int, request_id: str) -> list[dict[str, Any]]:
    """兜底推荐：按质量分排序取前N题。"""
    logger.warning("Using fallback recommendations for student_id=%s", student_id)
    problems = db_mod.find_problems_page(0, limit)

    items = []
    for i, problem in enumerate(problems):
        item = {
            "request_id": request_id,
            "student_id": student_id,
            "rank_no": i + 1,
            "problem_id": problem["id"],
            "score_total": 0.6,
            "score_need_match": 0.5,
            "score_difficulty_fit": 0.6,
            "score_success_prob": 0.6,
            "score_novelty": 0.7,
            "score_quality": 0.8,
            "reason_text": "系统推荐的优质题目，适合当前学习阶段。",
            "reason_json": None,
        }
        items.append(item)

    if items:
        db_mod.insert_recommend_items(items)
        db_mod.complete_request(request_id)
    return items


# ──────────────────────────────────────────
# 画像更新（暴露给 API 层）
# ──────────────────────────────────────────

def update_skill_after_practice(
    student_id: int,
    tag_name: str,
    is_correct: bool,
    practice_attempt_count: int = 1,
) -> dict[str, Any]:
    """
    练习后更新画像（完整 BKT + 遗忘 + Wilson 流程）。
    """
    engine = get_engine()

    state = db_mod.find_skill_state(student_id, tag_name)
    if state is None:
        s = get_settings()
        state = {
            "student_id": student_id,
            "tag_name": tag_name,
            "mastery_score": s.bkt_p_initial * 100,  # P(L₀) = 0.30 → 30
            "forgetting_score": 0.0,
            "confidence_score": 0.0,
            "attempt_count": 0,
            "success_count": 0,
            "avg_attempts_to_success": None,
            "last_practice_at": None,
        }

    result = engine.update_after_practice(
        mastery_score=state["mastery_score"],
        forgetting_score=state["forgetting_score"],
        success_count=state["success_count"],
        attempt_count=state["attempt_count"],
        avg_attempts_to_success=state.get("avg_attempts_to_success"),
        is_correct=is_correct,
        practice_attempt_count=practice_attempt_count,
    )

    # 更新平均尝试次数
    new_success = result["success_count"]
    new_attempt = result["attempt_count"]
    avg_a = (new_attempt / new_success) if new_success > 0 else None

    update_state = {
        "student_id": student_id,
        "tag_name": tag_name,
        "mastery_score": result["mastery_score"],
        "forgetting_score": result["forgetting_score"],
        "confidence_score": result["confidence_score"],
        "attempt_count": new_attempt,
        "success_count": new_success,
        "avg_attempts_to_success": avg_a,
        "last_practice_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    db_mod.upsert_skill_state(update_state)
    return update_state


def batch_update_skills(student_id: int, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """批量更新多个技能标签。"""
    results = []
    for u in updates:
        r = update_skill_after_practice(
            student_id=student_id,
            tag_name=u["tag_name"],
            is_correct=u["is_correct"],
            practice_attempt_count=u.get("attempt_count", 1),
        )
        results.append(r)
    return results


def decay_all_skills(days_threshold: int = 1) -> int:
    """
    定时衰减任务：对所有超过 days_threshold 天没练习的技能进行非累积遗忘衰减。
    """
    engine = get_engine()
    skills = db_mod.find_skills_needing_decay(days_threshold)
    updated = 0

    for state in skills:
        last = state.get("last_practice_at")
        if not last:
            continue
        if isinstance(last, str):
            last = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        days_since = (datetime.now() - last).days
        if days_since <= 0:
            continue

        result = engine.decay_forgetting(
            mastery_score=state["mastery_score"],
            forgetting_score=state["forgetting_score"],
            success_count=state["success_count"],
            avg_attempts_to_success=state.get("avg_attempts_to_success"),
            days_since_last=float(days_since),
        )

        db_mod.update_skill_scores(
            state["student_id"],
            state["tag_name"],
            result["mastery_score"],
            result["forgetting_score"],
            state.get("confidence_score", 0),
        )
        updated += 1

    logger.info("Decayed %d skills (threshold=%d days)", updated, days_threshold)
    return updated
