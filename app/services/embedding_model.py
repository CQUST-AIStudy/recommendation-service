"""
向量语义检索 — 方案 C 的核心。

设计原则:
  1. 可选模块:sentence-transformers 没装时优雅降级,方案 A+B 不受影响
  2. 离线预算 embedding,在线只做余弦相似度,延迟可控
  3. 2-3 千题规模用纯 Python 暴力余弦就够,无需 faiss

依赖:
  sentence-transformers (含 PyTorch)
    pip install sentence-transformers
  模型推荐: BAAI/bge-small-zh (中文优秀、200MB 上下)
    或 BAAI/bge-small-en-v1.5 (英文)

存储:
  embedding 存到 leetcode_problem_embedding 表(见 SQL 迁移)
"""
from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# 模型单例(惰性加载,避免没装包时 import 报错)
_MODEL = None
_MODEL_NAME: str | None = None


def is_available() -> bool:
    """检查 sentence-transformers 是否可用。"""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def get_model(model_name: str = "BAAI/bge-small-zh"):
    """惰性加载 SentenceTransformer 单例。"""
    global _MODEL, _MODEL_NAME
    if _MODEL is None or _MODEL_NAME != model_name:
        try:
            from sentence_transformers import SentenceTransformer
            _MODEL = SentenceTransformer(model_name)
            _MODEL_NAME = model_name
            logger.info("Loaded embedding model: %s", model_name)
        except Exception as exc:
            logger.error("Failed to load embedding model %s: %s", model_name, exc)
            _MODEL = None
            raise
    return _MODEL


def encode_texts(texts: list[str], model_name: str = "BAAI/bge-small-zh") -> list[list[float]]:
    """批量编码文本,返回 L2 归一化的向量(list of list of float)。

    返回 Python list 方便存 MySQL BLOB / JSON。
    """
    if not texts:
        return []
    model = get_model(model_name)
    # SentenceTransformer 默认 batch encode,GPU 自动用 GPU
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    # numpy array → list of list
    return [list(map(float, v)) for v in vecs]


def cosine_dense(v1: list[float], v2: list[float]) -> float:
    """两个向量的余弦相似度(已归一化时等价于点积)。"""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    return sum(a * b for a, b in zip(v1, v2))


def cosine_many(query: list[float], vectors: list[list[float]]) -> list[float]:
    """query 对多个向量算余弦。"""
    return [cosine_dense(query, v) for v in vectors]


# ──────────────────────────────────────────
# 序列化 (BLOB / JSON)
# ──────────────────────────────────────────

import json
import struct


def encode_blob(vec: list[float]) -> bytes:
    """list[float] → 紧凑 bytes (little-endian float32)。

    用 struct 比 JSON 小 4-8 倍,2-3 千题 × 384 维约 4MB。
    """
    if not vec:
        return b""
    return struct.pack(f"<{len(vec)}f", *vec)


def decode_blob(blob: bytes) -> list[float]:
    """bytes → list[float]。"""
    if not blob:
        return []
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def encode_json(vec: list[float]) -> str:
    """list[float] → JSON 字符串(便于调试,体积大)。"""
    return json.dumps(vec)


# ──────────────────────────────────────────
# 工厂:从 DB 加载所有题向量
# ──────────────────────────────────────────

def load_all_embeddings() -> dict[int, list[float]]:
    """从 leetcode_problem_embedding 表加载所有题向量。

    Returns
    -------
    dict[problem_id, vector]
        空 dict 表示表不存在或没数据(此时调用方应降级)。
    """
    try:
        from app import db as db_mod
        with db_mod.query() as cur:
            # 用 try 是因为表可能还没建(方案 C 是可选的)
            cur.execute(
                "SELECT problem_id, embedding_blob FROM leetcode_problem_embedding"
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning("Failed to load embeddings (table may not exist): %s", exc)
        return {}

    result: dict[int, list[float]] = {}
    for row in rows:
        blob = row.get("embedding_blob")
        if blob:
            try:
                result[int(row["problem_id"])] = decode_blob(blob)
            except Exception as exc:
                logger.debug("Skip bad embedding for pid=%s: %s", row.get("problem_id"), exc)
    logger.info("Loaded %d problem embeddings", len(result))
    return result


def find_weak_tag_centroids(
    skill_profile: list[dict[str, Any]],
    weak_threshold: float = 60.0,
) -> dict[str, list[float]]:
    """对每个弱项 tag,聚合所有有该 tag 标注的题向量,取平均作为该 tag 的代表向量。

    Returns
    -------
    dict[tag_name, vector]
    """
    embeddings = load_all_embeddings()
    if not embeddings:
        return {}

    from app import db as db_mod
    weak_tags = [
        s["tag_name"] for s in skill_profile
        if s.get("tag_name") and (s.get("mastery_score") or 100) < weak_threshold
    ]
    if not weak_tags:
        return {}

    centroids: dict[str, list[list[float]]] = {tag: [] for tag in weak_tags}
    try:
        with db_mod.query() as cur:
            placeholders = ",".join(["%s"] * len(weak_tags))
            cur.execute(
                f"""SELECT tag_name, problem_id FROM leetcode_problem_tag
                WHERE tag_name IN ({placeholders})""",
                weak_tags,
            )
            rows = cur.fetchall()
    except Exception as exc:
        logger.warning("Failed to load tags for centroids: %s", exc)
        return {}

    for row in rows:
        tag = row["tag_name"]
        vec = embeddings.get(int(row["problem_id"]))
        if vec:
            centroids[tag].append(vec)

    # 平均 → L2 归一化
    result: dict[str, list[float]] = {}
    for tag, vecs in centroids.items():
        if not vecs:
            continue
        dim = len(vecs[0])
        mean = [0.0] * dim
        for v in vecs:
            for i in range(dim):
                mean[i] += v[i]
        n = len(vecs)
        mean = [x / n for x in mean]
        # L2 归一化
        norm = math.sqrt(sum(x * x for x in mean))
        if norm > 0:
            mean = [x / norm for x in mean]
        result[tag] = mean
    return result


def find_semantic_neighbors(
    weak_centroids: dict[str, list[float]],
    candidate_pool: dict[int, list[float]],
    top_k: int = 20,
    min_score: float = 0.30,
) -> list[tuple[int, float]]:
    """对每个弱项 tag 质心,在候选池里找最相似的题。

    Returns
    -------
    list[(problem_id, best_similarity)]
        按相似度降序,最多 top_k 条。
    """
    if not weak_centroids or not candidate_pool:
        return []

    pool_ids = list(candidate_pool.keys())
    pool_vecs = [candidate_pool[pid] for pid in pool_ids]

    scored: dict[int, float] = {}
    for tag, centroid in weak_centroids.items():
        sims = cosine_many(centroid, pool_vecs)
        for pid, sim in zip(pool_ids, sims):
            # 一道题可能同时和多个弱项 tag 接近,取最高分
            if sim > scored.get(pid, 0.0):
                scored[pid] = sim

    # 过滤阈值 + Top-K
    result = [(pid, s) for pid, s in scored.items() if s >= min_score]
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:top_k]
