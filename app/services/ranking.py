"""
六因子排序引擎 — 加权打分 + MMR 多样性重排

排序公式:
score = w1·need_match + w2·difficulty_fit + w3·P_success + w4·novelty
      + w5·quality - w6·repeat_penalty

匹配机制升级 (方案 A):
  - 旧版的 _TAG_MAP 和 _COURSE_WEIGHTS 改为从 knowledge_tags 模块取(50+ 项)
  - 文本回退用 tag_relevance_score 打分,带相关性加权,而不是 bool 命中
"""
from __future__ import annotations

import logging
import math
from typing import Any

from app.services.knowledge_tags import (
    get_course_weight,
    tag_relevance_score,
)

logger = logging.getLogger(__name__)


def _num(value: Any, default: float = 0.0) -> float:
    """把 DB 返回的 Decimal/int/float/None 统一转 float。

    MySQL 的 DECIMAL 列在 PyMySQL 里返回 decimal.Decimal,
    直接和 Python float 做运算会抛 TypeError,所以必须显式转。
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_text(problem: dict[str, Any]) -> str:
    title = problem.get("title_main", "") or ""
    text = problem.get("problem_text", "") or ""
    solution = problem.get("solution_text", "") or ""
    return (title + " " + text + " " + solution).lower()


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
    return 0.75 * (1.0 - mastery_norm) + 0.10 * forgetting_norm + 0.15 * get_course_weight(tag)


def compute_need_match(
    problem: dict[str, Any],
    skill_profile: list[dict[str, Any]],
    problem_tags: list[dict[str, Any]] | None = None,
) -> float:
    """
    题目匹配度 = Σ need(tag_i)·r_i / Σ r_i

    r_i = tag 表里的 relevance_score(方案 A 之后是真实相关度,不再是固定 1.0)。
    tag 表缺失时回退到 knowledge_tags.tag_relevance_score 在线打分。
    """
    title = problem.get("title_main", "") or ""
    body = (problem.get("problem_text", "") or "") + " " + (problem.get("solution_text", "") or "")
    profile_map = {s.get("tag_name"): s for s in skill_profile}

    total_need = 0.0
    total_relevance = 0.0

    # 第一优先级:tag 表数据 (relevance_score 已经是方案 A 离线打标后的真实分数)
    if problem_tags:
        for pt in problem_tags:
            tag = pt.get("tag_name", "")
            relevance = _num(pt.get("relevance_score"), 0.0)
            state = profile_map.get(tag)
            if state and relevance > 0:
                m_norm = _num(state.get("mastery_score"), 50.0) / 100.0
                f_norm = _num(state.get("forgetting_score"), 0.0) / 100.0
                total_need += compute_need(m_norm, f_norm, tag) * relevance
                total_relevance += relevance

    # 第二优先级:tag 表缺失或全 0,在线打分
    if total_relevance < 0.01:
        for state in skill_profile:
            tag = state.get("tag_name", "")
            if not tag:
                continue
            relevance = tag_relevance_score(title, body, tag)
            if relevance >= 0.5:
                m_norm = _num(state.get("mastery_score"), 50.0) / 100.0
                f_norm = _num(state.get("forgetting_score"), 0.0) / 100.0
                total_need += compute_need(m_norm, f_norm, tag) * relevance
                total_relevance += relevance

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


def _profile_tag_weights(
    problem: dict[str, Any],
    skill_profile: list[dict[str, Any]],
    problem_tags: list[dict[str, Any]] | None,
) -> list[tuple[dict[str, Any], float]]:
    profile_map = {str(state.get("tag_name")): state for state in skill_profile if state.get("tag_name")}
    persisted = []
    for row in problem_tags or []:
        state = profile_map.get(str(row.get("tag_name") or ""))
        relevance = _num(row.get("relevance_score"), 0.0)
        if state and relevance > 0:
            persisted.append((state, relevance))
    if persisted:
        return persisted

    title = problem.get("title_main", "") or ""
    body = (problem.get("problem_text", "") or "") + " " + (problem.get("solution_text", "") or "")
    return [
        (state, relevance)
        for state in skill_profile
        if (tag := state.get("tag_name"))
        and (relevance := tag_relevance_score(title, body, str(tag))) >= 0.5
    ]


def compute_success_prob(
    problem: dict[str, Any],
    skill_profile: list[dict[str, Any]],
    problem_tags: list[dict[str, Any]] | None = None,
) -> float:
    """
    通过概率估计(按掌握度映射,无数据回退 0.60)。
    相关 tag 识别走 knowledge_tags.tag_relevance_score,带分数加权。
    """
    if not skill_profile:
        return 0.6

    weighted_mastery = 0.0
    total_w = 0.0
    for state, weight in _profile_tag_weights(problem, skill_profile, problem_tags):
        weighted_mastery += weight * (_num(state.get("mastery_score"), 50.0) / 100.0)
        total_w += weight

    if total_w < 0.01:
        return 0.6

    avg_mastery = weighted_mastery / total_w
    return min(0.95, avg_mastery * 0.85 + 0.15)


def compute_novelty(
    problem: dict[str, Any],
    skill_profile: list[dict[str, Any]],
    problem_tags: list[dict[str, Any]] | None = None,
) -> float:
    """新颖度: 练习次数越少越新颖。相关 tag 识别走 knowledge_tags.tag_relevance_score。"""
    weighted_attempts = 0.0
    total_w = 0.0
    for state, weight in _profile_tag_weights(problem, skill_profile, problem_tags):
        weighted_attempts += weight * _num(state.get("attempt_count"), 0.0)
        total_w += weight

    if total_w < 0.01:
        return 1.0
    avg = weighted_attempts / total_w
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
    pta_error_ctx: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    六因子加权排序 + 错题本加权 + PTA高频错题加权.

    Returns list of dicts with scores.
    """
    if not skill_profile:
        avg_mastery = 50.0
    else:
        avg_mastery = sum(_num(s.get("mastery_score"), 50.0) for s in skill_profile) / len(skill_profile)

    w_nm = weights.get("need_match", 0.40)
    w_df = weights.get("difficulty_fit", 0.20)
    w_sp = weights.get("success_prob", 0.15)
    w_nv = weights.get("novelty", 0.10)
    w_qa = weights.get("quality", 0.10)
    w_sem = weights.get("semantic", 0.10)
    w_rp = weights.get("repeat_penalty", 0.15)
    w_wq = weights.get("wrong_question", 0.10)
    w_pta = weights.get("pta_error", 0.12)

    from app.services.wrong_question_features import compute_wrong_question_boost, compute_pta_error_boost

    items = []
    for problem in problems:
        pid = problem.get("id")
        if pid is None:
            continue

        tags = (problem_tags_map or {}).get(pid)
        need_match = compute_need_match(problem, skill_profile, tags)
        diff_fit = compute_difficulty_fit(problem, avg_mastery)
        success_prob = compute_success_prob(problem, skill_profile, tags)
        novelty = compute_novelty(problem, skill_profile, tags)
        quality = float(problem.get("quality_score") or 0.8)
        semantic_score = max(0.0, min(1.0, _num(problem.get("_semantic_score"), 0.0)))

        wq_boost = compute_wrong_question_boost(tags, wrong_question_ctx)
        pta_boost = compute_pta_error_boost(tags, pta_error_ctx)

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

        total_weight = w_nm + w_df + w_sp + w_nv + w_qa + w_sem + w_wq + w_pta + w_rp
        total = (
            w_nm * need_match
            + w_df * diff_fit
            + w_sp * success_prob
            + w_nv * novelty
            + w_qa * quality
            + w_sem * semantic_score
            + w_wq * wq_boost
            + w_pta * pta_boost
            - w_rp * repeat_penalty
        ) / total_weight
        total = total + min(positive_adj, 0.20)
        total = max(0.0, min(1.0, total))

        items.append({
            "problem": problem,
            "problem_id": pid,
            "score_total": round(total, 4),
            "score_need_match": round(need_match, 4),
            "score_difficulty_fit": round(diff_fit, 4),
            "score_success_prob": round(success_prob, 4),
            "score_novelty": round(novelty, 4),
            "score_quality": round(quality, 4),
            "score_semantic": round(semantic_score, 4),
            "score_wrong_question": round(wq_boost, 4),
            "score_pta_error": round(pta_boost, 4),
            "repeat_penalty": round(repeat_penalty, 4),
            "matched_tag": problem.get("_matched_tag"),
            "recall_sources": list(problem.get("_recall_sources") or []),
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
        same_as_prev = bool(tags & prev_tags)

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
            item["diversity_relaxed"] = True
            result.append(item)
            used_ids.add(item["problem_id"])

    return result[:limit]


def generate_reason_text(
    problem: dict[str, Any],
    need_match: float,
    skill_profile: list[dict[str, Any]],
    problem_tags: list[dict[str, Any]] | None = None,
) -> str:
    """
    生成可解释推荐理由。

    升级后能说清楚三件事(答辩弹药):
      1. 这题考的是什么知识点(主标签 + 相关度)
      2. 为什么推给你(你在该知识点掌握度低)
      3. 难度是否合适(目标难度 vs 题目难度)
    """
    parts: list[str] = []

    title = problem.get("title_main", "") or ""
    body = (problem.get("problem_text", "") or "") + " " + (problem.get("solution_text", "") or "")

    # ---- Step 1: 找主标签 + 相关度 ----
    primary_tag = None
    primary_relevance = 0.0
    if problem_tags:
        # 按 relevance_score 降序、优先 is_primary
        sorted_tags = sorted(
            problem_tags,
            key=lambda t: (int(t.get("is_primary") or 0), float(t.get("relevance_score") or 0)),
            reverse=True,
        )
        if sorted_tags:
            primary_tag = sorted_tags[0].get("tag_name")
            primary_relevance = float(sorted_tags[0].get("relevance_score") or 0)

    # 如果 tag 表没数据,在线算一遍主标签
    if not primary_tag:
        from app.services.knowledge_tags import detect_tags_for_problem
        detected = detect_tags_for_problem(title, body, min_score=0.5, max_tags=1)
        if detected:
            primary_tag, primary_relevance, _ = detected[0]

    # ---- Step 2: 该标签在学生画像里的掌握度 ----
    profile_map = {s.get("tag_name"): s for s in skill_profile}
    state = profile_map.get(primary_tag) if primary_tag else None

    # ---- Step 3: 组装理由 ----
    if primary_tag and primary_relevance >= 0.5:
        tag_part = f"本题主要考查「{primary_tag}」"
        if primary_relevance < 1.0:
            tag_part += f"(相关度 {primary_relevance:.2f})"
        parts.append(tag_part + "。")
    elif primary_tag:
        parts.append(f"本题涉及「{primary_tag}」。")

    if state and primary_tag:
        mastery = _num(state.get("mastery_score"), 50.0)
        attempts = int(_num(state.get("attempt_count"), 0))
        if mastery < 40:
            parts.append(f"你在该知识点掌握度 {mastery:.1f}%,属于薄弱项,建议优先突破。")
        elif mastery < 70:
            parts.append(f"你在该知识点掌握度 {mastery:.1f}%,仍需巩固。")
        else:
            parts.append(f"你在该知识点掌握度 {mastery:.1f}%,可作为提升训练。")
        if attempts > 0:
            parts.append(f"历史练习 {attempts} 次。")

    # 难度说明
    difficulty = (problem.get("difficulty") or "Medium").lower()
    if difficulty == "easy":
        parts.append("难度较低,适合基础巩固。")
    elif difficulty == "medium":
        parts.append("中等难度,适合能力提升。")
    elif difficulty == "hard":
        parts.append("高难度,有助于突破瓶颈。")

    est = problem.get("estimated_minutes")
    if est:
        parts.append(f"预计用时 {est} 分钟。")

    return "".join(parts) if parts else "系统根据你的画像推荐的优质题目,适合当前学习阶段。"
