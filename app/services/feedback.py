"""
反馈上下文构建 — 从 leetcode_recommend_feedback 表读取历史交互，
构建分数调整映射、已完成/不喜欢题目集合。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app import db as db_mod
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 反馈动作分数增量（默认值，运行时从 Settings 覆盖）
_DEFAULT_DELTAS: dict[str, float] = {
    "exposure": -0.01,
    "click": 0.04,
    "start": 0.06,
    "complete": 0.10,   # 设计文档修正版: +0.10（已完成题会被过滤，不需要强惩罚）
    "skip": -0.12,
    "dislike": -0.35,
}

_ACTION_DELTAS: dict[str, float] | None = None


def _get_deltas() -> dict[str, float]:
    global _ACTION_DELTAS
    if _ACTION_DELTAS is None:
        s = get_settings()
        _ACTION_DELTAS = {
            "exposure": s.feedback_delta_exposure,
            "click": s.feedback_delta_click,
            "start": s.feedback_delta_start,
            "complete": s.feedback_delta_complete,
            "skip": s.feedback_delta_skip,
            "dislike": s.feedback_delta_dislike,
        }
    return _ACTION_DELTAS


_VALID_ACTIONS = set(_DEFAULT_DELTAS.keys())


def build_feedback_context(
    student_id: int,
    max_history: int | None = None,
) -> dict[str, Any]:
    """
    构建反馈上下文。

    Parameters
    ----------
    student_id : int
    max_history : int | None
        最多读取的反馈条数。

    Returns
    -------
    dict with keys:
        - score_adjustments: dict[int, float]  problem_id -> 分数调整量
        - completed_problem_ids: list[int]
        - disliked_problem_ids: list[int]
    """
    settings = get_settings()
    limit = max_history or settings.feedback_max_history

    try:
        feedback_list = db_mod.find_feedback_by_student(student_id, limit)
    except Exception as exc:
        logger.warning("Failed to load feedback for student %s: %s", student_id, exc)
        return {"score_adjustments": {}, "completed_problem_ids": [], "disliked_problem_ids": []}

    if not feedback_list:
        return {"score_adjustments": {}, "completed_problem_ids": [], "disliked_problem_ids": []}

    # 统计每题的各行为计数 + 分数调整
    counters: dict[int, dict[str, int]] = {}
    score_adj: dict[int, float] = {}
    recency_decay = settings.feedback_recency_decay
    recency_floor = settings.feedback_recency_floor

    for i, fb in enumerate(feedback_list):
        pid = fb.get("problem_id")
        if pid is None:
            continue
        action = (fb.get("action") or "").strip().lower()
        if action not in _VALID_ACTIONS:
            continue

        # 近因衰减因子
        recency_factor = max(recency_floor, 1.0 - i * recency_decay)
        delta = _get_deltas()[action] * recency_factor
        score_adj[pid] = score_adj.get(pid, 0.0) + delta

        # 行为计数
        c = counters.setdefault(pid, {"exposure": 0, "click": 0, "start": 0, "complete": 0, "skip": 0, "dislike": 0})
        c[action] += 1

    # 二次惩罚
    completed: list[int] = []
    disliked: list[int] = []

    for pid, c in counters.items():
        if c["complete"] > 0:
            completed.append(pid)
        if c["dislike"] > 0:
            disliked.append(pid)

        engaged = c["click"] + c["start"] + c["complete"]
        idle_exposure = max(0, c["exposure"] - engaged)
        if idle_exposure >= 2:
            score_adj[pid] -= min(0.25, (idle_exposure - 1) * 0.05)

        if c["skip"] > 1:
            score_adj[pid] -= min(0.20, (c["skip"] - 1) * 0.04)

    # 裁剪到 [-0.55, +0.25]
    score_adj = {k: max(-0.55, min(0.25, v)) for k, v in score_adj.items()}

    return {
        "score_adjustments": score_adj,
        "completed_problem_ids": completed,
        "disliked_problem_ids": disliked,
    }


def record_feedback(
    request_id: str,
    student_id: int,
    problem_id: int,
    action: str,
    session_id: str | None = None,
    extra_json: str | None = None,
) -> bool:
    """记录一条反馈行为。"""
    action = (action or "").strip().lower()
    if action not in _VALID_ACTIONS:
        logger.warning("Invalid feedback action: %s", action)
        return False

    fb = {
        "request_id": request_id,
        "student_id": student_id,
        "problem_id": problem_id,
        "session_id": session_id,
        "action": action,
        "action_at": datetime.now(),
        "extra_json": extra_json,
    }

    try:
        db_mod.insert_feedback(fb)
        return True
    except Exception as exc:
        logger.error("Failed to record feedback: %s", exc)
        return False
