"""
离线打标脚本 — 方案 A + 方案 B 合体的最后一块拼图。

作用:
  对 leetcode_problem_bank 里所有"完全没标签"或"标签 relevance_score 全是 1.0"
  的题,分两阶段自动打标:
    阶段 1 (方案 A): knowledge_tags 词典 + 加权打分,强信号优先
    阶段 2 (方案 B): TF-IDF 质心模型,补足阶段 1 没覆盖到的题
  最后写入 leetcode_problem_tag 表。

运行方式:
    cd recommendation-service
    uv run python scripts/backfill_problem_tags.py                # 只补全没标签的题
    uv run python scripts/backfill_problem_tags.py --rescore      # 强制重打所有题的 relevance_score
    uv run python scripts/backfill_problem_tags.py --dry-run      # 只打印,不写库
    uv run python scripts/backfill_problem_tags.py --tfidf-only   # 只跑 TF-IDF 二次召回

跑完后:
  - 在线 _find_by_tags 走 tag 表查询时,能命中更多题
  - compute_need_match 的 r_i 从固定 1.0 变成真实相关度
  - generate_reason_text 能说"本题主要考查「动态规划」(相关度 0.92)"
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 让脚本不安装 package 也能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db as db_mod
from app.services.knowledge_tags import detect_tags_for_problem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("backfill_tags")


def _iter_problems(rescore: bool) -> list[dict]:
    """
    选择处理范围:
      - rescore=False: 只处理完全没标签的题
      - rescore=True : 处理所有题,重打 relevance_score
    """
    if rescore:
        return db_mod.find_all_problems(5000)
    return db_mod.find_problems_without_tags(5000)


def _phase_a_dict_match(p: dict, min_score: float, max_tags: int) -> list[tuple[str, float, str]]:
    """方案 A:词典匹配。"""
    title = p.get("title_main", "") or ""
    body = (p.get("problem_text", "") or "") + " " + (p.get("solution_text", "") or "")
    return detect_tags_for_problem(title, body, min_score=min_score, max_tags=max_tags)


def _phase_b_tfidf(
    problems: list[dict],
    min_score: float,
    max_tags: int,
) -> dict[int, list[tuple[str, float, str]]]:
    """方案 B:TF-IDF 二次召回。返回 {problem_id: [tag candidates]}。

    只对方案 A 没打上标签的题做(其他题已经在 A 阶段处理过)。
    """
    from app.services.tfidf_model import build_model_from_db

    # 先用 DB 里现有的标签数据训练模型
    log.info("训练 TF-IDF 模型(从已有 tag 数据)...")
    model = build_model_from_db(max_problems=5000)
    if not model.centroids_:
        log.warning("TF-IDF 模型为空(可能没有已标注题),跳过阶段 B")
        return {}

    log.info("TF-IDF 模型就绪: %d 个 tag 质心", len(model.centroids_))
    results: dict[int, list[tuple[str, float, str]]] = {}
    for p in problems:
        title = p.get("title_main", "") or ""
        body = (p.get("problem_text", "") or "") + " " + (p.get("solution_text", "") or "")
        # TF-IDF 分数通常 < 0.5,min_score 阈值要低
        hits = model.score(title, body, top_k=max_tags, min_score=min_score)
        if hits:
            results[p["id"]] = hits
    return results


def backfill(
    dry_run: bool,
    rescore: bool,
    min_score: float,
    max_tags: int,
    tfidf_only: bool,
    tfidf_min_score: float,
) -> dict:
    """返回统计 {scanned, tagged_phase_a, tagged_phase_b, skipped}。"""
    problems = _iter_problems(rescore)
    log.info("待处理题目数: %d (rescore=%s)", len(problems), rescore)

    # ───── 阶段 B:TF-IDF 二次召回 (先算结果,后面和 A 合并) ─────
    tfidf_results: dict[int, list[tuple[str, float, str]]] = {}
    if not tfidf_only:
        # A 阶段会覆盖的题就跳过 TF-IDF
        # TF-IDF 只补 A 没覆盖到的题
        pass  # 实际逻辑下面再分流

    # 第一遍:跑 A
    phase_b_candidates: list[dict] = []  # A 没命中的题,留给 B
    scanned = 0
    tagged_a = 0
    tagged_b = 0
    skipped = 0

    if not tfidf_only:
        for p in problems:
            scanned += 1
            pid = p["id"]
            candidates = _phase_a_dict_match(p, min_score, max_tags)
            if candidates:
                tagged_a += _write_tags(pid, candidates, dry_run)
            else:
                phase_b_candidates.append(p)

            if scanned % 100 == 0:
                log.info(
                    "Phase A 进度 %d/%d (tagged_a=%d 留给B=%d)",
                    scanned, len(problems), tagged_a, len(phase_b_candidates),
                )
    else:
        phase_b_candidates = problems
        scanned = len(problems)

    # 第二遍:对 A 没命中的题,跑 TF-IDF
    if phase_b_candidates:
        log.info("阶段 B:对 %d 道方案 A 没覆盖的题跑 TF-IDF", len(phase_b_candidates))
        tfidf_results = _phase_b_tfidf(phase_b_candidates, tfidf_min_score, max_tags)
        for p in phase_b_candidates:
            pid = p["id"]
            hits = tfidf_results.get(pid, [])
            if hits:
                tagged_b += _write_tags(pid, hits, dry_run)
            else:
                skipped += 1

    return {
        "scanned": scanned,
        "tagged_phase_a": tagged_a,
        "tagged_phase_b": tagged_b,
        "skipped": skipped,
    }


def _write_tags(pid: int, candidates: list[tuple[str, float, str]], dry_run: bool) -> int:
    """写入 tag 表(或 dry run 打印),返回写入数。"""
    n_written = 0
    title_preview = ""
    if dry_run:
        try:
            problem = db_mod.find_problem_by_id(pid)
            title_preview = (problem.get("title_main", "") if problem else "")[:30]
        except Exception:
            pass

    for idx, (tag_name, score, category) in enumerate(candidates):
        is_primary = idx == 0
        if dry_run:
            log.info(
                "[DRY] pid=%s title=%r → %s (%.3f, %s, primary=%s)",
                pid, title_preview, tag_name, score, category, is_primary,
            )
        else:
            try:
                db_mod.upsert_problem_tag(pid, tag_name, category, score, is_primary)
                n_written += 1
            except Exception as exc:
                log.warning("Upsert tag failed pid=%s tag=%s: %s", pid, tag_name, exc)
    return max(1, n_written) if candidates else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="离线补全 leetcode_problem_tag (方案 A+B)")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写库")
    parser.add_argument("--rescore", action="store_true", help="重打所有题的 relevance_score")
    parser.add_argument("--min-score", type=float, default=0.5, help="阶段 A 相关度阈值")
    parser.add_argument("--max-tags", type=int, default=5, help="单题最多打几个标签")
    parser.add_argument("--tfidf-only", action="store_true", help="只跑 TF-IDF 阶段 B")
    parser.add_argument(
        "--tfidf-min-score", type=float, default=0.05,
        help="阶段 B(TF-IDF) 相关度阈值,默认 0.05",
    )
    args = parser.parse_args()

    stats = backfill(
        dry_run=args.dry_run,
        rescore=args.rescore,
        min_score=args.min_score,
        max_tags=args.max_tags,
        tfidf_only=args.tfidf_only,
        tfidf_min_score=args.tfidf_min_score,
    )
    log.info(
        "完成: scanned=%d tagged_a=%d tagged_b=%d skipped=%d",
        stats["scanned"], stats["tagged_phase_a"], stats["tagged_phase_b"], stats["skipped"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
