"""
Unit tests for the wrong_question_boost ranking factor.

Covers:
  1. Empty / None inputs return 0.0 (no crash).
  2. Three unresolved tag matches saturate the boost to 1.0.
  3. Mixed unresolved + resolved hits produce the expected blend.
  4. End-to-end: two equal-baseline problems differ in score_total only because
     one's tags intersect the student's unresolved wrong-question set.
"""
from app.services.ranking import rank_and_score
from app.services.wrong_question_features import compute_wrong_question_boost


# ──────────────────────────────────────────
# compute_wrong_question_boost unit tests
# ──────────────────────────────────────────

def test_boost_returns_zero_for_none_inputs():
    assert compute_wrong_question_boost(None, None) == 0.0


def test_boost_returns_zero_for_empty_context():
    ctx = {
        "resolved_tag_counts": {},
        "unresolved_tag_counts": {},
        "total_unresolved_count": 0,
    }
    tags = [{"tag_name": "DP"}, {"tag_name": "Array"}]
    assert compute_wrong_question_boost(tags, ctx) == 0.0


def test_boost_saturates_on_three_unresolved_hits():
    ctx = {
        "resolved_tag_counts": {},
        "unresolved_tag_counts": {"DP": 1, "Array": 1, "Greedy": 1},
        "total_unresolved_count": 3,
    }
    tags = [
        {"tag_name": "DP"},
        {"tag_name": "Array"},
        {"tag_name": "Greedy"},
    ]
    # (3 + 0.3*0) / 3 = 1.0
    assert compute_wrong_question_boost(tags, ctx) == 1.0


def test_boost_blends_unresolved_and_resolved_hits():
    ctx = {
        "resolved_tag_counts": {"Tree": 1, "Heap": 1, "Graph": 1, "Sorting": 1, "Hash": 1},
        "unresolved_tag_counts": {"DP": 1},
        "total_unresolved_count": 1,
    }
    tags = [
        {"tag_name": "DP"},         # 1 unresolved
        {"tag_name": "Tree"},       # resolved
        {"tag_name": "Heap"},       # resolved
        {"tag_name": "Graph"},      # resolved
        {"tag_name": "Sorting"},    # resolved
        {"tag_name": "Hash"},       # resolved
    ]
    # (1 + 0.3*5) / 3 = 2.5/3 ≈ 0.8333, capped at 1.0
    expected = (1 + 0.3 * 5) / 3.0
    assert abs(compute_wrong_question_boost(tags, ctx) - min(1.0, expected)) < 1e-9


# ──────────────────────────────────────────
# rank_and_score integration
# ──────────────────────────────────────────

def test_ranking_lifts_problem_matching_unresolved_wrong_question():
    """Two problems identical except tag set; the one whose tag matches the
    student's unresolved wrong-question set must score strictly higher."""
    base_problem_a = {
        "id": 101,
        "difficulty": "Medium",
        "quality_score": 0.8,
    }
    base_problem_b = {
        "id": 102,
        "difficulty": "Medium",
        "quality_score": 0.8,
    }

    skill_profile = [{"tag_name": "Array", "mastery_score": 60, "attempt_count": 1}]
    weights = {
        "need_match": 0.40,
        "difficulty_fit": 0.20,
        "success_prob": 0.15,
        "novelty": 0.10,
        "quality": 0.10,
        "repeat_penalty": 0.15,
        "wrong_question": 0.10,
    }

    problem_tags_map = {
        101: [{"tag_name": "DP"}],            # matches unresolved
        102: [{"tag_name": "Sorting"}],       # no match
    }
    wrong_question_ctx = {
        "resolved_tag_counts": {},
        "unresolved_tag_counts": {"DP": 1},
        "total_unresolved_count": 1,
    }

    ranked = rank_and_score(
        [base_problem_a, base_problem_b],
        skill_profile,
        None,
        weights,
        problem_tags_map,
        wrong_question_ctx,
    )
    by_id = {item["problem_id"]: item for item in ranked}

    assert by_id[101]["score_total"] > by_id[102]["score_total"]
    assert by_id[101]["score_wrong_question"] > 0.0
    assert by_id[102]["score_wrong_question"] == 0.0


def test_ranking_unchanged_when_context_missing():
    """Regression guard: with no wrong-question context, ranking must behave
    exactly as before (all score_wrong_question = 0)."""
    problem = {"id": 1, "difficulty": "Easy", "quality_score": 0.9}
    skill_profile = [{"tag_name": "Array", "mastery_score": 70, "attempt_count": 2}]
    weights = {
        "need_match": 0.40,
        "difficulty_fit": 0.20,
        "success_prob": 0.15,
        "novelty": 0.10,
        "quality": 0.10,
        "repeat_penalty": 0.15,
        "wrong_question": 0.10,
    }
    problem_tags_map = {1: [{"tag_name": "Array"}]}

    ranked_with_none = rank_and_score(
        [problem], skill_profile, None, weights, problem_tags_map, None
    )
    ranked_with_empty = rank_and_score(
        [problem],
        skill_profile,
        None,
        weights,
        problem_tags_map,
        {
            "resolved_tag_counts": {},
            "unresolved_tag_counts": {},
            "total_unresolved_count": 0,
        },
    )

    assert ranked_with_none[0]["score_wrong_question"] == 0.0
    assert ranked_with_empty[0]["score_wrong_question"] == 0.0
    assert ranked_with_none[0]["score_total"] == ranked_with_empty[0]["score_total"]
