"""Wrong-question notebook feature loader for the recommendation pipeline.

Caches per-student wrong-question tag context and PTA high-frequency error
context for the duration of one recommendation request, and exposes boost
functions used by the ranking stage.
"""
from __future__ import annotations

import logging
from typing import Any

from app import db as db_mod
from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Per-process cache. reset_cache_for_request() clears it at the start of each
# recommendation request so stale data does not leak across students.
_cache: dict[str, dict[str, Any]] = {}


def load_wrong_question_context_by_id(student_id: int) -> dict[str, Any]:
    """Load (and cache) wrong-question tag context keyed by student_id.

    The recommendation pipeline speaks in student_id space, so this is the
    primary entry point. Internally it resolves student_id -> student_no.
    """
    if not student_id:
        return _empty_context()
    key = f"id:{student_id}"
    if key in _cache:
        return _cache[key]

    rows = db_mod.fetch_student_wrong_question_tags_by_id(student_id)
    ctx = _build_context(rows)
    _cache[key] = ctx
    return ctx


def load_wrong_question_context_by_no(student_no: str) -> dict[str, Any]:
    """Load (and cache) wrong-question tag context keyed by student_no."""
    if not student_no:
        return _empty_context()
    key = f"no:{student_no}"
    if key in _cache:
        return _cache[key]

    rows = db_mod.fetch_student_wrong_question_tags(student_no)
    ctx = _build_context(rows)
    _cache[key] = ctx
    return ctx


def load_pta_error_context_by_no(student_no: str, min_errors: int = 5) -> dict[str, Any]:
    """按学号加载 PTA 高频错题上下文，内部解析为 student_profile.id 后查询。"""
    if not student_no:
        return _empty_pta_context()
    profile = db_mod.find_student_by_student_no(student_no)
    if not profile or not profile.get("id"):
        logger.info("No student_profile found for student_no=%s", student_no)
        return _empty_pta_context()
    return load_pta_error_context(profile["id"], min_errors)


def load_pta_error_context(student_id: int, min_errors: int = 5) -> dict[str, Any]:
    """Load PTA problems where the student has ≥ min_errors wrong attempts.

    Returns context with per-problem error counts and tag counts from the
    pta_tag_mapping table for use in the ranking boost.
    """
    if not student_id:
        return _empty_pta_context()
    key = f"pta:{student_id}:{min_errors}"
    if key in _cache:
        return _cache[key]

    rows = db_mod.find_pta_high_frequency_errors(student_id, min_errors)

    pta_items = []
    tag_counts: dict[str, int] = _load_pta_error_tags_from_db(student_id, rows)
    for r in rows or []:
        problem_id = r.get("problem_id")
        error_count = int(r.get("error_count") or 0)
        pta_items.append({
            "problem_id": problem_id,
            "error_count": error_count,
            "problem_title": r.get("problem_title", ""),
            "source_problem_id": r.get("source_problem_id"),
            "offering_id": r.get("offering_id"),
            "offering_title": r.get("offering_title", ""),
        })

    ctx = {
        "pta_items": pta_items,
        "pta_tag_counts": tag_counts,
        "total_pta_errors": sum(i["error_count"] for i in pta_items),
    }
    _cache[key] = ctx
    return ctx


def _load_pta_error_tags_from_db(
    student_id: int, rows: list[dict[str, Any]]
) -> dict[str, int]:
    """Load tag relevance scores from pta_tag_mapping for the errored problems."""
    tag_counts: dict[str, int] = {}
    if not rows:
        return tag_counts
    try:
        mappings = db_mod.find_pta_tag_mappings() or []
    except Exception:
        logger.warning("Failed to load pta_tag_mapping, PTA tag boost unavailable", exc_info=True)
        return tag_counts

    title_map: dict[str, list[tuple[str, float]]] = {}
    for m in mappings:
        kw = (m.get("pta_keyword") or "").casefold()
        if not kw:
            continue
        tag = m.get("leetcode_tag")
        relevance = float(m.get("relevance") or 0.8)
        if not tag:
            continue
        title_map.setdefault(kw, []).append((tag, relevance))

    for r in rows:
        title = (r.get("problem_title") or "").casefold()
        error_count = int(r.get("error_count") or 0)
        for kw, tags in title_map.items():
            if kw in title:
                for tag, rel in tags:
                    weighted = int(error_count * rel) or 1
                    tag_counts[tag] = tag_counts.get(tag, 0) + weighted
    return tag_counts


