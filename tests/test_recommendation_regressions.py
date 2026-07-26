from __future__ import annotations

from contextlib import contextmanager

import pytest

from app import db as db_mod
from app.api.recommendation import _build_result, record_feedback_action
from app.core.responses import ApiError
from app.db import mysql_client
from app.schemas.recommendation import FeedbackRequest
from app.services import embedding_model, recall
from app.services.ranking import diversity_rerank, rank_and_score


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []
        self.executemany_calls = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def executemany(self, sql, params):
        self.executemany_calls.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


def test_mastery_zero_is_used_for_weak_centroid(monkeypatch):
    cursor = FakeCursor([{"tag_name": "动态规划", "problem_id": 1}])

    @contextmanager
    def fake_query():
        yield cursor

    monkeypatch.setattr(db_mod, "query", fake_query)
    result = embedding_model.find_weak_tag_centroids(
        [{"tag_name": "动态规划", "mastery_score": 0}],
        {1: [1.0, 0.0]},
        weak_threshold=60,
    )
    assert result == {"动态规划": [1.0, 0.0]}


def test_completed_and_disliked_are_never_supplemented(monkeypatch):
    monkeypatch.setattr(recall, "recall_by_weakness", lambda *_: [{"id": 1}])
    monkeypatch.setattr(recall, "recall_by_difficulty", lambda *_: [{"id": 2}])
    monkeypatch.setattr(recall, "recall_by_exploration", lambda *_: [])
    monkeypatch.setattr(recall, "recall_by_popularity", lambda *_: [{"id": 3}])
    monkeypatch.setattr(
        db_mod,
        "find_problems_page",
        lambda *_: [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}],
    )

    candidates = recall.collect_candidates(
        [{"tag_name": "动态规划", "mastery_score": 20}],
        {"completed_problem_ids": [1], "disliked_problem_ids": [2]},
        limit=2,
        semantic_ratio=0,
    )
    assert set(candidates) == {3, 4}
    assert candidates[4]["_recall_sources"] == ["supplement"]


def test_semantic_similarity_participates_in_ranking():
    problems = [
        {"id": 1, "quality_score": 1, "_semantic_score": 0.2},
        {"id": 2, "quality_score": 1, "_semantic_score": 0.9},
    ]
    weights = {
        "need_match": 0,
        "difficulty_fit": 0,
        "success_prob": 0,
        "novelty": 0,
        "quality": 0,
        "semantic": 1,
        "wrong_question": 0,
        "pta_error": 0,
        "repeat_penalty": 0,
    }
    ranked = rank_and_score(problems, [], None, weights)
    assert [item["problem_id"] for item in ranked] == [2, 1]
    assert ranked[0]["score_total"] == 0.9


def test_diversity_relaxation_is_explicit():
    items = [{"problem_id": problem_id} for problem_id in range(1, 6)]
    tags = {problem_id: [{"tag_name": "数组"}] for problem_id in range(1, 6)}
    result = diversity_rerank(items, limit=5, problem_tags_map=tags, max_tag_ratio=0.4)
    assert len(result) == 5
    assert sum(bool(item.get("diversity_relaxed")) for item in result) == 4


def test_result_builder_handles_orphan_problem_and_null_scores():
    req = {
        "request_id": "request-1",
        "status": "completed",
        "student_id": 1,
    }
    original_find_tags = db_mod.find_tags_for_problems
    original_find_skills = db_mod.find_all_skill_states
    db_mod.find_tags_for_problems = lambda _ids: {}
    db_mod.find_all_skill_states = lambda _student_id: []
    try:
        response = _build_result(req, [{
        "rank_no": 1,
        "problem_id": 99,
        "title_main": None,
        "score_total": None,
        "reason_text": None,
        }])
    finally:
        db_mod.find_tags_for_problems = original_find_tags
        db_mod.find_all_skill_states = original_find_skills
    assert response.status == "completed"
    assert response.items[0].problem is None
    assert response.items[0].score_total == 0.0
    assert response.items[0].matched_tag is None


def test_failed_result_is_not_reported_as_pending():
    response = _build_result({
        "request_id": "request-2",
        "status": "failed",
        "error_message": "database unavailable",
    })
    assert response.status == "failed"
    assert response.error_message == "database unavailable"


def test_unknown_feedback_request_is_rejected(monkeypatch):
    monkeypatch.setattr(db_mod, "get_request", lambda _request_id: None)
    with pytest.raises(ApiError) as exc_info:
        record_feedback_action(FeedbackRequest(
            requestId="missing-request",
            problemId=1,
            action="click",
        ))
    assert exc_info.value.status_code == 404


def test_items_and_completed_status_share_one_transaction(monkeypatch):
    cursor = FakeCursor([{"status": "pending"}])

    @contextmanager
    def fake_transaction():
        yield cursor

    monkeypatch.setattr(mysql_client, "transaction", fake_transaction)
    item = {
        "request_id": "request-3",
        "student_id": 1,
        "rank_no": 1,
        "problem_id": 1,
        "score_total": 0.8,
        "score_need_match": 0.8,
        "score_difficulty_fit": 0.8,
        "score_success_prob": 0.8,
        "score_novelty": 0.8,
        "score_quality": 0.8,
        "score_semantic": 0.7,
        "reason_text": "reason",
        "reason_json": "{}",
        "matched_tag": "动态规划",
        "recall_source": "semantic",
    }
    mysql_client.complete_request_with_items("request-3", [item])
    assert len(cursor.executemany_calls) == 1
    assert any("status = 'completed'" in sql for sql, _ in cursor.executed)
