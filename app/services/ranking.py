"""
六因子排序引擎 — 加权打分 + MMR 多样性重排

排序公式:
score = w1·need_match + w2·difficulty_fit + w3·P_success + w4·novelty
      + w5·quality - w6·repeat_penalty
"""
from __future__ import annotations

import logging
import math
import random
from typing import Any

logger = logging.getLogger(__name__)

# 中英文标签映射（同 recall.py）
_TAG_MAP: dict[str, str] = {
    "动态规划": "dynamic programming", "贪心": "greedy", "回溯": "backtrack",
    "图": "graph", "树": "tree", "堆": "heap", "并查集": "union find",
    "位运算": "bit manipulation", "数组": "array", "字符串": "string",
    "链表": "linked list", "哈希表": "hash", "栈": "stack", "队列": "queue",
    "排序": "sort", "二分查找": "binary search",
}

# 课程重点权重 w_course（高权重 = 课程重点知识点）
_COURSE_WEIGHTS: dict[str, float] = {
    "数组": 0.8, "链表": 0.9, "栈": 0.8, "队列": 0.8,
    "树": 0.9, "图": 0.7, "字符串": 0.7, "动态规划": 0.6,
    "贪心": 0.6, "回溯": 0.6, "排序": 0.8, "二分查找": 0.7,
    "哈希表": 0.7, "堆": 0.6, "并查集": 0.4, "位运算": 0.4,
}


def _get_course_weight(tag: str) -> float:
    return _COURSE_WEIGHTS.get(tag, 0.5)


def _build_text(problem: dict[str, Any]) -> str:
    title = problem.get("title_main", "") or ""
    text = problem.get("problem_text", "") or ""
    solution = problem.get("solution_text", "") or ""
    return (title + " " + text + " " + solution).lower()


def _get_english_tag(chinese: str) -> str:
    return _TAG_MAP.get(chinese, chinese)


# ──────────────────────────────────────────
# 需求度函数
# ──────────────────────────────────────────

def compute_need(mastery_norm: float, forgetting_norm: float, tag: str) -> float:
    """
    need(tag) = 0.75·(1 - m_norm) + 0.10·f_norm + 0.15·w_course

    Parameters
    ----------
    mastery_norm : float
        掌握度归一化 [0, 1]。
    forgetting_norm : float
        遗忘度归一化 [0, 1]。
    tag : str
        技能标签名。
    """
    return 0.75 * (1.0 - mastery_norm) + 0.10 * forgetting_norm + 0.15 * _get_course_weight(tag)


def compute_need_match(
    problem: dict[str, Any],
    skill_profile: list[dict[str, Any]],
    problem_tags: list[dict[str, Any]] | None = None,
) -> float:
    """
    题目匹配度 = Σ need(tag_i)·r_i / Σ r_i
    """
    ptext = _build_text(problem)
    profile_map = {s.get("tag_name"): s for s in skill_profile}

    total_need = 0.0
    total_relevance = 0.0

    # 先用 tag 表数据
    if problem_tags:
        for pt in problem_tags:
            tag = pt.get("tag_name", "")
            relevance = pt.get("relevance_score", 1.0) or 1.0
            state = profile_map.get(tag)
            if state:
                m_norm = (state.get("mastery_score") or 50) / 100.0
                f_norm = (state.get("forgetting_score") or 0) / 100.0
                total_need += compute_need(m_norm, f_norm, tag) * relevance
                total_relevance += relevance

    # 如果 tag 表没数据，回退文本匹配
    if total_relevance < 0.01:
        for state in skill_profile:
            tag = state.get("tag_name", "")
            if not tag:
                continue
            tag_lower = tag.lower()
            eng = _get_english_tag(tag).lower()
            if (tag_lower and tag_lower in ptext) or (eng and eng in ptext):
                m_norm = (state.get("mastery_score") or 50) / 100.0
                f_norm = (state.get("forgetting_score") or 0) / 100.0
                total_need += compute_need(m_norm, f_norm, tag)
                total_relevance += 1.0

    if total_relevance < 0.01:
        return 0.3  # 无匹配时给默认值

    return total_need / total_relevance


