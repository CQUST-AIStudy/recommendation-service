"""
TF-IDF 自动打标模块 — 方案 B 的核心。

核心思想:
  从已有标签的题(方案 A 已铺好的 tag 表)里聚合每个 tag 的代表性文本,
  对没有标签的题用 TF-IDF + 余弦相似度找最匹配的 tag。

相比方案 A 的关键词词典:
  - 词典命中(强信号) → 仍然走 knowledge_tags
  - 词典没命中但 TF-IDF 相似 → 由本模块补足
  - relevance_score 写入 tag 表后,在线排序无须重算 TF-IDF

依赖:
  scikit-learn 是经典选择。如果环境装不上,可以用纯 Python 实现的 fallback。
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# 简易的分词器:中英混排,英文按空格/标点切,中文按字切。
# 不引入 jieba 是为了不增加依赖;TF-IDF 对粗粒度分词也能学到统计信号。
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]+|[\u4e00-\u9fa5]")


def tokenize(text: str) -> list[str]:
    """粗粒度 tokenizer:英文单词 + 中文单字。

    中文按字切是因为 LeetCode 中文题面里同一个知识点会出现"动态规划""DP""动归"
    多种写法,单字级别的统计更鲁棒。
    """
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


# ──────────────────────────────────────────
# 纯 Python TF-IDF (避免依赖 scikit-learn)
# ──────────────────────────────────────────

class TfidfVectorizer:
    """
    极简 TF-IDF 实现,够用就行。

    - TF: 词频 / 文档总词数
    - IDF: log((1 + N) / (1 + df)) + 1  (sklearn 平滑公式)
    - 输出向量默认 L2 归一化,方便直接算余弦。
    """

    def __init__(self) -> None:
        self.vocabulary_: dict[str, int] = {}
        self.idf_: dict[str, float] = {}

    def fit(self, corpus: list[list[str]]) -> "TfidfVectorizer":
        """corpus 是分好词的 list of token list。"""
        n_docs = len(corpus)
        df: dict[str, int] = Counter()
        for tokens in corpus:
            for tok in set(tokens):
                df[tok] += 1

        # 词表按字典序固定
        self.vocabulary_ = {tok: i for i, tok in enumerate(sorted(df.keys()))}
        # sklearn 的平滑 IDF
        self.idf_ = {
            tok: math.log((1.0 + n_docs) / (1.0 + df_i)) + 1.0
            for tok, df_i in df.items()
        }
        return self

    def transform(self, corpus: list[list[str]]) -> list[dict[int, float]]:
        """返回稀疏表示 (token_idx → tfidf)。已 L2 归一化。"""
        result: list[dict[int, float]] = []
        for tokens in corpus:
            tf = Counter(tokens)
            total = max(1, sum(tf.values()))
            vec: dict[int, float] = {}
            for tok, cnt in tf.items():
                if tok not in self.vocabulary_:
                    continue
                idx = self.vocabulary_[tok]
                vec[idx] = (cnt / total) * self.idf_.get(tok, 0.0)
            # L2 归一化
            norm = math.sqrt(sum(v * v for v in vec.values()))
            if norm > 0:
                vec = {k: v / norm for k, v in vec.items()}
            result.append(vec)
        return result

    def fit_transform(self, corpus: list[list[str]]) -> list[dict[int, float]]:
        return self.fit(corpus).transform(corpus)


def cosine_sparse(v1: dict[int, float], v2: dict[int, float]) -> float:
    """稀疏向量的余弦相似度。两个向量都已 L2 归一化时,等价于点积。"""
    if not v1 or not v2:
        return 0.0
    # 取较短的遍历
    if len(v1) > len(v2):
        v1, v2 = v2, v1
    return sum(val * v2.get(idx, 0.0) for idx, val in v1.items())


# ──────────────────────────────────────────
# Tag 质心 + 对题打分
# ──────────────────────────────────────────

class TagCentroidModel:
    """
    每个知识点 tag 的"质心向量":

      centroid[tag] = mean(tfidf_vec(problem) for problem in problems_with_tag)

    对新题:算 tfidf 向量 → 与每个 tag 质心算余弦 → 取 Top-K。
    """

    def __init__(self) -> None:
        self.vectorizer_: TfidfVectorizer = TfidfVectorizer()
        self.centroids_: dict[str, dict[int, float]] = {}
        self.tag_categories_: dict[str, str] = {}

    def fit(
        self,
        problems: list[dict[str, Any]],
        problem_tag_map: dict[int, list[dict[str, Any]]],
    ) -> "TagCentroidModel":
        """
        Parameters
        ----------
        problems : list[dict]
            题目列表(必须有 title_main / problem_text / solution_text)。
        problem_tag_map : dict[int, list[dict]]
            每个题的 tag 列表 {problem_id: [{tag_name, tag_category, relevance_score}, ...]}
        """
        # 1. 拼文档
        docs: list[list[str]] = []
        pid_to_doc_idx: dict[int, int] = {}
        for p in problems:
            pid = p["id"]
            title = p.get("title_main", "") or ""
            body = (p.get("problem_text", "") or "") + " " + (p.get("solution_text", "") or "")
            docs.append(tokenize(title + " " + body))
            pid_to_doc_idx[pid] = len(docs) - 1

        if not docs:
            logger.warning("TF-IDF model fit with empty corpus")
            return self

        # 2. fit vectorizer
        self.vectorizer_.fit(docs)
        doc_vecs = self.vectorizer_.transform(docs)

        # 3. 算每个 tag 的质心 (relevance_score 加权)
        from collections import defaultdict
        sums: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        weights: dict[str, float] = defaultdict(float)
        cats: dict[str, str] = {}

        for pid, tags in problem_tag_map.items():
            doc_idx = pid_to_doc_idx.get(pid)
            if doc_idx is None:
                continue
            vec = doc_vecs[doc_idx]
            for t in tags:
                tag_name = t.get("tag_name")
                if not tag_name:
                    continue
                w = float(t.get("relevance_score") or 1.0)
                cats[tag_name] = t.get("tag_category", "algorithm")
                for idx, val in vec.items():
                    sums[tag_name][idx] += w * val
                weights[tag_name] += w

        # 归一化为单位向量(余弦相似度要求)
        for tag_name, vec in sums.items():
            norm = math.sqrt(sum(v * v for v in vec.values()))
            if norm > 0:
                self.centroids_[tag_name] = {k: v / norm for k, v in vec.items()}
            else:
                self.centroids_[tag_name] = {}
            self.tag_categories_[tag_name] = cats.get(tag_name, "algorithm")

        logger.info(
            "TF-IDF fit done: %d problems, %d tags with centroid",
            len(docs), len(self.centroids_),
        )
        return self

    def score(
        self,
        title: str,
        body: str,
        top_k: int = 5,
        min_score: float = 0.05,
    ) -> list[tuple[str, float, str]]:
        """
        对一道新题打分,返回 Top-K tag。

        Returns
        -------
        list[(tag_name, cosine_score, category)]
            按余弦相似度降序。
        """
        tokens = tokenize((title or "") + " " + (body or ""))
        if not tokens:
            return []
        vec = self.vectorizer_.transform([tokens])[0]
        if not vec:
            return []

        scored: list[tuple[str, float, str]] = []
        for tag_name, centroid in self.centroids_.items():
            sim = cosine_sparse(vec, centroid)
            if sim >= min_score:
                scored.append((tag_name, sim, self.tag_categories_.get(tag_name, "algorithm")))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


# ──────────────────────────────────────────
# 工厂:从 DB 加载已标注数据训练模型
# ──────────────────────────────────────────

def build_model_from_db(max_problems: int = 5000) -> TagCentroidModel:
    """从 leetcode_problem_bank + leetcode_problem_tag 表训练模型。

    用于离线脚本(通常是 backfill 脚本调用)。
    """
    from app import db as db_mod

    problems = db_mod.find_all_problems(max_problems)
    if not problems:
        logger.warning("No problems in DB; TF-IDF model empty")
        return TagCentroidModel()

    problem_tag_map: dict[int, list[dict[str, Any]]] = {}
    for p in problems:
        try:
            tags = db_mod.find_tags_for_problem(p["id"])
            if tags:
                problem_tag_map[p["id"]] = tags
        except Exception as exc:
            logger.debug("Skip tags for problem %s: %s", p["id"], exc)

    if not problem_tag_map:
        logger.warning("No tags found in DB; TF-IDF model empty")
        return TagCentroidModel()

    model = TagCentroidModel()
    model.fit(problems, problem_tag_map)
    return model
