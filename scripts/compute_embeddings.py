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
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as db_mod
from app.services import embedding_model

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("compute_embeddings")


def ensure_table_exists() -> bool:
    """检查 leetcode_problem_embedding 表是否已建。

    推荐做法是手动执行 sql/V13__create_leetcode_problem_embedding.sql。
    本脚本不做自动建表(避免 schema 管理混乱)。
    """
    try:
        n = db_mod.find_embedding_count()
        return True  # 表存在(可能是 0 行)
    except Exception as exc:
        log.error(
            "leetcode_problem_embedding 表不存在或无法访问: %s\n"
            "请先执行 sql/V13__create_leetcode_problem_embedding.sql", exc,
        )
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="离线计算 leetcode_problem_embedding")
    parser.add_argument(
        "--model", default="BAAI/bge-small-zh",
        help="SentenceTransformer 模型名 (默认 BAAI/bge-small-zh)",
    )
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
    if not ensure_table_exists():
        return 1

    # 3) 加载模型
    try:
        embedding_model.get_model(args.model)
    except Exception:
        log.error("模型加载失败,退出")
        return 1

    # 4) 决定要编码哪些题
    already = db_mod.find_problem_ids_with_embeddings(args.model) if not args.force else set()
    all_problems = db_mod.find_all_problems(args.max_problems)
    todo = [p for p in all_problems if p["id"] not in already]
    log.info(
        "待编码: %d 道 (已编码: %d, 跳过)", len(todo), len(already),
    )

    if not todo:
        log.info("没有需要编码的题,退出")
        return 0

    # 5) 批量编码 + 写库
    n_done = 0
    n_failed = 0
    for batch_start in range(0, len(todo), args.batch_size):
        batch = todo[batch_start:batch_start + args.batch_size]
        texts = [
            (p.get("title_main", "") + " " + (p.get("problem_text", "") or "")).strip()
            for p in batch
        ]

        try:
            vectors = embedding_model.encode_texts(texts, model_name=args.model)
        except Exception as exc:
            log.error("批 %d-%d 编码失败: %s", batch_start, batch_start + len(batch), exc)
            n_failed += len(batch)
            continue

        for p, vec in zip(batch, vectors):
            try:
                blob = embedding_model.encode_blob(vec)
                db_mod.upsert_problem_embedding(p["id"], args.model, len(vec), blob)
                n_done += 1
            except Exception as exc:
                log.warning("写入失败 pid=%s: %s", p["id"], exc)
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
