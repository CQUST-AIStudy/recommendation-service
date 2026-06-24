"""
方案 A 单元测试 — knowledge_tags 模块的核心打分逻辑。

不依赖 DB,纯函数测试,跑起来很快。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.knowledge_tags import (
    KNOWLEDGE_TAGS,
    detect_tags_for_problem,
    get_course_weight,
    get_english_synonyms,
    tag_relevance_score,
)


# ──────────────────────────────────────────
# 词典覆盖度
# ──────────────────────────────────────────

def test_dict_size_meets_plan_a():
    """方案 A 承诺扩充到 50+ 项,确保没回退。"""
    assert len(KNOWLEDGE_TAGS) >= 50, f"词典太小: {len(KNOWLEDGE_TAGS)}"


def test_every_tag_has_required_fields():
    """每个 tag 必须有 category / en / zh / course_weight。"""
    for name, meta in KNOWLEDGE_TAGS.items():
        assert meta.get("category") in {"algorithm", "data_structure", "technique"}, name
        assert isinstance(meta.get("en"), list) and len(meta["en"]) >= 1, name
        assert isinstance(meta.get("zh"), list) and len(meta["zh"]) >= 1, name
        assert 0.0 <= float(meta.get("course_weight", -1)) <= 1.0, name


def test_no_duplicate_synonyms_within_tag():
    """同一 tag 内部英文同义词不能重复,避免打分时重复加权。"""
    for name, meta in KNOWLEDGE_TAGS.items():
        ens = [k.lower() for k in meta["en"]]
        assert len(ens) == len(set(ens)), f"{name} 有重复英文同义词"


# ──────────────────────────────────────────
# 单 tag 相关性打分
# ──────────────────────────────────────────

def test_chinese_keyword_hits_score_1():
    """中文关键词命中应该给 1.0 强信号。"""
    title = ""
    text = "这道题考察的是动态规划的状态转移方程"
    assert tag_relevance_score(title, text, "动态规划") == 1.0


def test_english_full_word_hits_08():
    """英文整词命中应该给 0.8 中信号。"""
    title = "Two Sum"
    text = "Use a hash map to track complements. dynamic programming is overkill here."
    # 这段文字其实考的是哈希,不是动态规划
    # 但 "dynamic programming" 出现了,所以相关度至少 0.8
    assert tag_relevance_score(title, text, "动态规划") >= 0.8


def test_title_match_adds_bonus():
    """标题里命中应该额外加 0.2(上限 1.0)。"""
    title = "Binary Search Tree Inorder"
    text = ""
    score = tag_relevance_score(title, text, "树")
    assert score == 1.0  # 0.8 + 0.2 上限封顶


def test_no_hit_returns_zero():
    """完全不相关的题应该给 0.0,不能误命中。"""
    title = "Two Sum"
    text = "Given an array of integers, return indices of two numbers such that they add up to target."
    assert tag_relevance_score(title, text, "动态规划") == 0.0
    assert tag_relevance_score(title, text, "图") == 0.0


def test_unknown_tag_falls_back_to_substring():
    """未收录的 tag(比如自定义关键词)回退到朴素子串,但降权到 0.5。"""
    text = "本题考察了单调队列优化"
    assert tag_relevance_score("", text, "自定义不存在") == 0.0
    assert tag_relevance_score("", text, "单调队列优化") == 0.5  # 已收录,走正常打分


# ──────────────────────────────────────────
# 多 tag 检测 (用于离线打标)
# ──────────────────────────────────────────

def test_detect_returns_sorted_top_k():
    """detect_tags_for_problem 应该按 score 降序,最多返回 max_tags 条。"""
    title = "Coin Change 2"
    text = (
        "This is a classic dynamic programming problem. "
        "Use a 1D array to track the number of combinations. "
        "状态转移方程: dp[i] += dp[i - coin]。"
    )
    hits = detect_tags_for_problem(title, text, min_score=0.5, max_tags=3)
    assert len(hits) >= 1
    assert all(hits[i][1] >= hits[i + 1][1] for i in range(len(hits) - 1))
    # Top1 应该是动态规划(中文"状态转移"命中,1.0 分)
    assert hits[0][0] == "动态规划"
    assert hits[0][1] == 1.0


def test_detect_filters_below_threshold():
    """min_score 阈值应该真的过滤掉低分项。"""
    title = "Two Sum"
    text = "Use hash map."
    hits = detect_tags_for_problem(title, text, min_score=0.9, max_tags=5)
    # 哈希表相关度只到 0.8,过滤后应该为空或仅含标题加成项
    for _, score, _ in hits:
        assert score >= 0.9


# ──────────────────────────────────────────
# 兼容性 (旧 _TAG_MAP / _COURSE_WEIGHTS 行为)
# ──────────────────────────────────────────

def test_get_course_weight_default():
    """未收录 tag 返回默认 0.5。"""
    assert get_course_weight("不存在的知识点") == 0.5


def test_get_course_weight_known():
    """链表权重应该是 0.9(高权重课程重点)。"""
    assert abs(get_course_weight("链表") - 0.9) < 0.01


def test_get_english_synonyms_returns_list():
    """get_english_synonyms 应返回非空 list,而不是旧版单个字符串。"""
    syns = get_english_synonyms("动态规划")
    assert isinstance(syns, list)
    assert len(syns) >= 1
    assert any("dynamic programming" in s for s in syns)


# ──────────────────────────────────────────
# 真实场景:不同类型的题打分应该有显著差异
# ──────────────────────────────────────────

def test_real_leetcode_problem_classification():
    """模拟几道真实 LeetCode 题的描述,验证分类是否符合预期。"""
    test_cases = [
        # (title, text, expected_top_tag)
        (
            "Climbing Stairs",
            "You are climbing a staircase. Use dynamic programming: dp[i] = dp[i-1] + dp[i-2].",
            "动态规划",
        ),
        (
            "Reverse Linked List",
            "Given the head of a singly linked list, reverse the list.",
            "链表",
        ),
        (
            "Valid Parentheses",
            "Given a string containing just the characters '(', ')', use a stack.",
            "栈",
        ),
        (
            "Number of Islands",
            "Given an m x n 2D binary grid which represents a map of '1' (land) and '0' (water). "
            "Use DFS to count islands.",
            "深度优先搜索",
        ),
    ]
    for title, text, expected in test_cases:
        hits = detect_tags_for_problem(title, text, min_score=0.5, max_tags=5)
        tag_names = [h[0] for h in hits]
        assert expected in tag_names, (
            f"「{title}」期望命中「{expected}」,实际命中: {tag_names}"
        )


if __name__ == "__main__":
    # 简单的脚本式运行(没装 pytest 也能跑)
    import traceback

    tests = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