def compute_difficulty_fit(problem: dict[str, Any], avg_mastery: float) -> float:
    """
    难度匹配度: d_target = (m_avg/100)×2 + 1
    difficulty_fit = e^(-|d_target - d_problem|)
    """
    d_target = (avg_mastery / 100.0) * 2.0 + 1.0  # Easy=1, Medium=2, Hard=3
    difficulty = (problem.get("difficulty") or "Medium").lower()
    d_problem = {"easy": 1.0, "medium": 2.0, "hard": 3.0}.get(difficulty, 2.0)
    return math.exp(-abs(d_target - d_problem))


def compute_success_prob(problem: dict[str, Any], skill_profile: list[dict[str, Any]]) -> float:
    """
    通过概率估计（按掌握度映射，无数据回退 0.60）。
    """
    if not skill_profile:
        return 0.6

    ptext = _build_text(problem)
    profile_map = {s.get("tag_name"): s for s in skill_profile}

    related_mastery = []
    for state in skill_profile:
        tag = state.get("tag_name", "")
        tag_lower = tag.lower()
        eng = _get_english_tag(tag).lower()
        if (tag_lower and tag_lower in ptext) or (eng and eng in ptext):
            related_mastery.append((state.get("mastery_score") or 50) / 100.0)

    if not related_mastery:
        return 0.6

    avg_mastery = sum(related_mastery) / len(related_mastery)
    # 简单映射: 掌握度 -> 通过概率
    return min(0.95, avg_mastery * 0.85 + 0.15)


def compute_novelty(problem: dict[str, Any], skill_profile: list[dict[str, Any]]) -> float:
    """新颖度: 练习次数越少越新颖。"""
    ptext = _build_text(problem)
    total_attempts = 0
    matched = 0

    for state in skill_profile:
        tag = state.get("tag_name", "")
        tag_lower = tag.lower()
        eng = _get_english_tag(tag).lower()
        if (tag_lower and tag_lower in ptext) or (eng and eng in ptext):
            total_attempts += state.get("attempt_count") or 0
            matched += 1

    if matched == 0:
        return 1.0
    avg = total_attempts / matched
    return max(0.1, 1.0 - avg / 10.0)


# ──────────────────────────────────────────
# 六因子排序
# ──────────────────────────────────────────

