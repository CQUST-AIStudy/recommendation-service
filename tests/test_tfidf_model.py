"""
方案 B 单元测试 — TF-IDF 模型。

用合成数据验证:在已标注的训练集上,模型能把同知识点的题聚到一起,
对没标签的新题能给出合理的 Top-K 预测。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.tfidf_model import (
    TagCentroidModel,
    TfidfVectorizer,
    cosine_sparse,
    tokenize,
)


# ──────────────────────────────────────────
# 分词器
# ──────────────────────────────────────────

def test_tokenize_handles_mixed_lang():
    """中英混排的文本应该能正确切出 token。"""
    tokens = tokenize("Use dynamic programming. 状态转移方程 dp[i]")
    # 英文 token
    assert "use" in tokens
    assert "dynamic" in tokens
    assert "programming" in tokens
    assert "dp" in tokens
    # 中文单字
    assert "状" in tokens
    assert "态" in tokens
    assert "转" in tokens
    assert "移" in tokens


def test_tokenize_empty():
    assert tokenize("") == []
    assert tokenize(None) == []  # type: ignore[arg-type]


# ──────────────────────────────────────────
# 向量器
# ──────────────────────────────────────────

def test_vectorizer_fit_transform():
    """fit_transform 后,每个文档的向量应该非空且 L2 归一化。"""
    import math

    corpus = [
        ["dp", "dp", "dp", "coin"],
        ["tree", "inorder", "bst"],
        ["dp", "memoization"],
    ]
    vec = TfidfVectorizer().fit_transform(corpus)
    assert len(vec) == 3
    for v in vec:
        if v:
            norm = math.sqrt(sum(x * x for x in v.values()))
            assert 0.99 < norm < 1.01, f"Vector not L2-normalized: norm={norm}"


def test_vectorizer_unknown_token_ignored():
    """词表外的 token 应该被忽略,不出现在向量里。"""
    corpus = [["apple"], ["apple", "banana"]]
    vec = TfidfVectorizer()
    vec.fit(corpus)
    # "cherry" 不在词表里
    out = vec.transform([["apple", "cherry"]])[0]
    # 只有 apple 在词表,所以向量里应该只有 1 个 entry(对应 apple)
    assert len(out) <= 1


# ──────────────────────────────────────────
# 余弦相似度
# ──────────────────────────────────────────

def test_cosine_identical_vectors():
    """两个相同向量的余弦应该是 1.0。"""
    v = {0: 0.6, 1: 0.8}
    assert abs(cosine_sparse(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_vectors():
    """正交向量(无重叠维度)的余弦应该是 0。"""
    v1 = {0: 1.0}
    v2 = {1: 1.0}
    assert cosine_sparse(v1, v2) == 0.0


def test_cosine_empty():
    assert cosine_sparse({}, {0: 1.0}) == 0.0
    assert cosine_sparse(None, None) == 0.0  # type: ignore[arg-type]


# ──────────────────────────────────────────
# Tag 质心模型 (核心)
# ──────────────────────────────────────────

def _make_synthetic_dataset():
    """构造一个最小数据集:3 道动态规划题,3 道树题,1 道未知题。"""
    problems = [
        {"id": 1, "title_main": "Coin Change",
         "problem_text": "dp coin combination state transition", "solution_text": ""},
        {"id": 2, "title_main": "Climbing Stairs",
         "problem_text": "dp memoization optimal substructure", "solution_text": ""},
        {"id": 3, "title_main": "Longest Common Subsequence",
         "problem_text": "dp lcs state transition dynamic", "solution_text": ""},
        {"id": 4, "title_main": "Binary Tree Inorder",
         "problem_text": "tree traversal inorder bst", "solution_text": ""},
        {"id": 5, "title_main": "Validate BST",
         "problem_text": "tree binary search bst", "solution_text": ""},
        {"id": 6, "title_main": "Invert Binary Tree",
         "problem_text": "tree treenode swap children", "solution_text": ""},
        {"id": 7, "title_main": "Mystery Problem",  # 待分类
         "problem_text": "dp memoization optimal", "solution_text": ""},
        {"id": 8, "title_main": "Another Tree Question",  # 待分类
         "problem_text": "tree traversal recursive", "solution_text": ""},
    ]
    tag_map = {
        1: [{"tag_name": "动态规划", "tag_category": "algorithm", "relevance_score": 1.0}],
        2: [{"tag_name": "动态规划", "tag_category": "algorithm", "relevance_score": 1.0}],
        3: [{"tag_name": "动态规划", "tag_category": "algorithm", "relevance_score": 1.0}],
        4: [{"tag_name": "树", "tag_category": "data_structure", "relevance_score": 1.0}],
        5: [{"tag_name": "树", "tag_category": "data_structure", "relevance_score": 1.0}],
        6: [{"tag_name": "树", "tag_category": "data_structure", "relevance_score": 1.0}],
        # 7 和 8 故意不打标签
    }
    return problems, tag_map


def test_centroid_model_fit():
    """模型 fit 后应该有 2 个 tag 质心。"""
    problems, tag_map = _make_synthetic_dataset()
    train_problems = [p for p in problems if p["id"] in tag_map]
    model = TagCentroidModel()
    model.fit(train_problems, tag_map)
    assert len(model.centroids_) == 2
    assert "动态规划" in model.centroids_
    assert "树" in model.centroids_


def test_centroid_model_classifies_dp_problem():
    """对一道 DP 风格的新题,Top1 应该是动态规划。"""
    problems, tag_map = _make_synthetic_dataset()
    train_problems = [p for p in problems if p["id"] in tag_map]
    model = TagCentroidModel()
    model.fit(train_problems, tag_map)

    # problem 7 是 DP 风格,但没打过标签
    test_p = problems[6]  # id=7
    hits = model.score(test_p["title_main"], test_p["problem_text"], top_k=2, min_score=0.0)
    assert len(hits) >= 1
    assert hits[0][0] == "动态规划", f"Expected 动态规划, got {[h[0] for h in hits]}"


def test_centroid_model_classifies_tree_problem():
    """对一道树风格的新题,Top1 应该是树。"""
    problems, tag_map = _make_synthetic_dataset()
    train_problems = [p for p in problems if p["id"] in tag_map]
    model = TagCentroidModel()
    model.fit(train_problems, tag_map)

    # problem 8 是树风格,但没打过标签
    test_p = problems[7]  # id=8
    hits = model.score(test_p["title_main"], test_p["problem_text"], top_k=2, min_score=0.0)
    assert len(hits) >= 1
    assert hits[0][0] == "树", f"Expected 树, got {[h[0] for h in hits]}"


def test_centroid_model_handles_empty_corpus():
    """空数据训练不应崩溃,但模型用不了。"""
    model = TagCentroidModel()
    model.fit([], {})
    assert len(model.centroids_) == 0
    hits = model.score("anything", "any text", top_k=3, min_score=0.0)
    assert hits == []


def test_centroid_model_returns_category():
    """score 返回的 tuple 第三项应该是 category。"""
    problems, tag_map = _make_synthetic_dataset()
    train_problems = [p for p in problems if p["id"] in tag_map]
    model = TagCentroidModel()
    model.fit(train_problems, tag_map)

    hits = model.score("dp state", "transition memoization", top_k=2, min_score=0.0)
    if hits:
        # category 必须是合法枚举
        for _, _, cat in hits:
            assert cat in {"algorithm", "data_structure", "technique"}


if __name__ == "__main__":
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