def reset_cache_for_request() -> None:
    """Clear the cache. Called at the start of every recommendation request."""
    _cache.clear()


def compute_wrong_question_boost(
    problem_tags: list[dict[str, Any]] | None,
    ctx: dict[str, Any] | None,
) -> float:
    """Boost = min(1.0, unresolved_hits + 0.3 * resolved_hits) / 3.

    Rationale: each unresolved-tag hit contributes strongly (1.0), resolved hits
    contribute gently (0.3) to keep related practice in rotation. Three or more
    unresolved hits saturate the boost.
    """
    if not ctx or not problem_tags:
        return 0.0
    unresolved = ctx.get("unresolved_tag_counts", {}) or {}
    resolved = ctx.get("resolved_tag_counts", {}) or {}
    if not unresolved and not resolved:
        return 0.0

    u_hits = sum(
        1 for pt in problem_tags
        if pt and pt.get("tag_name") in unresolved
    )
    r_hits = sum(
        1 for pt in problem_tags
        if pt and pt.get("tag_name") in resolved
    )
    if u_hits == 0 and r_hits == 0:
        return 0.0
    return min(1.0, (u_hits + 0.3 * r_hits) / 3.0)


def compute_pta_error_boost(
    problem_tags: list[dict[str, Any]] | None,
    pta_ctx: dict[str, Any] | None,
    max_boost: float = 1.0,
) -> float:
    """基于 PTA 高频错题标签的加权提升。

    每个匹配标签: weight = min(1.0, error_count / 15.0)
    boost = min(max_boost, sum(all_tag_weights))

    15 次以上错误→单标签权重饱和为 1.0，3 个标签各 5 次→约 1.0。
    """
    if not pta_ctx or not problem_tags:
        return 0.0
    pta_tag_counts = pta_ctx.get("pta_tag_counts", {}) or {}
    if not pta_tag_counts:
        return 0.0

    total = 0.0
    for pt in problem_tags:
        tag_name = pt.get("tag_name") if pt else None
        if not tag_name or tag_name not in pta_tag_counts:
            continue
        error_count = pta_tag_counts[tag_name]
        tag_weight = min(1.0, error_count / 15.0)
        total += tag_weight

    return round(min(max_boost, total), 4) if total > 0 else 0.0


def get_boost_weight() -> float:
    return float(get_settings().weight_wrong_question)


def get_pta_boost_weight() -> float:
    return float(get_settings().weight_pta_error)


def _build_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unresolved: dict[str, int] = {}
    resolved: dict[str, int] = {}
    total_unresolved = 0
    for r in rows or []:
        tags_csv = (r.get("tags_cached") or "").strip()
        if not tags_csv:
            continue
        is_resolved = bool(r.get("is_resolved"))
        bucket = resolved if is_resolved else unresolved
        for tag in tags_csv.split(","):
            t = tag.strip()
            if not t:
                continue
            bucket[t] = bucket.get(t, 0) + 1
        if not is_resolved:
            total_unresolved += 1
    return {
        "resolved_tag_counts": resolved,
        "unresolved_tag_counts": unresolved,
        "total_unresolved_count": total_unresolved,
    }


def _empty_context() -> dict[str, Any]:
    return {
        "resolved_tag_counts": {},
        "unresolved_tag_counts": {},
        "total_unresolved_count": 0,
    }


def _empty_pta_context() -> dict[str, Any]:
    return {
        "pta_items": [],
        "pta_tag_counts": {},
        "total_pta_errors": 0,
    }
