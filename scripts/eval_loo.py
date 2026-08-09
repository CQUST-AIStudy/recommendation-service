"""Temporal holdout offline evaluation for the recommendation pipeline.

Evaluation protocol:
  1. Split each student's attempts: first 70% = train, last 30% = test.
  2. From training data, compute per-tag mastery (BKT), forgetting (Ebbinghaus),
     using chronological update order and train-end timestamp as "now".
  3. Ground-truth weak/strong tags come from the SYNTHETIC DATA's true abilities
     (NOT from BKT mastery), eliminating circular evaluation.
  4. Metrics computed on common candidate set (all problems) for AUC,
     and on pipeline output (recall+rank+rerank) for precision/NDCG.
  5. Multiple seeds (10) with mean +/- std.

Baselines: Random, Quality, Difficulty.
Ablations: No-BKT, No-Ebbinghaus (only ablations that actually change behaviour).

Usage:
    cd recommendation-service
    uv run python scripts/eval_loo.py
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta
from typing import Any

# ────────────────────────── config ──────────────────────────

TAGS = [
    "数组",
    "字符串",
    "链表",
    "栈",
    "队列",
    "树",
    "二叉树",
    "二叉搜索树",
    "图",
    "有向图",
    "哈希表",
    "排序",
    "快速排序",
    "归并排序",
    "动态规划",
    "贪心",
    "回溯",
    "递归",
    "分治",
    "双指针",
    "滑动窗口",
    "二分查找",
    "堆",
    "优先队列",
    "并查集",
    "字典树",
    "拓扑排序",
    "最短路径",
    "最小生成树",
    "深度优先搜索",
    "广度优先搜索",
    "单调栈",
    "单调队列",
    "位运算",
    "前缀和",
    "差分数组",
    "线段树",
    "树状数组",
    "背包问题",
    "区间DP",
]
NUM_STUDENTS = 150
NUM_PROBLEMS = 500
MIN_ATTEMPTS = 20
MAX_ATTEMPTS = 45
TRAIN_RATIO = 0.70
K_VALUES = [5, 10]
RECO_LIMIT = 20
NUM_SEEDS = 10
WEAK_ABILITY = 0.40
STRONG_ABILITY = 0.70


def generate_data(seed: int) -> dict[str, Any]:
    rnd = random.Random(seed)

    problems: list[dict[str, Any]] = []
    problem_tags: dict[int, list[dict[str, Any]]] = {}
    tag_to_problems: dict[str, list[int]] = {t: [] for t in TAGS}

    for pid in range(1, NUM_PROBLEMS + 1):
        primary = rnd.choice(TAGS)
        has_secondary = rnd.random() < 0.35
        pool = [t for t in TAGS if t != primary]
        secondary = rnd.choice(pool) if has_secondary else None
        diff = rnd.choices(["Easy", "Medium", "Hard"], weights=[3, 5, 2])[0]
        quality = round(rnd.uniform(0.50, 1.00), 4)
        all_tags = [primary] + ([secondary] if secondary else [])
        title = f"{primary}练习题{pid}"
        p = {
            "id": pid,
            "source_key": f"id:{pid}",
            "title_main": title,
            "title_alt": title,
            "problem_text": " ".join(all_tags) + " 练习",
            "solution_text": " ".join(all_tags) + " 解法",
            "difficulty": diff,
            "quality_score": quality,
        }
        problems.append(p)
        tags_row = []
        for rank, tag in enumerate(all_tags):
            rel = round(1.0 - rank * 0.3, 4)
            tags_row.append({"problem_id": pid, "tag_name": tag, "relevance_score": rel, "tag_category": "algorithm", "is_primary": 1 if rank == 0 else 0})
            tag_to_problems.setdefault(tag, []).append(pid)
        problem_tags[pid] = tags_row

    students: list[dict[str, Any]] = []
    all_attempts: dict[int, list[dict[str, Any]]] = {}

    for sid in range(1, NUM_STUDENTS + 1):
        profile_type = rnd.choices(["weak", "mixed", "strong"], weights=[25, 55, 20])[0]
        abilities: dict[str, float] = {}
        for tag in TAGS:
            if profile_type == "weak":
                abilities[tag] = round(rnd.uniform(0.10, 0.40), 3)
            elif profile_type == "strong":
                abilities[tag] = round(rnd.uniform(0.60, 0.90), 3)
            else:
                abilities[tag] = round(rnd.uniform(0.15, 0.80), 3)

        n_attempts = rnd.randint(MIN_ATTEMPTS, MAX_ATTEMPTS)
        base_time = datetime(2026, 3, 1)
        attempts: list[dict[str, Any]] = []

        for i in range(n_attempts):
            if rnd.random() < 0.72:
                weak_tags = [t for t in TAGS if abilities[t] < 0.50]
                chosen_tag = rnd.choice(weak_tags) if weak_tags else rnd.choice(TAGS)
            else:
                chosen_tag = rnd.choice(TAGS)
            candidates = tag_to_problems.get(chosen_tag, [])
            if not candidates:
                continue
            pid = rnd.choice(candidates)
            p_tags = [pt["tag_name"] for pt in problem_tags.get(pid, [])]
            avg_ability = sum(abilities.get(t, 0.3) for t in p_tags) / max(1, len(p_tags))
            diff_obj = next((p for p in problems if p["id"] == pid), {})
            diff_bonus = {"Easy": 0.15, "Medium": 0.0, "Hard": -0.15}.get(diff_obj.get("difficulty", "Medium"), 0.0)
            p_correct = max(0.05, min(0.95, 0.25 + 0.65 * avg_ability + diff_bonus))
            is_correct = rnd.random() < p_correct
            attempts.append(
                {
                    "student_id": sid,
                    "problem_id": pid,
                    "submitted_at": base_time + timedelta(hours=i * 3 + rnd.randint(0, 2)),
                    "judge_status": "ACCEPTED" if is_correct else "WRONG_ANSWER",
                    "tags": p_tags,
                }
            )

        attempts.sort(key=lambda a: a["submitted_at"])
        all_attempts[sid] = attempts
        students.append({"id": sid, "abilities": abilities, "profile_type": profile_type})

    return {"problems": problems, "problem_tags": problem_tags, "tag_to_problems": tag_to_problems, "students": students, "all_attempts": all_attempts}


# ────────────────────────── skill profile builder ──────────────────────────


def build_skill_profile(
    train_attempts: list[dict[str, Any]],
    ablation: str = "full",
) -> list[dict[str, Any]]:
    """Build skill profile using chronological BKT updates.

    train_attempts MUST be sorted by submitted_at.
    """
    from app.services.bkt import BKTEngine
    from app.services.ebbinghaus import EbbinghausEngine
    from app.services.wilson import WilsonEngine

    bkt = BKTEngine()
    ebb = EbbinghausEngine()
    wil = WilsonEngine()

    # Per-tag chronological sequences (preserving submitted_at order)
    tag_sequences: dict[str, list[bool]] = {}
    tag_last_time: dict[str, datetime] = {}

    for att in train_attempts:
        is_correct = att["judge_status"] == "ACCEPTED"
        t = att["submitted_at"]
        if isinstance(t, str):
            t = datetime.fromisoformat(t)
        for tag in att.get("tags", []):
            tag_sequences.setdefault(tag, []).append(is_correct)
            if tag not in tag_last_time or t > tag_last_time[tag]:
                tag_last_time[tag] = t

    if not tag_sequences:
        return []

    # Evaluation "now" = end of training period (NOT a fixed future date)
    eval_now = max(tag_last_time.values())

    profile: list[dict[str, Any]] = []
    for tag, sequence in tag_sequences.items():
        total = len(sequence)
        correct = sum(sequence)
        last_time = tag_last_time[tag]
        days_since = max(0.0, (eval_now - last_time).total_seconds() / 86400.0)

        # BKT: update in TRUE chronological order
        if ablation == "no_bkt":
            mastery = 50.0
        else:
            p_l = bkt.p_initial
            for is_correct in sequence:
                p_l = bkt.update(p_l, is_correct)
            mastery = p_l * 100.0

        # Ebbinghaus
        if ablation == "no_ebbinghaus":
            forgetting = 0.0
        else:
            avg_attempts = total / correct if correct > 0 else None
            stability = ebb.compute_stability(correct, avg_attempts, days_since)
            retention = ebb.compute_retention(days_since, stability)
            forgetting = round((1.0 - retention) * 100.0, 2)

        # Wilson (always computed; not ablated because ranking doesn't use it)
        p = correct / total if total > 0 else 0.0
        confidence = wil.wilson_lower(p, total) * 100.0

        profile.append(
            {
                "student_id": 0,
                "tag_name": tag,
                "mastery_score": round(mastery, 2),
                "forgetting_score": forgetting,
                "confidence_score": round(confidence, 2),
                "attempt_count": total,
                "success_count": correct,
                "avg_attempts_to_success": total / correct if correct > 0 else None,
                "last_practice_at": last_time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return profile


# ────────────────────────── mock db ──────────────────────────


class MockDB:
    def __init__(self, data: dict[str, Any]):
        self._problems = {p["id"]: p for p in data["problems"]}
        self._problem_tags = data["problem_tags"]
        self._tag_to_problems = data["tag_to_problems"]

    def find_all_skill_states(self, sid):
        return []

    def find_skill_state(self, sid, tag):
        return None

    def find_feedback_by_student(self, sid, limit=300):
        return []

    def fetch_student_wrong_question_tags_by_id(self, sid):
        return []

    def fetch_student_wrong_question_tags(self, sn):
        return []

    def find_pta_high_frequency_errors(self, sid, min_errors=5, class_id=None):
        return []

    def find_pta_tag_mappings(self):
        return []

    def find_student_by_id(self, sid):
        return {"id": sid, "student_no": str(sid)}

    def find_student_by_student_no(self, sn):
        return {"id": int(sn), "student_no": sn}

    def find_problem_ids_by_tag_names(self, tags):
        seen = set()
        result = []
        for tag in tags:
            for pid in self._tag_to_problems.get(tag, []):
                if pid not in seen:
                    seen.add(pid)
                    result.append(pid)
        return result

    def find_problems_by_ids(self, ids):
        return [self._problems[pid] for pid in ids if pid in self._problems]

    def find_problems_page(self, offset, limit):
        s = sorted(self._problems.values(), key=lambda p: float(p.get("quality_score") or 0), reverse=True)
        return s[offset : offset + limit]

    def find_all_problems(self, limit=1000):
        return self.find_problems_page(0, limit)

    def find_problems_by_difficulty(self, diff, limit=100):
        return [p for p in self._problems.values() if p["difficulty"] == diff][:limit]

    def find_tags_for_problem(self, pid):
        return self._problem_tags.get(pid, [])

    def find_tags_for_problems(self, pids):
        return {pid: self._problem_tags.get(pid, []) for pid in pids}

    def find_embedding_count(self, *a, **kw):
        return 0

    def find_problem_attempts_for_student_in_class(self, sid, cid):
        return []


def install_mock_db(mock: MockDB) -> None:
    import app.db as db_pkg
    import app.db.mysql_client as mc

    for name in dir(mock):
        if name.startswith("_"):
            continue
        fn = getattr(mock, name)
        if callable(fn):
            setattr(db_pkg, name, fn)
            setattr(mc, name, fn)


# ────────────────────────── helpers ──────────────────────────


def _primary_tag(pt_map: dict[int, list[dict[str, Any]]], pid: int) -> str | None:
    for pt in pt_map.get(pid, []):
        if pt.get("is_primary"):
            return pt["tag_name"]
    rows = pt_map.get(pid, [])
    return rows[0]["tag_name"] if rows else None


def _all_tags(pt_map: dict[int, list[dict[str, Any]]], pid: int) -> set[str]:
    return {pt["tag_name"] for pt in pt_map.get(pid, [])}


def jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


# ────────────────────────── scoring & recommendation ──────────────────────────


def run_pipeline(skill_profile, mock_db, limit):
    """Run full recall + rank + rerank pipeline. Returns ordered pid list."""
    from app.services.recall import collect_candidates
    from app.services.ranking import rank_and_score, diversity_rerank
    from app.services.recommendation_service import _get_weights

    feedback_ctx = {"score_adjustments": {}, "completed_problem_ids": [], "disliked_problem_ids": []}
    candidates = collect_candidates(
        skill_profile,
        feedback_ctx,
        limit,
        weak_ratio=0.60,
        diff_ratio=0.25,
        explore_ratio=0.15,
        student_id=0,
        wrong_question_ratio=0.0,
        semantic_ratio=0.0,
        embedding_config={"enabled": False},
        candidate_multiplier=3,
        wrong_question_context=None,
    )
    if not candidates:
        return []
    ptm = {pid: mock_db.find_tags_for_problem(pid) for pid in candidates}
    weights = _get_weights()
    ranked = rank_and_score(
        list(candidates.values()),
        skill_profile,
        feedback_ctx.get("score_adjustments"),
        weights,
        ptm,
        None,
        None,
    )
    reranked = diversity_rerank(ranked, limit, ptm, max_tag_ratio=0.40)
    return [item["problem_id"] for item in reranked]


def score_all_problems_algo(skill_profile, all_problems, pt_map):
    """Score ALL problems using ranking function (bypass recall). Returns {pid: score}."""
    from app.services.ranking import rank_and_score
    from app.services.recommendation_service import _get_weights

    weights = _get_weights()
    # Add _recall_sources so rank_and_score doesn't crash
    problems_copy = []
    for p in all_problems:
        pc = dict(p)
        pc["_recall_sources"] = ["auc_eval"]
        problems_copy.append(pc)
    ranked = rank_and_score(problems_copy, skill_profile, {}, weights, pt_map, None, None)
    return {item["problem_id"]: item["score_total"] for item in ranked}


def score_all_random(all_problems, seed):
    rnd = random.Random(seed * 7919)
    return {p["id"]: rnd.random() for p in all_problems}


def score_all_quality(all_problems):
    return {p["id"]: float(p.get("quality_score") or 0.0) for p in all_problems}


def score_all_difficulty(all_problems, skill_profile):
    from app.services.ranking import compute_difficulty_fit

    avg = sum(float(s.get("mastery_score", 50)) for s in skill_profile) / len(skill_profile) if skill_profile else 50
    return {p["id"]: compute_difficulty_fit(p, avg) for p in all_problems}


# ────────────────────────── metrics ──────────────────────────


def weak_precision(pids, pt_map, weak_primary_tags, k):
    if k == 0:
        return 0.0
    hits = sum(1 for pid in pids[:k] if _primary_tag(pt_map, pid) in weak_primary_tags)
    return hits / k


def next_topic_recall(pids, pt_map, new_test_tags, k):
    if not new_test_tags:
        return 0.0
    rec_tags: set[str] = set()
    for pid in pids[:k]:
        rec_tags.update(_all_tags(pt_map, pid))
    return len(rec_tags & new_test_tags) / len(new_test_tags)


def ndcg(pids, pt_map, relevant_tags, k):
    """NDCG@K with per-problem binary relevance. IDCG from sorted ideal."""
    top = pids[:k]
    gains = [1.0 if (_all_tags(pt_map, pid) & relevant_tags) else 0.0 for pid in top]
    dcg = sum(g / math.log2(r + 2) for r, g in enumerate(gains))
    # IDCG: best possible if all top-K were relevant
    ideal_hits = sum(1 for _ in range(min(len(gains), k)))
    idcg = sum(1.0 / math.log2(r + 2) for r in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def pairwise_auc(scores, pt_map, weak_tags, strong_tags):
    """Standard pairwise AUC on ALL problems. Ties = 0.5. None if no weak or no strong."""
    weak_scores = [sc for pid, sc in scores.items() if _primary_tag(pt_map, pid) in weak_tags]
    strong_scores = [sc for pid, sc in scores.items() if _primary_tag(pt_map, pid) in strong_tags]
    if not weak_scores or not strong_scores:
        return None
    correct = 0.0
    for ws in weak_scores:
        for ss in strong_scores:
            if ws > ss:
                correct += 1.0
            elif ws == ss:
                correct += 0.5
    return correct / (len(weak_scores) * len(strong_scores))


# ────────────────────────── per-seed evaluation ──────────────────────────

CONFIGS = [
    ("algo", "full", "BKT+Ebbinghaus"),
    ("algo", "no_bkt", "No-BKT"),
    ("algo", "no_ebbinghaus", "No-Ebbinghaus"),
    ("baseline", "random", "Random"),
    ("baseline", "quality", "Quality"),
    ("baseline", "difficulty", "Difficulty"),
]


def evaluate_one_seed(data: dict[str, Any], seed: int) -> dict[str, dict[str, float]]:
    """Returns {config_label: {metric_name: value}}."""
    pt_map = data["problem_tags"]
    all_problems = data["problems"]
    students = data["students"]
    all_attempts = data["all_attempts"]

    # Build train/test splits
    splits: dict[int, dict[str, Any]] = {}
    for stu in students:
        sid = stu["id"]
        attempts = all_attempts[sid]
        if len(attempts) < 8:
            continue
        cut = int(len(attempts) * TRAIN_RATIO)
        train = attempts[:cut]
        test = attempts[cut:]
        test_tags = set()
        train_tags = set()
        for a in test:
            test_tags.update(a.get("tags", []))
        for a in train:
            train_tags.update(a.get("tags", []))
        new_test_tags = test_tags - train_tags
        # Ground-truth weak/strong from SYNTHETIC abilities (independent of BKT)
        abilities = stu["abilities"]
        weak_tags_gt = {t for t, v in abilities.items() if v < WEAK_ABILITY}
        strong_tags_gt = {t for t, v in abilities.items() if v > STRONG_ABILITY}
        splits[sid] = {
            "train": train,
            "test_tags": test_tags,
            "new_test_tags": new_test_tags,
            "weak_tags": weak_tags_gt,
            "strong_tags": strong_tags_gt,
        }

    valid_sids = list(splits.keys())
    mock_db = MockDB(data)
    install_mock_db(mock_db)

    results: dict[str, dict[str, float]] = {}

    for ctype, cid, label in CONFIGS:
        metric_sums = {f"weak_p{k}": 0.0 for k in K_VALUES}
        metric_sums.update({f"next_r{k}": 0.0 for k in K_VALUES})
        metric_sums.update({f"ndcg{k}": 0.0 for k in K_VALUES})
        metric_sums["auc"] = 0.0
        metric_sums["coverage"] = 0.0
        metric_sums["personalization"] = 0.0
        auc_count = 0
        cnt = 0
        rec_all: set[int] = set()
        rec_sets: list[set[int]] = []

        for sid in valid_sids:
            sp_info = splits[sid]
            train = sp_info["train"]
            if not train:
                continue
            weak_tags = sp_info["weak_tags"]
            strong_tags = sp_info["strong_tags"]

            # Build skill profile (only for algo configs)
            if ctype == "algo":
                sp = build_skill_profile(train, cid)
                if not sp:
                    continue
            else:
                sp = build_skill_profile(train, "full")

            cnt += 1

            # --- AUC on ALL problems (common candidate set) ---
            if ctype == "algo":
                all_scores = score_all_problems_algo(sp, all_problems, pt_map)
            elif cid == "random":
                all_scores = score_all_random(all_problems, seed * 1000 + sid)
            elif cid == "quality":
                all_scores = score_all_quality(all_problems)
            elif cid == "difficulty":
                all_scores = score_all_difficulty(all_problems, sp)
            else:
                all_scores = {p["id"]: 0.0 for p in all_problems}

            auc_val = pairwise_auc(all_scores, pt_map, weak_tags, strong_tags)
            if auc_val is not None:
                metric_sums["auc"] += auc_val
                auc_count += 1

            # --- Pipeline output for precision/NDCG/recall ---
            if ctype == "algo":
                pids = run_pipeline(sp, mock_db, RECO_LIMIT)
            elif cid == "random":
                rnd = random.Random(seed * 1000 + sid)
                all_pids = list(mock_db._problems.keys())
                pids = rnd.sample(all_pids, min(RECO_LIMIT, len(all_pids)))
            elif cid == "quality":
                ranked_q = sorted(all_problems, key=lambda p: float(p.get("quality_score") or 0), reverse=True)
                pids = [p["id"] for p in ranked_q[:RECO_LIMIT]]
            elif cid == "difficulty":
                avg = sum(float(s.get("mastery_score", 50)) for s in sp) / len(sp) if sp else 50
                target = "Easy" if avg < 40 else ("Medium" if avg < 70 else "Hard")
                probs = mock_db.find_problems_by_difficulty(target, RECO_LIMIT * 2)
                if len(probs) < RECO_LIMIT:
                    probs = mock_db.find_problems_page(0, RECO_LIMIT)
                pids = [p["id"] for p in probs[:RECO_LIMIT]]
            else:
                pids = []

            rec_all.update(pids)
            rec_sets.append(set(pids))

            new_test = sp_info["new_test_tags"]
            test_tags = sp_info["test_tags"]

            for k in K_VALUES:
                metric_sums[f"weak_p{k}"] += weak_precision(pids, pt_map, weak_tags, k)
                metric_sums[f"next_r{k}"] += next_topic_recall(pids, pt_map, new_test, k)
                metric_sums[f"ndcg{k}"] += ndcg(pids, pt_map, test_tags, k)

        # Personalization
        pers = 0.0
        if len(rec_sets) > 1:
            sims = []
            rng = random.Random(99)
            for _ in range(min(200, len(rec_sets) * 3)):
                i, j = rng.sample(range(len(rec_sets)), 2)
                sims.append(jaccard(rec_sets[i], rec_sets[j]))
            pers = 1.0 - (sum(sims) / len(sims) if sims else 0)

        # Finalize averages
        avg_metrics = {}
        for k in K_VALUES:
            avg_metrics[f"weak_p{k}"] = metric_sums[f"weak_p{k}"] / cnt if cnt else 0
            avg_metrics[f"next_r{k}"] = metric_sums[f"next_r{k}"] / cnt if cnt else 0
            avg_metrics[f"ndcg{k}"] = metric_sums[f"ndcg{k}"] / cnt if cnt else 0
        avg_metrics["auc"] = metric_sums["auc"] / auc_count if auc_count else 0
        avg_metrics["auc_n"] = float(auc_count)
        avg_metrics["coverage"] = len(rec_all) / NUM_PROBLEMS
        avg_metrics["personalization"] = pers
        results[label] = avg_metrics

    return results


# ────────────────────────── multi-seed aggregation ──────────────────────────


def aggregate_seeds(all_seed_results: list[dict[str, dict[str, float]]]) -> dict[str, dict[str, dict[str, float]]]:
    """Returns {config_label: {metric_name: {mean, std}}}."""
    config_labels = list(all_seed_results[0].keys())
    metric_names = [k for k in all_seed_results[0][config_labels[0]] if not k.endswith("_n")]

    aggregated: dict[str, dict[str, dict[str, float]]] = {}
    for label in config_labels:
        aggregated[label] = {}
        for metric in metric_names:
            values = [sr[label][metric] for sr in all_seed_results]
            mean = sum(values) / len(values)
            if len(values) > 1:
                variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
                std = math.sqrt(variance)
            else:
                std = 0.0
            aggregated[label][metric] = {"mean": mean, "std": std}

        # auc_n (count, not averaged)
        auc_ns = [sr[label].get("auc_n", 0) for sr in all_seed_results]
        aggregated[label]["auc_n"] = {"mean": sum(auc_ns) / len(auc_ns), "std": 0.0}

    return aggregated


def print_table(agg: dict[str, dict[str, dict[str, float]]]) -> None:
    header = (
        f"{'Config':<18s} | {'WeakP@5':>12s} {'WeakP@10':>12s} | "
        f"{'NDCG@5':>12s} {'NDCG@10':>12s} | "
        f"{'NextR@5':>12s} {'NextR@10':>12s} | "
        f"{'AUC':>12s} | {'Cov':>12s} {'Pers':>12s}"
    )
    print(header)
    print("-" * len(header))

    for label in CONFIGS:
        cfg = label[2]
        if cfg not in agg:
            continue
        m = agg[cfg]

        def fmt(metric):
            d = m[metric]
            return f"{d['mean']:5.1%}±{d['std']:4.1%}"

        row = (
            f"{cfg:<18s} | {fmt('weak_p5'):>12s} {fmt('weak_p10'):>12s} | "
            f"{fmt('ndcg5'):>12s} {fmt('ndcg10'):>12s} | "
            f"{fmt('next_r5'):>12s} {fmt('next_r10'):>12s} | "
            f"{fmt('auc'):>12s} | {fmt('coverage'):>12s} {fmt('personalization'):>12s}"
        )
        print(row)

    print("=" * len(header))


# ────────────────────────── main ──────────────────────────


def main():
    print("Temporal Holdout Offline Evaluation")
    print(f"  Students={NUM_STUDENTS}  Problems={NUM_PROBLEMS}  Tags={len(TAGS)}")
    print(f"  Seeds={NUM_SEEDS}  Train/Test={int(TRAIN_RATIO * 100)}/{int((1 - TRAIN_RATIO) * 100)}")
    print(f"  Weak<={WEAK_ABILITY}  Strong>={STRONG_ABILITY}  (ground-truth abilities)")
    print()

    all_seed_results: list[dict[str, dict[str, float]]] = []

    for seed_idx in range(NUM_SEEDS):
        seed = 42 + seed_idx
        print(f"  Seed {seed} ({seed_idx + 1}/{NUM_SEEDS})...", end=" ", flush=True)
        data = generate_data(seed)
        results = evaluate_one_seed(data, seed)
        all_seed_results.append(results)
        # Quick preview
        full_auc = results.get("BKT+Ebbinghaus", {}).get("auc", 0)
        random_auc = results.get("Random", {}).get("auc", 0)
        print(f"AUC: full={full_auc:.1%}  random={random_auc:.1%}")

    print()
    agg = aggregate_seeds(all_seed_results)
    print_table(agg)

    # Save raw results
    output = {
        "config": {
            "students": NUM_STUDENTS,
            "problems": NUM_PROBLEMS,
            "tags": len(TAGS),
            "seeds": NUM_SEEDS,
            "train_ratio": TRAIN_RATIO,
            "weak_ability": WEAK_ABILITY,
            "strong_ability": STRONG_ABILITY,
        },
        "results": {label: {metric: vals for metric, vals in metrics.items()} for label, metrics in agg.items()},
    }
    output_path = "scripts/eval_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nDetailed results saved to {output_path}")

    print("\nMetrics:")
    print("  WeakP@K   = Precision@K for weak-tag problems (ground-truth ability < 0.4)")
    print("  NDCG@K    = Normalized DCG (position-weighted test-tag hit)")
    print("  NextR@K   = Recall@K for NEW test tags (tags in test but NOT in train)")
    print("  AUC       = Standard pairwise AUC on ALL 500 problems (ties=0.5)")
    print("  Cov       = Coverage: catalogue fraction recommended")
    print("  Pers      = Personalization: 1 - avg pairwise Jaccard")
    print(f"  Format    = mean +/- std across {NUM_SEEDS} seeds")


if __name__ == "__main__":
    main()
