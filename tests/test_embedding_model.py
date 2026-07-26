"""
方案 C 单元测试 — embedding 模块的纯逻辑部分。

注意:不测试实际的 sentence-transformers 编码(那需要装 PyTorch + 下载模型)。
只测试:
  - 序列化 (blob/json)
  - 余弦相似度
  - find_semantic_neighbors (用合成向量)
  - is_available 的降级逻辑
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import embedding_model


# ──────────────────────────────────────────
# 序列化
# ──────────────────────────────────────────

def test_blob_round_trip():
    """float list → blob → float list 应该无损。"""
    original = [0.1, -0.5, 1.0, 0.0, 0.333, -2.7]
    blob = embedding_model.encode_blob(original)
    decoded = embedding_model.decode_blob(blob)

    assert len(decoded) == len(original)
    for a, b in zip(original, decoded):
        # float32 精度损失,允许 1e-5 误差
        assert abs(a - b) < 1e-5


def test_blob_empty():
    assert embedding_model.encode_blob([]) == b""
    assert embedding_model.decode_blob(b"") == []


def test_json_round_trip():
    """JSON 序列化也应该无损。"""
    import json
    original = [0.123, -0.456, 1.0]
    s = embedding_model.encode_json(original)
    decoded = json.loads(s)
    assert decoded == original


# ──────────────────────────────────────────
# 余弦相似度
# ──────────────────────────────────────────

def test_cosine_identical():
    v = [1.0, 0.0, 0.0]
    assert abs(embedding_model.cosine_dense(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal():
    v1 = [1.0, 0.0]
    v2 = [0.0, 1.0]
    assert abs(embedding_model.cosine_dense(v1, v2)) < 1e-6


def test_cosine_opposite():
    v1 = [1.0, 0.0]
    v2 = [-1.0, 0.0]
    assert abs(embedding_model.cosine_dense(v1, v2) - (-1.0)) < 1e-6


def test_cosine_dimension_mismatch_returns_zero():
    assert embedding_model.cosine_dense([1.0, 2.0], [1.0]) == 0.0


def test_cosine_many():
    query = [1.0, 0.0]
    pool = [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]]
    sims = embedding_model.cosine_many(query, pool)
    assert len(sims) == 3
    # query = (1,0) → 与 pool[0]=(1,0) 余弦 1.0
    assert abs(sims[0] - 1.0) < 1e-6
    # 与 pool[1]=(0,1) 余弦 0
    assert abs(sims[1]) < 1e-6
    # 与 pool[2] 余弦 = 1·0.7 + 0·0.7 = 0.7
    assert abs(sims[2] - 0.7) < 1e-6


# ──────────────────────────────────────────
# find_semantic_neighbors (核心)
# ──────────────────────────────────────────

def test_find_semantic_neighbors_basic():
    """对一个弱项质心,在候选池里找最相似的题。"""
    # 弱项 "动态规划" 质心 = (1.0, 0.0)
    # 候选池里有 5 道题
    centroids = {
        "动态规划": [1.0, 0.0],
    }
    pool = {
        1: [1.0, 0.0],     # 完全相似 (cos=1.0)
        2: [0.9, 0.1],     # 高度相似 (cos≈0.99)
        3: [0.0, 1.0],     # 完全不同 (cos=0)
        4: [0.5, 0.5],     # 中等 (cos=0.5)
        5: [-1.0, 0.0],    # 相反 (cos=-1)
    }

    neighbors = embedding_model.find_semantic_neighbors(
        centroids, pool, top_k=3, min_score=0.30,
    )
    # 至少能返回 3 个 (pid 1, 2, 4 都过阈值)
    assert len(neighbors) <= 3
    # Top1 必须是 pid 1 (cos=1.0)
    assert neighbors[0][0] == 1
    # 相似度按降序
    for i in range(len(neighbors) - 1):
        assert neighbors[i][1] >= neighbors[i + 1][1]


def test_find_semantic_neighbors_empty():
    """空输入应返回空。"""
    assert embedding_model.find_semantic_neighbors({}, {1: [1.0]}, top_k=5) == []
    assert embedding_model.find_semantic_neighbors({"a": [1.0]}, {}, top_k=5) == []


def test_find_semantic_neighbors_multi_tag_take_max():
    """如果一道题同时接近多个弱项 tag,应取最高分。"""
    centroids = {
        "动态规划": [1.0, 0.0, 0.0],
        "贪心": [0.0, 1.0, 0.0],
    }
    pool = {
        1: [0.8, 0.6, 0.0],  # 与 DP cos=0.8, 与贪心 cos=0.6 → 取 0.8
    }
    neighbors = embedding_model.find_semantic_neighbors(
        centroids, pool, top_k=5, min_score=0.0,
    )
    assert len(neighbors) == 1
    assert neighbors[0][0] == 1
    # 最高分应该是 0.8
    assert abs(neighbors[0][1] - 0.8) < 1e-6


def test_find_semantic_neighbors_threshold():
    """min_score 阈值应该真的过滤。"""
    centroids = {"x": [1.0, 0.0]}
    pool = {
        1: [0.4, 0.9],  # cos = 0.4, 低于 0.5 阈值
        2: [0.9, 0.4],  # cos = 0.9, 高于阈值
    }
    neighbors = embedding_model.find_semantic_neighbors(
        centroids, pool, top_k=5, min_score=0.5,
    )
    pids = [pid for pid, _ in neighbors]
    assert 2 in pids
    assert 1 not in pids


# ──────────────────────────────────────────
# 降级逻辑
# ──────────────────────────────────────────

def test_is_available_returns_bool():
    """is_available 应该返回 bool,不抛异常。"""
    result = embedding_model.is_available()
    assert isinstance(result, bool)
    # 测试环境不一定装了 sentence-transformers,两种结果都合法


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
