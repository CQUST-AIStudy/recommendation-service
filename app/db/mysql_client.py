"""
MySQL database client — 直连 ptadatabase，读写推荐系统相关表。
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from queue import Empty, Full, Queue
from typing import Any, Generator

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_POOL_MAX = 10
_POOL_TIMEOUT = 5.0  # seconds
_pool: Queue = Queue(maxsize=_POOL_MAX)
_pool_state = {"created": 0}  # 使用 dict 避免全局变量声明问题
_pool_created_lock = threading.Lock()


def _create_conn() -> pymysql.Connection:
    s = get_settings()
    return pymysql.connect(
        host=s.db_host,
        port=s.db_port,
        user=s.db_user,
        password=s.db_password,
        database=s.db_name,
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


def get_conn() -> pymysql.Connection:
    """获取数据库连接（线程安全连接池，带超时等待）。"""
    # 先尝试从池中获取
    try:
        conn = _pool.get_nowait()
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            with _pool_created_lock:
                _pool_state["created"] = max(0, _pool_state["created"] - 1)
    except Empty:
        pass

    # 池空，创建新连接（限制总数）
    with _pool_created_lock:
        if _pool_state["created"] < _POOL_MAX:
            _pool_state["created"] += 1
            logger.debug("Creating new DB connection (total=%d)", _pool_state["created"])
            return _create_conn()

    # 超过上限，阻塞等待
    logger.warning("DB pool exhausted, waiting for available connection")
    try:
        conn = _pool.get(timeout=_POOL_TIMEOUT)
        try:
            conn.ping(reconnect=True)
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            with _pool_created_lock:
                _pool_state["created"] = max(0, _pool_state["created"] - 1)
    except Empty:
        pass

    # 最终兜底：强制创建
    logger.error("DB pool timeout, creating untracked connection")
    return _create_conn()


def release_conn(conn: pymysql.Connection) -> None:
    try:
        _pool.put_nowait(conn)
    except Full:
        try:
            conn.close()
            with _pool_created_lock:
                _pool_state["created"] = max(0, _pool_state["created"] - 1)
        except Exception:
            pass


def close_all() -> None:
    """关闭池中所有连接（用于服务关闭时清理）。"""
    while not _pool.empty():
        try:
            conn = _pool.get_nowait()
            try:
                conn.close()
            except Exception:
                pass
        except Empty:
            break
    logger.info("DB connection pool closed")


@contextmanager
def transaction() -> Generator[DictCursor, None, None]:
    conn = get_conn()
    cursor = conn.cursor()
    try:
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        release_conn(conn)


@contextmanager
def query() -> Generator[DictCursor, None, None]:
    conn = get_conn()
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()
        release_conn(conn)


# ──────────────────────────────────────────
# student_skill_state CRUD
# ──────────────────────────────────────────

def find_skill_state(student_id: int, tag_name: str) -> dict[str, Any] | None:
    with query() as cur:
        cur.execute(
            "SELECT * FROM student_skill_state WHERE student_id = %s AND tag_name = %s",
            (student_id, tag_name),
        )
        return cur.fetchone()


def find_all_skill_states(student_id: int) -> list[dict[str, Any]]:
    with query() as cur:
        cur.execute(
            "SELECT * FROM student_skill_state WHERE student_id = %s", (student_id,)
        )
        return cur.fetchall()


def find_skills_needing_decay(days_threshold: int) -> list[dict[str, Any]]:
    with query() as cur:
        cur.execute(
            "SELECT * FROM student_skill_state WHERE last_practice_at IS NOT NULL "
            "AND last_practice_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
            (days_threshold,),
        )
        return cur.fetchall()


def upsert_skill_state(state: dict[str, Any]) -> None:
    with transaction() as cur:
        cur.execute(
            """INSERT INTO student_skill_state
            (student_id, tag_name, mastery_score, forgetting_score, confidence_score,
             attempt_count, success_count, avg_attempts_to_success, last_practice_at)
            VALUES (%(student_id)s, %(tag_name)s, %(mastery_score)s, %(forgetting_score)s,
                    %(confidence_score)s, %(attempt_count)s, %(success_count)s,
                    %(avg_attempts_to_success)s, %(last_practice_at)s)
            ON DUPLICATE KEY UPDATE
                mastery_score = VALUES(mastery_score),
                forgetting_score = VALUES(forgetting_score),
                confidence_score = VALUES(confidence_score),
                attempt_count = VALUES(attempt_count),
                success_count = VALUES(success_count),
                avg_attempts_to_success = VALUES(avg_attempts_to_success),
                last_practice_at = VALUES(last_practice_at)""",
            state,
        )


def update_skill_scores(student_id: int, tag_name: str, mastery: float,
                        forgetting: float, confidence: float) -> None:
    with transaction() as cur:
        cur.execute(
            """UPDATE student_skill_state
            SET mastery_score = %s, forgetting_score = %s, confidence_score = %s
            WHERE student_id = %s AND tag_name = %s""",
            (mastery, forgetting, confidence, student_id, tag_name),
        )


# ──────────────────────────────────────────
# leetcode_problem_bank + tag queries
# ──────────────────────────────────────────

def find_problem_by_id(problem_id: int) -> dict[str, Any] | None:
    with query() as cur:
        cur.execute("SELECT * FROM leetcode_problem_bank WHERE id = %s", (problem_id,))
        return cur.fetchone()


def find_problems_by_difficulty(difficulty: str, limit: int = 100) -> list[dict[str, Any]]:
    with query() as cur:
        cur.execute(
            "SELECT * FROM leetcode_problem_bank WHERE difficulty = %s LIMIT %s",
            (difficulty, limit),
        )
        return cur.fetchall()


def find_problems_by_ids(ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join(["%s"] * len(ids))
    with query() as cur:
        cur.execute(
            f"SELECT * FROM leetcode_problem_bank WHERE id IN ({placeholders})", ids
        )
        return cur.fetchall()


def find_problems_page(offset: int, limit: int) -> list[dict[str, Any]]:
    with query() as cur:
        cur.execute(
            "SELECT * FROM leetcode_problem_bank ORDER BY quality_score DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        return cur.fetchall()


def find_all_problems(limit: int = 1000) -> list[dict[str, Any]]:
    with query() as cur:
        cur.execute("SELECT * FROM leetcode_problem_bank ORDER BY quality_score DESC LIMIT %s", (limit,))
        return cur.fetchall()


def find_problem_ids_by_tags(tag_category: str, tags: list[str]) -> list[int]:
    if not tags:
        return []
    placeholders = ",".join(["%s"] * len(tags))
    with query() as cur:
        cur.execute(
            f"""SELECT DISTINCT problem_id FROM leetcode_problem_tag
            WHERE tag_category = %s AND tag_name IN ({placeholders})
            ORDER BY problem_id""",
            [tag_category, *tags],
        )
        return [row["problem_id"] for row in cur.fetchall()]


def find_tags_for_problem(problem_id: int) -> list[dict[str, Any]]:
    with query() as cur:
        cur.execute(
            "SELECT * FROM leetcode_problem_tag WHERE problem_id = %s", (problem_id,)
        )
        return cur.fetchall()


# ──────────────────────────────────────────
# leetcode_recommend_request CRUD
# ──────────────────────────────────────────

def create_request(request_id: str, student_id: int, scene: str, limit: int) -> None:
    with transaction() as cur:
        cur.execute(
            """INSERT INTO leetcode_recommend_request
            (request_id, student_id, scene, request_limit, status)
            VALUES (%s, %s, %s, %s, 'pending')""",
            (request_id, student_id, scene, limit),
        )


def get_request(request_id: str) -> dict[str, Any] | None:
    with query() as cur:
        cur.execute(
            "SELECT * FROM leetcode_recommend_request WHERE request_id = %s", (request_id,)
        )
        return cur.fetchone()


def complete_request(request_id: str) -> None:
    with transaction() as cur:
        cur.execute(
            "UPDATE leetcode_recommend_request SET status = 'completed', finished_at = NOW() "
            "WHERE request_id = %s",
            (request_id,),
        )


def fail_request(request_id: str, error_message: str) -> None:
    with transaction() as cur:
        cur.execute(
            "UPDATE leetcode_recommend_request SET status = 'failed', "
            "error_message = %s, finished_at = NOW() WHERE request_id = %s",
            (error_message[:512], request_id),
        )


# ──────────────────────────────────────────
# leetcode_recommend_item CRUD
# ──────────────────────────────────────────

def insert_recommend_items(items: list[dict[str, Any]]) -> None:
    if not items:
        return
    with transaction() as cur:
        cur.executemany(
            """INSERT INTO leetcode_recommend_item
            (request_id, student_id, rank_no, problem_id, score_total,
             score_need_match, score_difficulty_fit, score_success_prob,
             score_novelty, score_quality, reason_text, reason_json)
            VALUES (%(request_id)s, %(student_id)s, %(rank_no)s, %(problem_id)s,
                    %(score_total)s, %(score_need_match)s, %(score_difficulty_fit)s,
                    %(score_success_prob)s, %(score_novelty)s, %(score_quality)s,
                    %(reason_text)s, %(reason_json)s)""",
            items,
        )


def find_recommend_items(request_id: str) -> list[dict[str, Any]]:
    with query() as cur:
        cur.execute(
            """SELECT i.*, p.title_main, p.source_url, p.difficulty, p.problem_text,
                    p.solution_text, p.estimated_minutes, p.quality_score as problem_quality
            FROM leetcode_recommend_item i
            LEFT JOIN leetcode_problem_bank p ON i.problem_id = p.id
            WHERE i.request_id = %s
            ORDER BY i.rank_no""",
            (request_id,),
        )
        return cur.fetchall()


# ──────────────────────────────────────────
# leetcode_recommend_feedback CRUD
# ──────────────────────────────────────────

def insert_feedback(fb: dict[str, Any]) -> None:
    with transaction() as cur:
        cur.execute(
            """INSERT INTO leetcode_recommend_feedback
            (request_id, student_id, problem_id, session_id, action, action_at, extra_json)
            VALUES (%(request_id)s, %(student_id)s, %(problem_id)s,
                    %(session_id)s, %(action)s, %(action_at)s, %(extra_json)s)""",
            fb,
        )


def find_feedback_by_student(student_id: int, limit: int = 300) -> list[dict[str, Any]]:
    with query() as cur:
        cur.execute(
            "SELECT * FROM leetcode_recommend_feedback WHERE student_id = %s "
            "ORDER BY action_at DESC LIMIT %s",
            (student_id, limit),
        )
        return cur.fetchall()


# ──────────────────────────────────────────
# PTA unified schema queries (spider-repo)
# ──────────────────────────────────────────

def find_student_by_student_no(student_no: str) -> dict[str, Any] | None:
    """Lookup student_profile by student_no (学号)."""
    with query() as cur:
        cur.execute(
            "SELECT * FROM student_profile WHERE student_no = %s LIMIT 1",
            (student_no,),
        )
        return cur.fetchone()


def find_student_by_id(student_id: int) -> dict[str, Any] | None:
    """Lookup student_profile by id."""
    with query() as cur:
        cur.execute(
            "SELECT * FROM student_profile WHERE id = %s LIMIT 1",
            (student_id,),
        )
        return cur.fetchone()


def find_problem_attempts_for_student(student_id: int) -> list[dict[str, Any]]:
    """Get all problem attempts for a student from unified schema."""
    with query() as cur:
        cur.execute(
            """SELECT spa.*, ap.title AS problem_title, ap.source_problem_id,
                      ao.title_override AS offering_title
            FROM student_problem_attempt spa
            LEFT JOIN assignment_problem ap ON spa.problem_id = ap.id
            LEFT JOIN assignment_offering ao ON spa.offering_id = ao.id
            WHERE spa.student_id = %s
            ORDER BY spa.submitted_at""",
            (student_id,),
        )
        return cur.fetchall()


def find_problem_states_for_student(student_id: int) -> list[dict[str, Any]]:
    """Get latest problem states for a student."""
    with query() as cur:
        cur.execute(
            """SELECT sps.*, ap.title AS problem_title, ap.source_problem_id
            FROM student_problem_state sps
            LEFT JOIN assignment_problem ap ON sps.problem_id = ap.id
            WHERE sps.student_id = %s""",
            (student_id,),
        )
        return cur.fetchall()


def find_assignments_for_student(student_id: int) -> list[dict[str, Any]]:
    """Get assignment records for a student."""
    with query() as cur:
        cur.execute(
            "SELECT * FROM student_assignment WHERE student_id = %s",
            (student_id,),
        )
        return cur.fetchall()


def find_all_problems_for_assignments(offering_ids: list[int]) -> list[dict[str, Any]]:
    """Get all problems for given assignment offerings."""
    if not offering_ids:
        return []
    placeholders = ",".join(["%s"] * len(offering_ids))
    with query() as cur:
        cur.execute(
            f"SELECT * FROM assignment_problem WHERE offering_id IN ({placeholders})",
            offering_ids,
        )
        return cur.fetchall()


def find_students_by_class(class_id: int) -> list[dict[str, Any]]:
    """Get all student_profile IDs in a teaching class."""
    with query() as cur:
        cur.execute(
            """SELECT sp.* FROM student_profile sp
            INNER JOIN class_member cm ON sp.id = cm.student_id
            WHERE cm.class_id = %s""",
            (class_id,),
        )
        return cur.fetchall()


def find_offering_ids_for_class(class_id: int) -> list[int]:
    """Get all assignment_offering IDs for a teaching class."""
    with query() as cur:
        cur.execute(
            "SELECT id FROM assignment_offering WHERE class_id = %s",
            (class_id,),
        )
        return [row["id"] for row in cur.fetchall()]


def find_problems_by_source_ids(source_problem_ids: list[str]) -> list[dict[str, Any]]:
    """Find assignment_problems by source_problem_id (PTA problem IDs)."""
    if not source_problem_ids:
        return []
    placeholders = ",".join(["%s"] * len(source_problem_ids))
    with query() as cur:
        cur.execute(
            f"SELECT * FROM assignment_problem WHERE source_problem_id IN ({placeholders})",
            source_problem_ids,
        )
        return cur.fetchall()


# ──────────────────────────────────────────
# Legacy schema queries (spider-repo sync_to_db.py)
# ──────────────────────────────────────────

def find_legacy_submit_situation(student_id: int) -> list[dict[str, Any]]:
    """Get submission history from legacy submit_situation table."""
    with query() as cur:
        cur.execute(
            "SELECT * FROM submit_situation WHERE student_id = %s",
            (student_id,),
        )
        return cur.fetchall()


def find_legacy_problem_score_detail(student_id: int) -> list[dict[str, Any]]:
    """Get problem score details from legacy table."""
    with query() as cur:
        cur.execute(
            "SELECT * FROM problem_score_detail WHERE student_id = %s",
            (student_id,),
        )
        return cur.fetchall()


# ──────────────────────────────────────────
# PTA tag mapping
# ──────────────────────────────────────────

def find_pta_tag_mappings() -> list[dict[str, Any]]:
    """Get all PTA-to-LeetCode tag mappings."""
    with query() as cur:
        cur.execute("SELECT * FROM pta_tag_mapping")
        return cur.fetchall()


def upsert_pta_tag_mapping(mapping: dict[str, Any]) -> None:
    with transaction() as cur:
        cur.execute(
            """INSERT INTO pta_tag_mapping (pta_keyword, leetcode_tag, relevance)
            VALUES (%(pta_keyword)s, %(leetcode_tag)s, %(relevance)s)
            ON DUPLICATE KEY UPDATE
                leetcode_tag = VALUES(leetcode_tag),
                relevance = VALUES(relevance)""",
            mapping,
        )
