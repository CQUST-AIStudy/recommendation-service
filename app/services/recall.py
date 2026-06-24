"""
多路召回引擎 — 四路召回策略

1. 弱项标签召回 (60%)
2. 难度进阶召回 (25%)
3. 探索召回 (15%)
4. 热门题召回 (5%)

匹配机制升级 (方案 A):
  - tag 表查询时同时跨三个 category (algorithm / data_structure / technique)
  - 文本回退不再是子串 `in`,而是用 knowledge_tags.tag_relevance_score 打分
    按 score >= 0.5 过滤、按 score 降序排序,确保召回质量。
"""
from __future__ import annotations

import logging
import random
from typing import Any

from app import db as db_mod
from app.services.knowledge_tags import tag_relevance_score

logger = logging.getLogger(__name__)


def _num(value: Any, default: float = 0.0) -> float:
    """DB Decimal/int/float → float(MySQL DECIMAL 列必须显式转才能和 float 运算)。"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# tag_category 枚举(对齐 DB schema)
_TAG_CATEGORIES = ("algorithm", "data_structure", "technique")

_EXPLORATION_TAGS = {"动态规划", "贪心", "回溯", "图", "树", "堆", "并查集", "位运算"}


def _build_text(problem: dict[str, Any]) -> str:
    title = problem.get("title_main", "") or ""
    text = problem.get("problem_text", "") or ""
    solution = problem.get("solution_text", "") or ""
    return (title + " " + text + " " + solution).lower()


def _matches_tag(problem: dict[str, Any], tag: str) -> bool:
    """旧接口保留:bool 命中判断。新逻辑用 tag_relevance_score >= 0.5 作为阈值。"""
    title = problem.get("title_main", "") or ""
    text = (problem.get("problem_text", "") or "") + " " + (problem.get("solution_text", "") or "")
    return tag_relevance_score(title, text, tag) >= 0.5


def _find_by_tags(tags: list[str], limit: int) -> list[dict[str, Any]]:
    """先按 tag 表查(跨三个 category),查不到回退到打分式文本匹配。"""
    if not tags:
        return []

    # 第一优先级:tag 表(SQL)。跨 category 查询避免漏掉数据结构题。
    tag_set = set(tags)
    for cat in _TAG_CATEGORIES:
        try:
            ids = db_mod.find_problem_ids_by_tags(cat, tags)
            if ids:
                problems = db_mod.find_problems_by_ids(ids[:limit * 3])
                seen: set[int] = set()
                result = []
                for p in problems:
                    pid = p["id"]
                    if pid not in seen:
                        seen.add(pid)
                        result.append(p)
                    if len(result) >= limit:
                        return result
                if result:
                    return result
        except Exception as exc:
            logger.warning("Tag DB lookup failed (cat=%s), will try text match: %s", cat, exc)

    # 第二优先级:打分式文本匹配(不再用朴素子串 in)
    all_problems = db_mod.find_all_problems(2000)
    scored: list[tuple[float, dict[str, Any]]] = []
    for p in all_problems:
        title = p.get("title_main", "") or ""
        text = (p.get("problem_text", "") or "") + " " + (p.get("solution_text", "") or "")
        best_score = 0.0
        for tag in tag_set:
            s = tag_relevance_score(title, text, tag)
            if s > best_score:
                best_score = s
        if best_score >= 0.5:
            scored.append((best_score, p))
        if len(scored) >= limit * 3:
            break

    # 按相关度降序,Top limit 题
    scored.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in scored[:limit]]


# ──────────────────────────────────────────
# 四路召回
# ──────────────────────────────────────────

def recall_by_weakness(skill_profile: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """
    弱项标签召回：找掌握度最低的5个标签，按标签查题。
    """
    weak = sorted(
        [s for s in skill_profile if _num(s.get("mastery_score"), 50.0) < 60],
        key=lambda s: _num(s.get("mastery_score"), 50.0),
    )[:5]
    tags = [s["tag_name"] for s in weak if s.get("tag_name")]

    if not tags:
        # 全部都 >= 60，则取练习次数最少的
        tags = [
            s["tag_name"]
            for s in sorted(skill_profile, key=lambda s: _num(s.get("attempt_count"), 0))[:3]
            if s.get("tag_name")
        ]

    return _find_by_tags(tags, limit)


def recall_by_difficulty(skill_profile: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """
    难度进阶召回：根据平均掌握度选目标难度。
    """
    if not skill_profile:
        return db_mod.find_problems_page(0, limit)

    avg = sum(_num(s.get("mastery_score"), 50.0) for s in skill_profile) / len(skill_profile)
    target = "Easy" if avg < 40 else ("Medium" if avg < 70 else "Hard")

    problems = db_mod.find_problems_by_difficulty(target, limit)
    if not problems:
        problems = db_mod.find_problems_page(0, limit)
    return problems[:limit]


def recall_by_exploration(skill_profile: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """
    探索召回：找练习次数<3的未探索标签。
    """
    profile_map = {s.get("tag_name"): s for s in skill_profile}
    unexplored = [
        tag for tag in _EXPLORATION_TAGS
        if tag not in profile_map or (profile_map[tag].get("attempt_count") or 0) < 3
    ]
    return _find_by_tags(unexplored, limit)


def recall_by_popularity(limit: int) -> list[dict[str, Any]]:
    """热门题召回：按 quality_score 排序。"""
    return db_mod.find_problems_page(0, limit)


def recall_by_wrong_question_signals(student_id: int | None, limit: int) -> list[dict[str, Any]]:
    """错题本召回:从学生未掌握错题的 tag 反向拉取同 tag 候选题.

    Falls back to [] if the student has no unresolved wrong questions.
    """
    if not student_id or limit <= 0:
        return []
    try:
        from app.services.wrong_question_features import load_wrong_question_context_by_id
        ctx = load_wrong_question_context_by_id(student_id)
        tags = list((ctx.get("unresolved_tag_counts") or {}).keys())
        if not tags:
            return []
        return _find_by_tags(tags, limit)
    except Exception as exc:
        logger.warning("recall_by_wrong_question_signals failed for student %s: %s", student_id, exc)
        return []


def recall_by_semantic(
    skill_profile: list[dict[str, Any]],
    limit: int,
    min_score: float = 0.30,
) -> list[dict[str, Any]]:
    """方案 C 第六路召回:用题向量做语义近邻检索。

    优雅降级:
      - sentence-transformers 没装 → 返回 []
      - embedding 表没数据 → 返回 []
      - 否则:对学生每个弱项 tag,从 tag 标注题聚合质心向量,
              在候选池里找余弦 >= min_score 的题,按相似度降序。

    Parameters
    ----------
    skill_profile : list[dict]
        学生技能画像。
    limit : int
        最多召回多少题。
    min_score : float
        余弦相似度阈值,默认 0.30。
    """
    if limit <= 0 or not skill_profile:
        return []

    try:
        from app.services import embedding_model
        # 1) 检查方案 C 是否可用
        if db_mod.find_embedding_count() == 0:
            return []

        # 2) 加载候选池(所有有向量的题)
        all_embeddings = embedding_model.load_all_embeddings()
        if not all_embeddings:
            return []

        # 3) 算弱项 tag 质心
        weak_centroids = embedding_model.find_weak_tag_centroids(skill_profile)
        if not weak_centroids:
            return []

        # 4) 找语义近邻
        neighbors = embedding_model.find_semantic_neighbors(
            weak_centroids, all_embeddings, top_k=limit, min_score=min_score,
        )
        if not neighbors:
            return []

        # 5) 把 problem_id 转成完整 problem dict
        pids = [pid for pid, _ in neighbors]
        problems = db_mod.find_problems_by_ids(pids)
        pid_to_problem = {p["id"]: p for p in problems}

        # 6) 按相似度降序返回
        result: list[dict[str, Any]] = []
        for pid, _ in neighbors:
            p = pid_to_problem.get(pid)
            if p:
                result.append(p)
        return result
    except Exception as exc:
        logger.warning("recall_by_semantic failed (will degrade to A+B): %s", exc)
        return []


def collect_candidates(
    skill_profile: list[dict[str, Any]],
    feedback_ctx: dict[str, Any] | None,
    limit: int,
    weak_ratio: float = 0.60,
    diff_ratio: float = 0.25,
    explore_ratio: float = 0.15,
    student_id: int | None = None,
    wrong_question_ratio: float = 0.20,
    semantic_ratio: float = 0.15,
) -> dict[int, dict[str, Any]]:
    """
    多路召回合并去重 (方案 A+B+C 总共 6 路)。

    Parameters
    ----------
    skill_profile : list[dict]
        学生技能画像列表。
    feedback_ctx : dict | None
        反馈上下文，包含 completedProblemIds, dislikedProblemIds。
    limit : int
        期望推荐数量。
    student_id : int | None
        错题本召回需要学生 ID。
    wrong_question_ratio : float
        错题本召回占 limit 的比例。
    semantic_ratio : float
        方案 C 语义召回占 limit 的比例。表/模型不可用时自动降级为 0。

    Returns
    -------
    dict[int, dict]
        problem_id -> problem 映射。
    """
    candidate_by_id: dict[int, dict[str, Any]] = {}

    # 路召回 1-3:弱项 / 难度 / 探索 (方案 A+B)
    for p in recall_by_weakness(skill_profile, max(1, int(limit * weak_ratio))):
        candidate_by_id.setdefault(p["id"], p)

    for p in recall_by_difficulty(skill_profile, max(1, int(limit * diff_ratio))):
        candidate_by_id.setdefault(p["id"], p)

    for p in recall_by_exploration(skill_profile, max(1, int(limit * explore_ratio))):
        candidate_by_id.setdefault(p["id"], p)

    # 路召回 4:错题本
    if student_id and wrong_question_ratio > 0:
        for p in recall_by_wrong_question_signals(student_id, max(1, int(limit * wrong_question_ratio))):
            candidate_by_id.setdefault(p["id"], p)

    # 路召回 5:语义近邻 (方案 C) - 优雅降级
    if semantic_ratio > 0:
        semantic_hits = recall_by_semantic(skill_profile, max(1, int(limit * semantic_ratio)))
        if semantic_hits:
            for p in semantic_hits:
                candidate_by_id.setdefault(p["id"], p)
        else:
            logger.debug("Semantic recall unavailable, falling back to popularity only")

    # 路召回 6:热门题补足
    for p in recall_by_popularity(max(1, limit - len(candidate_by_id))):
        candidate_by_id.setdefault(p["id"], p)

    # 历史过滤
    if feedback_ctx:
        disliked = set(feedback_ctx.get("disliked_problem_ids", []))
        completed = set(feedback_ctx.get("completed_problem_ids", []))
        min_keep = max(3, limit // 2)

        filtered = {
            pid: p for pid, p in candidate_by_id.items()
            if pid not in disliked and pid not in completed
        }
        if len(filtered) >= min_keep:
            candidate_by_id = filtered
        else:
            candidate_by_id = {pid: p for pid, p in candidate_by_id.items() if pid not in disliked}

    # 兜底补足
    min_target = max(limit, min(60, limit * 3))
    if len(candidate_by_id) < min_target:
        extras = db_mod.find_problems_page(0, min_target * 2)
        for p in extras:
            candidate_by_id.setdefault(p["id"], p)

    return candidate_by_id
