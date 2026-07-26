from app import db as db_mod
from app.services.wrong_question_features import (
    load_pta_error_context,
)


def test_pta_error_context_exposes_knowledge_metadata(monkeypatch):
    monkeypatch.setattr(
        db_mod,
        "find_pta_high_frequency_errors",
        lambda student_id, min_errors: [
            {
                "problem_id": 9,
                "error_count": 3,
                "problem_title": "二叉树遍历",
                "problem_no": "7-2",
                "source_problem_id": "pta-9",
                "offering_id": 4,
                "offering_title": "树结构实验",
                "pta_problem_set_id": "123456",
                "knowledge_point": "二叉树遍历",
                "knowledge_path": "数据结构/树/二叉树遍历",
                "difficulty_label": "中等",
            }
        ],
    )
    monkeypatch.setattr(db_mod, "find_pta_tag_mappings", lambda: [])

    context = load_pta_error_context(12, min_errors=1)

    assert context["pta_items"][0]["knowledge_point"] == "二叉树遍历"
    assert context["pta_items"][0]["knowledge_path"] == "数据结构/树/二叉树遍历"
    assert context["pta_items"][0]["difficulty_label"] == "中等"
    assert context["pta_items"][0]["problem_no"] == "7-2"
    assert context["pta_items"][0]["pta_problem_set_id"] == "123456"