def rank_and_score(
    problems: list[dict[str, Any]],
    skill_profile: list[dict[str, Any]],
    feedback_adjustments: dict[int, float] | None,
    weights: dict[str, float],
    problem_tags_map: dict[int, list[dict[str, Any]]] | None = None,
    wrong_question_ctx: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    六因子加权排序 + 错题本加权 (seventh factor).

    Returns list of dicts with scores.
    """
    if not skill_profile:
        avg_mastery = 50.0
    else:
        avg_mastery = sum(s.get("mastery_score") or 50 for s in skill_profile) / len(skill_profile)

    w_nm = weights.get("need_match", 0.40)
    w_df = weights.get("difficulty_fit", 0.20)
    w_sp = weights.get("success_prob", 0.15)
    w_nv = weights.get("novelty", 0.10)
    w_qa = weights.get("quality", 0.10)
    w_rp = weights.get("repeat_penalty", 0.15)
    w_wq = weights.get("wrong_question", 0.10)

    from app.services.wrong_question_features import compute_wrong_question_boost

    items = []
    for problem in problems:
        pid = problem.get("id")
        if pid is None:
            continue

        tags = (problem_tags_map or {}).get(pid)
        need_match = compute_need_match(problem, skill_profile, tags)
        diff_fit = compute_difficulty_fit(problem, avg_mastery)
        success_prob = compute_success_prob(problem, skill_profile)
        novelty = compute_novelty(problem, skill_profile)
        quality = float(problem.get("quality_score") or 0.8)

        wq_boost = compute_wrong_question_boost(tags, wrong_question_ctx)

        total = (
            w_nm * need_match
            + w_df * diff_fit
            + w_sp * success_prob
            + w_nv * novelty
            + w_qa * quality
            + w_wq * wq_boost
        )

        # repeat_penalty: 独立减分项，与设计文档公式一致
        # score = w1·need + w2·diff + w3·P_success + w4·novelty + w5·q + w7·wq_boost - w6·repeat_penalty
        repeat_penalty = 0.0
        positive_adj = 0.0
        if feedback_adjustments and pid in feedback_adjustments:
            adj = feedback_adjustments[pid]
            if adj < 0:
                # 负值视为重复惩罚
                repeat_penalty = min(abs(adj), 0.55)
            else:
                # 正值视为额外加分（如点击/开始行为）
                positive_adj = adj

        total = total - w_rp * repeat_penalty + positive_adj
        total = max(0.0, min(1.2, total))

        items.append({
            "problem": problem,
            "problem_id": pid,
            "score_total": round(total, 4),
            "score_need_match": round(need_match, 4),
            "score_difficulty_fit": round(diff_fit, 4),
            "score_success_prob": round(success_prob, 4),
            "score_novelty": round(novelty, 4),
            "score_quality": round(quality, 4),
            "score_wrong_question": round(wq_boost, 4),
            "repeat_penalty": round(repeat_penalty, 4),
        })

    items.sort(key=lambda x: x["score_total"], reverse=True)
    return items


# ──────────────────────────────────────────
# MMR 多样性重排
# ──────────────────────────────────────────

def diversity_rerank(
    items: list[dict[str, Any]],
    limit: int,
    problem_tags_map: dict[int, list[dict[str, Any]]] | None = None,
    max_tag_ratio: float = 0.40,
) -> list[dict[str, Any]]:
    """
    多样性重排（MMR 思想）:
    - 单标签在 Top N 中占比 <= max_tag_ratio
    - 相邻 2 题不能完全同标签
    - 第一轮贪心选取后不足则回填
    """
    if len(items) <= limit:
        return items

    def _get_tags(problem_id: int) -> set[str]:
        tags = set()
        if problem_tags_map and problem_id in problem_tags_map:
            for pt in problem_tags_map[problem_id]:
                t = pt.get("tag_name")
                if t:
                    tags.add(t)
        return tags

    max_per_tag = max(1, int(limit * max_tag_ratio))
    tag_counts: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    used_ids: set[int] = set()
    prev_tags: set[str] = set()

    # 第一轮: 贪心选取
    for item in items:
        if len(result) >= limit:
            break
        pid = item["problem_id"]
        if pid in used_ids:
            continue

        tags = _get_tags(pid)
        # 检查单标签上限
        dominated = any(tag_counts.get(t, 0) >= max_per_tag for t in tags)
        # 检查相邻标签
        same_as_prev = tags == prev_tags and len(tags) > 0

        if not dominated and not same_as_prev:
            result.append(item)
            used_ids.add(pid)
            for t in tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            prev_tags = tags

    # 第二轮: 放宽约束回填
    for item in items:
        if len(result) >= limit:
            break
        if item["problem_id"] not in used_ids:
            result.append(item)
            used_ids.add(item["problem_id"])

    return result[:limit]


def generate_reason_text(
    problem: dict[str, Any],
    need_match: float,
    skill_profile: list[dict[str, Any]],
) -> str:
    """生成可解释推荐理由。"""
    parts: list[str] = []

    # 找出最薄弱的匹配技能
    ptext = _build_text(problem)
    profile_map = {s.get("tag_name"): s for s in skill_profile}

    weakest_tag = None
    weakest_mastery = 100.0
    for state in skill_profile:
        tag = state.get("tag_name", "")
        if not tag:
            continue
        tag_lower = tag.lower()
        eng = _get_english_tag(tag).lower()
        if (tag_lower and tag_lower in ptext) or (eng and eng in ptext):
            m = state.get("mastery_score") or 50
            if m < weakest_mastery:
                weakest_mastery = m
                weakest_tag = tag

    if weakest_tag and need_match > 0.5:
        parts.append(f"针对你的薄弱技能「{weakest_tag}」（掌握度 {weakest_mastery:.1f}%）进行强化练习。")

    difficulty = (problem.get("difficulty") or "Medium").lower()
    if difficulty == "easy":
        parts.append("适合基础巩固，建议先掌握基本思路。")
    elif difficulty == "medium":
        parts.append("中等难度，适合提升解题能力。")
    elif difficulty == "hard":
        parts.append("高难度挑战，有助于突破技能瓶颈。")

    est = problem.get("estimated_minutes")
    if est:
        parts.append(f"预计用时 {est} 分钟。")

    return "".join(parts) if parts else "系统推荐的优质题目，适合当前学习阶段。"
