from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from app import db as db_mod
from app.api.recommendation import _build_result, record_feedback_action
from app.core.responses import ApiError
from app.db import mysql_client
from app.schemas.recommendation import FeedbackRequest
from app.services import embedding_model, pta_ingestion, recall, recommendation_service
from app.services.ranking import compute_need_match, diversity_rerank, rank_and_score


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


def test_empty_skill_profile_is_initialized_from_pta_before_fallback(monkeypatch):
    calls = {"reads": 0, "ingestions": 0}

    def fake_find_states(_student_id):
        calls["reads"] += 1
        if calls["reads"] == 1:
            return []
        return [{"tag_name": "链表", "mastery_score": 35}]

    def fake_ingest(*, student_id):
        calls["ingestions"] += 1
        assert student_id == 7
        return {"tags_updated": 1}

    monkeypatch.setattr(db_mod, "find_all_skill_states", fake_find_states)
    monkeypatch.setattr(pta_ingestion, "ingest_pta_data_for_student", fake_ingest)

    profile = recommendation_service._load_or_initialize_skill_profile(7)

    assert calls == {"reads": 2, "ingestions": 1}
    assert profile == [{"tag_name": "链表", "mastery_score": 35}]


def test_pta_ingestion_rejects_unknown_numeric_student_id(monkeypatch):
    monkeypatch.setattr(db_mod, "find_student_by_id", lambda _student_id: None)

    assert pta_ingestion._resolve_student_id(student_id=987654) is None


def test_cold_start_scores_only_observable_problem_quality(monkeypatch):
    monkeypatch.setattr(
        db_mod,
        "find_problems_page",
        lambda *_: [{"id": 11, "quality_score": 0.73}],
    )

    items = recommendation_service._fallback_recommendations(
        student_id=7,
        limit=1,
        request_id="cold-start",
        feedback_ctx=None,
        fallback_reason="missing_skill_profile",
    )

    assert items[0]["score_total"] == pytest.approx(0.73)
    assert items[0]["score_quality"] == pytest.approx(0.73)
    assert items[0]["score_need_match"] == 0.0
    assert items[0]["score_difficulty_fit"] == 0.0
    assert items[0]["score_success_prob"] == 0.0
    assert items[0]["score_novelty"] == 0.0
    provenance = json.loads(items[0]["reason_json"])
    assert provenance["recommendationMode"] == "non_personalized_fallback"
    assert provenance["scoreBasis"] == ["problem_quality"]
    assert provenance["fallbackReason"] == "missing_skill_profile"


def test_empty_class_profile_never_uses_global_fallback(monkeypatch):
    monkeypatch.setattr(
        recommendation_service,
        "_load_skill_profile_for_scene",
        lambda *_: [],
    )
    monkeypatch.setattr(
        db_mod,
        "find_problems_page",
        lambda *_: pytest.fail("class-scoped empty profile must not query global fallback"),
    )

    assert recommendation_service._generate_recommendation_sync(
        student_id=7,
        limit=10,
        request_id="class-empty",
        scene="class:8",
    ) == []


def test_invalid_class_scene_is_rejected():
    with pytest.raises(ValueError, match="positive numeric class id"):
        recommendation_service._load_skill_profile_for_scene(7, "class:not-a-number")


def test_class_scene_builds_distinct_course_skill_profiles(monkeypatch):
    attempts_by_class = {
        8: [{"knowledge_path": "数据结构/线性表/链表", "judge_status": "WA"}],
        9: [{"knowledge_path": "算法设计/动态规划", "judge_status": "AC"}],
    }
    monkeypatch.setattr(
        db_mod,
        "find_problem_attempts_for_student_in_class",
        lambda student_id, class_id: attempts_by_class[class_id],
    )
    monkeypatch.setattr(
        pta_ingestion,
        "_get_pta_tag_map",
        lambda: {"链表": [("链表", 1.0)], "动态规划": [("动态规划", 1.0)]},
    )

    data_structure_profile = recommendation_service._load_skill_profile_for_scene(37, "class:8")
    algorithm_profile = recommendation_service._load_skill_profile_for_scene(37, "class:9")

    assert {state["tag_name"] for state in data_structure_profile} == {"链表"}
    assert {state["tag_name"] for state in algorithm_profile} == {"动态规划"}
    assert data_structure_profile != algorithm_profile

    monkeypatch.setattr(
        db_mod,
        "find_problem_ids_by_tag_names",
        lambda tags: [101] if "链表" in tags else [202],
    )
    monkeypatch.setattr(
        db_mod,
        "find_problems_by_ids",
        lambda ids: [{"id": pid, "title_main": "链表反转" if pid == 101 else "最长递增子序列"}
                     for pid in ids],
    )
    data_structure_titles = [
        item["title_main"] for item in recall.recall_by_weakness(data_structure_profile, 5)
    ]
    algorithm_titles = [
        item["title_main"] for item in recall.recall_by_weakness(algorithm_profile, 5)
    ]
    assert data_structure_titles == ["链表反转"]
    assert algorithm_titles == ["最长递增子序列"]


def test_pta_ingestion_uses_real_knowledge_path_when_title_is_generic(monkeypatch):
    saved_states = []

    class FakeWilson:
        @staticmethod
        def compute_confidence(_successes, _attempts):
            return 50.0

    class FakeEngine:
        wilson = FakeWilson()

    monkeypatch.setattr(db_mod, "find_skill_state", lambda *_: None)
    monkeypatch.setattr(db_mod, "upsert_skill_state", saved_states.append)
    monkeypatch.setattr(recommendation_service, "get_engine", lambda: FakeEngine())

    result = pta_ingestion._process_unified_attempts(7, [{
        "problem_title": "主要元素",
        "source_problem_id": "7-1",
        "offering_title": "第一次练习",
        "knowledge_leaf": "顺序表",
        "knowledge_path": "数据结构/线性表/顺序表",
        "judge_status": "AC",
        "submitted_at": "2026-08-02 10:00:00",
    }], pta_ingestion._DEFAULT_PTA_TAG_MAP)

    assert result["tags_updated"] >= 1
    assert {state["tag_name"] for state in saved_states} >= {"数组"}


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


def test_need_match_uses_each_students_skill_profile():
    problem = {"title_main": "最短路径", "problem_text": "", "solution_text": ""}
    tags = [{"tag_name": "图", "relevance_score": 1.0}]
    weak_profile = [{"tag_name": "图", "mastery_score": 20, "forgetting_score": 60}]
    strong_profile = [{"tag_name": "图", "mastery_score": 90, "forgetting_score": 10}]

    weak_match = compute_need_match(problem, weak_profile, tags)
    strong_match = compute_need_match(problem, strong_profile, tags)

    assert weak_match > strong_match
    assert weak_match != pytest.approx(0.5)


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
