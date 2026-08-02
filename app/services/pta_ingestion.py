"""
PTA 数据摄取服务 — 从 spider-repo 爬取的 PTA 数据中提取学生技能画像

支持两种数据源:
1. Unified schema (推荐): student_problem_attempt, student_problem_state
2. Legacy schema (兜底): submit_situation, problem_score_detail

通过 PTA-to-LeetCode 标签映射，将 PTA 题目的知识点转换为 LeetCode 标签体系，
然后调用 BKT + 遗忘 + Wilson 引擎初始化/更新 student_skill_state。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app import db as db_mod

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# PTA 题目关键词 → LeetCode 标签映射
# ──────────────────────────────────────────

# 默认硬编码映射（数据库 pta_tag_mapping 表未初始化时的兜底）
_DEFAULT_PTA_TAG_MAP: dict[str, list[tuple[str, float]]] = {
    # 中文关键词 → [(leetcode_tag, relevance), ...]
    "递归": [("递归", 0.9)],
    "分治": [("分治", 0.9)],
    "排序": [("排序", 0.95)],
    "查找": [("二分查找", 0.8)],
    "二分": [("二分查找", 0.9)],
    "链表": [("链表", 0.95)],
    "线性表": [("链表", 0.8), ("数组", 0.6)],
    "顺序表": [("数组", 0.9)],
    "列表": [("链表", 0.8)],
    "栈": [("栈", 0.95)],
    "队列": [("队列", 0.95)],
    "树": [("树", 0.9)],
    "二叉树": [("树", 0.95)],
    "图": [("图", 0.9)],
    "哈希": [("哈希表", 0.9)],
    "字符串": [("字符串", 0.9)],
    "数组": [("数组", 0.9)],
    "动态规划": [("动态规划", 0.9)],
    "贪心": [("贪心", 0.9)],
    "回溯": [("回溯", 0.9)],
    "堆": [("堆", 0.9)],
    "并查集": [("并查集", 0.85)],
    "位运算": [("位运算", 0.85)],
    "滑动窗口": [("滑动窗口", 0.9)],
    "双指针": [("双指针", 0.9)],
    "广度优先": [("图", 0.7), ("队列", 0.6)],
    "深度优先": [("图", 0.7), ("栈", 0.5), ("递归", 0.6)],
    "最短路径": [("图", 0.85)],
    "最小生成树": [("图", 0.8), ("贪心", 0.5)],
    "拓扑排序": [("图", 0.8)],
    "KMP": [("字符串", 0.8)],
    "归并排序": [("排序", 0.8), ("分治", 0.6)],
    "快速排序": [("排序", 0.85)],
    "希尔排序": [("排序", 0.7)],
    "基数排序": [("排序", 0.6)],
    "AVL": [("树", 0.85)],
    "红黑树": [("树", 0.8)],
    "B树": [("树", 0.75)],
    "散列表": [("哈希表", 0.9)],
    "Dijkstra": [("图", 0.85)],
    "Floyd": [("图", 0.8), ("动态规划", 0.4)],
    "Prim": [("图", 0.7)],
    "Kruskal": [("图", 0.7), ("并查集", 0.5)],
    "Huffman": [("贪心", 0.8), ("树", 0.5)],
}

# 已接受的 PTA 判题状态
_ACCEPTED_STATUSES = frozenset({
    "AC", "ACCEPTED", "答案正确", "CORRECT", "PASS",
    "100", "满分", "Accepted",
})


def _resolve_student_id(student_id: int | None = None,
                         student_no: str | None = None) -> int | None:
    """将 student_id 或 student_no 解析为 student_profile.id。"""
    if student_id is not None:
        # 验证该 student_id 是否存在
        profile = db_mod.find_student_by_id(student_id)
        if profile:
            return profile["id"]
        # 可能是 legacy student_id，直接返回
        return student_id

    if student_no is not None:
        profile = db_mod.find_student_by_student_no(student_no)
        if profile:
            return profile["id"]
        logger.warning("student_no '%s' not found in student_profile", student_no)
        return None

    return None


def _get_pta_tag_map() -> dict[str, list[tuple[str, float]]]:
    """获取 PTA → LeetCode 标签映射（先查 DB，无数据则用硬编码默认值）。"""
    try:
        rows = db_mod.find_pta_tag_mappings()
        if rows:
            mapping: dict[str, list[tuple[str, float]]] = defaultdict(list)
            for row in rows:
                key = row.get("pta_keyword", "").strip()
                tag = row.get("leetcode_tag", "").strip()
                rel = float(row.get("relevance", 0.8))
                if key and tag:
                    mapping[key].append((tag, rel))
            if mapping:
                return dict(mapping)
    except Exception as exc:
        logger.warning("Failed to load pta_tag_mapping from DB, using defaults: %s", exc)

    return _DEFAULT_PTA_TAG_MAP


def _extract_tags_from_text(text: str,
                             tag_map: dict[str, list[tuple[str, float]]],
                             ) -> list[tuple[str, float]]:
    """
    从题目文本/标题中提取匹配的 LeetCode 标签。

    使用词边界匹配避免误匹配（如"数"不应匹配到"数学"）。
    对于中文关键词，要求完整子串匹配但排除包含该关键词的更长词语。
    """
    if not text:
        return []

    tags: list[tuple[str, float]] = []
    seen: set[str] = set()

    # 按关键词长度降序排列，优先匹配更具体的关键词
    sorted_keywords = sorted(tag_map.keys(), key=len, reverse=True)

    for keyword in sorted_keywords:
        if keyword.lower() in text.lower():
            # 基本匹配成功，检查标签
            tag_list = tag_map[keyword]
            for tag, rel in tag_list:
                if tag not in seen:
                    seen.add(tag)
                    tags.append((tag, rel))

    return tags


def _is_accepted(status: str | None) -> bool:
    """判断 PTA 判题状态是否为通过。"""
    if not status:
        return False
    return status.strip() in _ACCEPTED_STATUSES


# ──────────────────────────────────────────
# 核心摄取函数
# ──────────────────────────────────────────

def ingest_pta_data_for_student(
    student_id: int | None = None,
    student_no: str | None = None,
) -> dict[str, Any]:
    """
    从 PTA 爬虫数据中摄取单个学生的提交历史，更新技能画像。

    优先使用 unified schema，若为空则回退到 legacy schema。

    Parameters
    ----------
    student_id : int | None
        student_profile.id 或 legacy student_id。
    student_no : str | None
        学号（用于查找 student_profile）。

    Returns
    -------
    dict with:
        - student_id: int
        - tags_updated: int
        - total_attempts_processed: int
        - source: "unified" | "legacy"
    """
    resolved_id = _resolve_student_id(student_id, student_no)
    if resolved_id is None:
        return {
            "student_id": None,
            "tags_updated": 0,
            "total_attempts_processed": 0,
            "source": "none",
            "error": "student not found",
        }

    tag_map = _get_pta_tag_map()

    # 尝试 unified schema
    attempts = db_mod.find_problem_attempts_for_student(resolved_id)
    if attempts:
        return _process_unified_attempts(resolved_id, attempts, tag_map)

    # 回退到 legacy schema
    legacy = db_mod.find_legacy_submit_situation(resolved_id)
    if legacy:
        return _process_legacy_submissions(resolved_id, legacy, tag_map)

    logger.info("No PTA data found for student_id=%s", resolved_id)
    return {
        "student_id": resolved_id,
        "tags_updated": 0,
        "total_attempts_processed": 0,
        "source": "none",
    }


def _process_unified_attempts(
    student_id: int,
    attempts: list[dict[str, Any]],
    tag_map: dict[str, list[tuple[str, float]]],
) -> dict[str, Any]:
    """处理 unified schema 的提交记录。"""
    # 按 tag 聚合统计
    tag_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"attempts": 0, "successes": 0, "last_correct_at": None}
    )

    total_processed = 0
    for attempt in attempts:
        total_processed += 1

        # 优先使用 PTA 真实知识点路径/叶子；标题和题号仅作为兜底信号。
        title = attempt.get("problem_title", "") or ""
        source_id = str(attempt.get("source_problem_id", "") or "")
        knowledge_leaf = str(attempt.get("knowledge_leaf", "") or "")
        knowledge_path = str(attempt.get("knowledge_path", "") or "")
        offering_title = str(attempt.get("offering_title", "") or "")
        text = " ".join((knowledge_path, knowledge_leaf, offering_title, title, source_id))

        extracted_tags = _extract_tags_from_text(text, tag_map)
        if not extracted_tags:
            continue

        status = attempt.get("judge_status", "")
        is_correct = _is_accepted(status)

        for tag, _rel in extracted_tags:
            stats = tag_stats[tag]
            stats["attempts"] += 1
            if is_correct:
                stats["successes"] += 1
                stats["last_correct_at"] = attempt.get("submitted_at")

    # 按聚合结果更新/创建 student_skill_state
    tags_updated = 0
    for tag, stats in tag_stats.items():
        existing = db_mod.find_skill_state(student_id, tag)

        # 初始化或从 PTA 数据推断
        total_attempts = stats["attempts"]
        total_successes = stats["successes"]

        if total_attempts <= 0:
            continue

        success_rate = total_successes / total_attempts
        from app.services.recommendation_service import get_engine
        engine = get_engine()

        if existing and (existing.get("attempt_count") or 0) > 0:
            # 已有 LeetCode 画像数据 → 合并 PTA 数据作为补充
            existing_attempts = existing.get("attempt_count", 0) or 0
            existing_successes = existing.get("success_count", 0) or 0

            # 合并统计
            merged_attempts = existing_attempts + total_attempts
            merged_successes = existing_successes + total_successes

            # 如果 PTA 数据没有新增，跳过
            if merged_attempts == existing_attempts:
                continue

            # PTA 掌握度使用 Wilson 下限（避免稀疏数据过度自信）
            pta_wilson = max(0.0, engine.wilson.wilson_lower(success_rate, total_attempts))
            pta_mastery = pta_wilson * 100.0

            # 重新计算掌握度（使用已有掌握度作为先验，PTA 数据更新之）
            old_mastery = existing.get("mastery_score", 30.0) or 30.0
            # 加权融合：已有数据权重更高（更有针对性），PTA 作为补充
            leetcode_weight = min(1.0, existing_attempts / 10.0)  # 最多10题就完全信任LeetCode
            pta_weight = 1.0 - leetcode_weight

            new_mastery = old_mastery * leetcode_weight + pta_mastery * pta_weight
            new_mastery = max(0.0, min(100.0, new_mastery))

            confidence = engine.wilson.compute_confidence(merged_successes, merged_attempts)
            avg_attempts = merged_attempts / merged_successes if merged_successes > 0 else None

            # PTA 新提交意味着近期有练习，降低遗忘度
            old_forgetting = existing.get("forgetting_score", 0.0) or 0.0
            pta_forgetting_reduction = min(old_forgetting, total_attempts * 2.0)
            new_forgetting = max(0.0, old_forgetting - pta_forgetting_reduction)

            state = {
                "student_id": student_id,
                "tag_name": tag,
                "mastery_score": round(new_mastery, 2),
                "forgetting_score": round(new_forgetting, 2),
                "confidence_score": round(min(100.0, confidence), 2),
                "attempt_count": merged_attempts,
                "success_count": merged_successes,
                "avg_attempts_to_success": round(avg_attempts, 2) if avg_attempts else None,
                "last_practice_at": existing.get("last_practice_at"),
            }
            db_mod.upsert_skill_state(state)
            tags_updated += 1
            continue

        # 全新标签 → 从 PTA 数据初始化
        mastery = min(95.0, success_rate * 100.0)
        forgetting = max(0.0, (1.0 - success_rate) * 30.0)

        if total_attempts > 0 and total_successes > 0:
            confidence = engine.wilson.compute_confidence(total_successes, total_attempts)
        else:
            confidence = 0.0

        avg_attempts = total_attempts / total_successes if total_successes > 0 else None

        state = {
            "student_id": student_id,
            "tag_name": tag,
            "mastery_score": round(mastery, 2),
            "forgetting_score": round(forgetting, 2),
            "confidence_score": round(min(100.0, confidence), 2),
            "attempt_count": total_attempts,
            "success_count": total_successes,
            "avg_attempts_to_success": round(avg_attempts, 2) if avg_attempts else None,
            "last_practice_at": str(stats["last_correct_at"]) if stats.get("last_correct_at") else None,
        }
        db_mod.upsert_skill_state(state)
        tags_updated += 1

    logger.info(
        "PTA unified ingestion: student_id=%s, processed=%d, tags_updated=%d",
        student_id, total_processed, tags_updated,
    )

    return {
        "student_id": student_id,
        "tags_updated": tags_updated,
        "total_attempts_processed": total_processed,
        "source": "unified",
    }


def _process_legacy_submissions(
    student_id: int,
    submissions: list[dict[str, Any]],
    tag_map: dict[str, list[tuple[str, float]]],
) -> dict[str, Any]:
    """处理 legacy schema 的提交记录。"""
    # Legacy 的 experiment_name 包含题目集名称，可从中提取知识点
    tag_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"attempts": 0, "successes": 0, "last_correct_at": None}
    )

    total_processed = 0
    for sub in submissions:
        total_processed += 1

        # legacy 表的 situation 字段是判题状态
        situation = sub.get("situation", "") or ""
        is_correct = _is_accepted(situation)

        # 从 experiment_name 提取标签
        exp_name = sub.get("experiment_name", "") or ""
        extracted_tags = _extract_tags_from_text(exp_name, tag_map)
        if not extracted_tags:
            continue

        for tag, _rel in extracted_tags:
            stats = tag_stats[tag]
            stats["attempts"] += 1
            if is_correct:
                stats["successes"] += 1
                stats["last_correct_at"] = sub.get("submit_time")

    # 同样的聚合逻辑（含合并更新）
    tags_updated = 0
    from app.services.recommendation_service import get_engine
    engine = get_engine()

    for tag, stats in tag_stats.items():
        existing = db_mod.find_skill_state(student_id, tag)

        total_attempts = stats["attempts"]
        total_successes = stats["successes"]
        if total_attempts <= 0:
            continue

        success_rate = total_successes / total_attempts

        if existing and (existing.get("attempt_count") or 0) > 0:
            # 合并模式（同 unified 路径的逻辑）
            existing_attempts = existing.get("attempt_count", 0) or 0
            existing_successes = existing.get("success_count", 0) or 0
            merged_attempts = existing_attempts + total_attempts
            merged_successes = existing_successes + total_successes

            if merged_attempts == existing_attempts:
                continue

            # PTA 掌握度使用 Wilson 下限（避免稀疏数据过度自信）
            pta_wilson = max(0.0, engine.wilson.wilson_lower(success_rate, total_attempts))
            pta_mastery = pta_wilson * 100.0

            old_mastery = existing.get("mastery_score", 30.0) or 30.0
            leetcode_weight = min(1.0, existing_attempts / 10.0)
            pta_weight = 1.0 - leetcode_weight
            new_mastery = max(0.0, min(100.0, old_mastery * leetcode_weight + pta_mastery * pta_weight))

            confidence = engine.wilson.compute_confidence(merged_successes, merged_attempts)
            avg_attempts = merged_attempts / merged_successes if merged_successes > 0 else None

            # PTA 新提交降低遗忘度
            old_forgetting = existing.get("forgetting_score", 0.0) or 0.0
            pta_forgetting_reduction = min(old_forgetting, total_attempts * 2.0)
            new_forgetting = max(0.0, old_forgetting - pta_forgetting_reduction)

            state = {
                "student_id": student_id,
                "tag_name": tag,
                "mastery_score": round(new_mastery, 2),
                "forgetting_score": round(new_forgetting, 2),
                "confidence_score": round(min(100.0, confidence), 2),
                "attempt_count": merged_attempts,
                "success_count": merged_successes,
                "avg_attempts_to_success": round(avg_attempts, 2) if avg_attempts else None,
                "last_practice_at": existing.get("last_practice_at"),
            }
            db_mod.upsert_skill_state(state)
            tags_updated += 1
            continue

        # 全新标签初始化
        mastery = min(95.0, success_rate * 100.0)
        forgetting = max(0.0, (1.0 - success_rate) * 30.0)
        confidence = engine.wilson.compute_confidence(total_successes, total_attempts) if total_successes > 0 else 0.0
        avg_attempts = total_attempts / total_successes if total_successes > 0 else None

        state = {
            "student_id": student_id,
            "tag_name": tag,
            "mastery_score": round(mastery, 2),
            "forgetting_score": round(forgetting, 2),
            "confidence_score": round(min(100.0, confidence), 2),
            "attempt_count": total_attempts,
            "success_count": total_successes,
            "avg_attempts_to_success": round(avg_attempts, 2) if avg_attempts else None,
            "last_practice_at": str(stats["last_correct_at"]) if stats.get("last_correct_at") else None,
        }
        db_mod.upsert_skill_state(state)
        tags_updated += 1

    logger.info(
        "PTA legacy ingestion: student_id=%s, processed=%d, tags_updated=%d",
        student_id, total_processed, tags_updated,
    )

    return {
        "student_id": student_id,
        "tags_updated": tags_updated,
        "total_attempts_processed": total_processed,
        "source": "legacy",
    }


def ingest_pta_data_for_class(class_id: int) -> dict[str, Any]:
    """
    摄取整个教学班的 PTA 数据。

    Parameters
    ----------
    class_id : int
        teaching_class.id

    Returns
    -------
    dict with summary stats
    """
    students = db_mod.find_students_by_class(class_id)
    if not students:
        return {"class_id": class_id, "students_processed": 0, "total_tags_updated": 0}

    total_tags = 0
    processed = 0
    errors = 0

    for student in students:
        sid = student["id"]
        try:
            result = ingest_pta_data_for_student(student_id=sid)
            total_tags += result.get("tags_updated", 0)
            if result.get("total_attempts_processed", 0) > 0:
                processed += 1
        except Exception as exc:
            logger.error("Failed to ingest PTA data for student %s: %s", sid, exc)
            errors += 1

    return {
        "class_id": class_id,
        "students_processed": processed,
        "total_students": len(students),
        "total_tags_updated": total_tags,
        "errors": errors,
    }
