"""Wrong-question notebook feature loader for the recommendation pipeline.

Caches per-student wrong-question tag context for the duration of one
recommendation request, and exposes a boost function used by the ranking stage.
"""
from __future__ import annotations

from typing import Any

from app import db as db_mod
from app.core.config import get_settings

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


def get_boost_weight() -> float:
    return float(get_settings().weight_wrong_question)


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
