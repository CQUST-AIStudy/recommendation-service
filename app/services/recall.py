"""
多路召回引擎 — 四路召回策略

1. 弱项标签召回 (60%)
2. 难度进阶召回 (25%)
3. 探索召回 (15%)
4. 热门题召回 (5%)
"""
from __future__ import annotations

import logging
import random
from typing import Any

from app import db as db_mod

logger = logging.getLogger(__name__)

# 中英文标签映射
_TAG_MAP: dict[str, str] = {
    "动态规划": "dynamic programming",
    "贪心": "greedy",
    "回溯": "backtrack",
    "图": "graph",
    "树": "tree",
    "堆": "heap",
    "并查集": "union find",
    "位运算": "bit manipulation",
    "数组": "array",
    "字符串": "string",
    "链表": "linked list",
    "哈希表": "hash",
    "栈": "stack",
    "队列": "queue",
    "排序": "sort",
    "二分查找": "binary search",
    "递归": "recursion",
    "分治": "divide and conquer",
    "滑动窗口": "sliding window",
    "双指针": "two pointers",
}

_EXPLORATION_TAGS = {"动态规划", "贪心", "回溯", "图", "树", "堆", "并查集", "位运算"}


def _get_english_tag(chinese: str) -> str:
    return _TAG_MAP.get(chinese, chinese)


def _build_text(problem: dict[str, Any]) -> str:
    title = problem.get("title_main", "") or ""
    text = problem.get("problem_text", "") or ""
    solution = problem.get("solution_text", "") or ""
    return (title + " " + text + " " + solution).lower()


def _matches_tag(problem: dict[str, Any], tag: str) -> bool:
    ptext = _build_text(problem)
    tag_lower = tag.lower()
    eng = _get_english_tag(tag).lower()
    return (tag_lower and tag_lower in ptext) or (eng and eng in ptext)


def _find_by_tags(tags: list[str], limit: int) -> list[dict[str, Any]]:
    """先按 tag 表查，查不到回退文本匹配。"""
    if not tags:
        return []

    try:
        ids = db_mod.find_problem_ids_by_tags("algorithm", tags)
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
        logger.warning("Tag DB lookup failed, fallback to text match: %s", exc)

    # 文本回退
    all_problems = db_mod.find_all_problems(2000)
    matched = []
    for p in all_problems:
        for tag in tags:
            if _matches_tag(p, tag):
                matched.append(p)
                break
        if len(matched) >= limit * 2:
            break
    random.shuffle(matched)
    return matched[:limit]


# ──────────────────────────────────────────
# 四路召回
# ──────────────────────────────────────────

def recall_by_weakness(skill_profile: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """
    弱项标签召回：找掌握度最低的5个标签，按标签查题。
    """
    weak = sorted(
        [s for s in skill_profile if (s.get("mastery_score") or 50) < 60],
        key=lambda s: s.get("mastery_score") or 50,
    )[:5]
    tags = [s["tag_name"] for s in weak if s.get("tag_name")]

    if not tags:
        # 全部都 >= 60，则取练习次数最少的
        tags = [
            s["tag_name"]
            for s in sorted(skill_profile, key=lambda s: s.get("attempt_count") or 0)[:3]
            if s.get("tag_name")
        ]

    return _find_by_tags(tags, limit)


def recall_by_difficulty(skill_profile: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """
    难度进阶召回：根据平均掌握度选目标难度。
    """
    if not skill_profile:
        return db_mod.find_problems_page(0, limit)

    avg = sum(s.get("mastery_score") or 50 for s in skill_profile) / len(skill_profile)
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


def collect_candidates(
    skill_profile: list[dict[str, Any]],
    feedback_ctx: dict[str, Any] | None,
    limit: int,
    weak_ratio: float = 0.60,
    diff_ratio: float = 0.25,
    explore_ratio: float = 0.15,
) -> dict[int, dict[str, Any]]:
    """
    多路召回合并去重。

    Parameters
    ----------
    skill_profile : list[dict]
        学生技能画像列表。
    feedback_ctx : dict | None
        反馈上下文，包含 completedProblemIds, dislikedProblemIds。
    limit : int
        期望推荐数量。

    Returns
    -------
    dict[int, dict]
        problem_id -> problem 映射。
    """
    candidate_by_id: dict[int, dict[str, Any]] = {}

    # 四路召回
    for p in recall_by_weakness(skill_profile, max(1, int(limit * weak_ratio))):
        candidate_by_id.setdefault(p["id"], p)

    for p in recall_by_difficulty(skill_profile, max(1, int(limit * diff_ratio))):
        candidate_by_id.setdefault(p["id"], p)

    for p in recall_by_exploration(skill_profile, max(1, int(limit * explore_ratio))):
        candidate_by_id.setdefault(p["id"], p)

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
