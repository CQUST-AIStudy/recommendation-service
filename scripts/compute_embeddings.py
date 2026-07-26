"""
离线计算题向量脚本 — 方案 C 的最后一块拼图。

作用:
  对 leetcode_problem_bank 里所有题,用 sentence-transformers 模型算 embedding,
  写入 leetcode_problem_embedding 表。

前置条件:
  pip install sentence-transformers
  首次运行会下载模型(约 100-200MB,缓存到 ~/.cache/huggingface)

运行方式:
    cd recommendation-service
    uv run pip install sentence-transformers   # 一次性
    uv run python scripts/compute_embeddings.py
    uv run python scripts/compute_embeddings.py --model BAAI/bge-small-en-v1.5
    uv run python scripts/compute_embeddings.py --batch-size 32

跑完后:
  - 在线 recall_by_semantic 会自动启用 (第六路召回)
  - 没装 sentence-transformers 时,recall_by_semantic 自动降级到空
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as db_mod
from app.core.config import get_settings
from app.services import embedding_model

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("compute_embeddings")


def ensure_table_exists(
    model_name: str,
    model_revision: str,
    preprocessing_version: str,
    expected_dim: int,
) -> bool:
    """检查 leetcode_problem_embedding 表是否已建。

    推荐做法是手动执行 sql/V13__create_leetcode_problem_embedding.sql。
    本脚本不做自动建表(避免 schema 管理混乱)。
    """
    try:
        db_mod.find_embedding_count(
            model_name, model_revision, preprocessing_version, expected_dim,
        )
        return True  # 表存在(可能是 0 行)
    except Exception as exc:
        log.error(
            "leetcode_problem_embedding 表不存在或无法访问: %s\n"
            "请先执行 sql/V13__create_leetcode_problem_embedding.sql", exc,
        )
        return False


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="离线计算 leetcode_problem_embedding")
    parser.add_argument(
        "--model", default=settings.embedding_model_name,
        help="SentenceTransformer 模型名",
    )
    parser.add_argument("--revision", default=settings.embedding_model_revision, help="模型 revision")
    parser.add_argument(
        "--preprocessing-version",
        default=settings.embedding_preprocessing_version,
        help="输入文本预处理版本",
    )
    parser.add_argument("--expected-dim", type=int, default=settings.embedding_expected_dim)
    parser.add_argument("--batch-size", type=int, default=16, help="批大小")
    parser.add_argument("--max-problems", type=int, default=5000, help="最多编码多少题")
    parser.add_argument("--force", action="store_true", help="强制重算已编码的题")
    args = parser.parse_args()

    # 1) 检查依赖
    if not embedding_model.is_available():
        log.error(
            "sentence-transformers 未安装,无法运行方案 C。\n"
            "请先执行: pip install sentence-transformers"
        )
        return 1

    # 2) 检查表
    if not ensure_table_exists(
        args.model, args.revision, args.preprocessing_version, args.expected_dim,
    ):
        return 1

    # 3) 决定要编码哪些题
    all_problems = db_mod.find_all_problems(args.max_problems)
    existing_hashes = {} if args.force else db_mod.find_problem_embedding_hashes(
        args.model, args.revision, args.preprocessing_version,
    )

    def build_text(problem: dict) -> str:
        return " ".join(filter(None, [
            str(problem.get("title_main") or "").strip(),
            str(problem.get("title_alt") or "").strip(),
            str(problem.get("problem_text") or "").strip(),
        ]))

    def content_hash(text: str) -> str:
        payload = f"{args.preprocessing_version}\0{text}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    prepared = [(problem, build_text(problem)) for problem in all_problems]
    todo = [
        (problem, text, content_hash(text))
        for problem, text in prepared
        if existing_hashes.get(int(problem["id"])) != content_hash(text)
    ]
    log.info(
        "待编码: %d 道 (已有同版本向量: %d)", len(todo), len(existing_hashes),
    )

    if not todo:
        log.info("没有需要编码的题,退出")
        return 0

    # 4) 仅在确实需要编码时加载模型
    try:
        embedding_model.get_model(args.model, args.revision)
    except Exception:
        log.error("模型加载失败,退出")
        return 1

    # 5) 批量编码 + 写库
    n_done = 0
    n_failed = 0
    for batch_start in range(0, len(todo), args.batch_size):
        batch = todo[batch_start:batch_start + args.batch_size]
        texts = [text for _, text, _ in batch]

        try:
            vectors = embedding_model.encode_texts(
                texts, model_name=args.model, revision=args.revision,
            )
        except Exception as exc:
            log.error("批 %d-%d 编码失败: %s", batch_start, batch_start + len(batch), exc)
            n_failed += len(batch)
            continue

        for (problem, _, digest), vec in zip(batch, vectors):
            try:
                if len(vec) != args.expected_dim:
                    raise ValueError(
                        f"embedding dim {len(vec)} does not match expected {args.expected_dim}"
                    )
                blob = embedding_model.encode_blob(vec)
                db_mod.upsert_problem_embedding(
                    problem["id"], args.model, args.revision,
                    args.preprocessing_version, digest, len(vec), blob,
                )
                n_done += 1
            except Exception as exc:
                log.warning("写入失败 pid=%s: %s", problem["id"], exc)
                n_failed += 1

        log.info(
            "进度 %d/%d (done=%d failed=%d)",
            min(batch_start + args.batch_size, len(todo)), len(todo),
            n_done, n_failed,
        )

    log.info("完成: encoded=%d failed=%d", n_done, n_failed)
    return 0 if n_failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
