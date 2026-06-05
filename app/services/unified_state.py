"""
Unified Knowledge State — P(recall) = P(L) × R(t)

将 BKT 掌握概率与遗忘保留率统一为单一回忆概率。

Reference: Lindsey, R. V., et al. (2014).
"Improving Students' Long-Term Knowledge Retention via Spaced Repetition
in a Practice-Based Learning System". CHI '14. (DAS3H model)
"""
from __future__ import annotations

import math

from .bkt import BKTEngine
from .ebbinghaus import EbbinghausEngine
from .wilson import WilsonEngine


class UnifiedStateEngine:
    """
    统一知识状态引擎

    协调 BKT、艾宾浩斯、Wilson 三个子引擎，
    实现练习后更新和定时衰减更新两种流程。
    """

    def __init__(
        self,
        bkt: BKTEngine,
        ebbinghaus: EbbinghausEngine,
        wilson: WilsonEngine,
    ):
        self.bkt = bkt
        self.ebbinghaus = ebbinghaus
        self.wilson = wilson

    # ──────────────────────────────────────────
    # 练习后更新流程
    # ──────────────────────────────────────────
    def update_after_practice(
        self,
        mastery_score: float,
        forgetting_score: float,
        success_count: int,
        attempt_count: int,
        avg_attempts_to_success: float | None,
        is_correct: bool,
        practice_attempt_count: int,
    ) -> dict[str, float]:
        """
        练习后完整更新流程。

        1. 从 P(recall) 反推 P(L)
        2. BKT 更新 P(L)
        3. 遗忘度重置为低值（练习减少遗忘）
        4. 重新计算 R(t) ≈ 1（因为刚练习）
        5. P(recall) = P(L) × R(t)
        6. Wilson 置信度更新

        Parameters
        ----------
        mastery_score : float
            当前掌握度 [0, 100]，实际存储的是 P(recall)×100。
        forgetting_score : float
            当前遗忘度 [0, 100]。
        success_count : int
            更新前的成功次数。
        attempt_count : int
            更新前的总尝试次数（含本次之前）。
        avg_attempts_to_success : float | None
            平均每次成功需要的尝试次数。
        is_correct : bool
            本次练习是否正确。
        practice_attempt_count : int
            本次练习的尝试次数。

        Returns
        -------
        dict[str, float]
            更新后的 mastery_score, forgetting_score, confidence_score
        """
        # Step 1: 反推 P(L) = P(recall) / R(t)
        p_recall = mastery_score / 100.0
        old_retention = self.ebbinghaus.get_retention_rate(forgetting_score)
        p_l = self.bkt.recover_p_l(p_recall, old_retention)

        # Step 2: BKT 更新
        p_l = self.bkt.update(p_l, is_correct)

        # Step 3: 练习降低遗忘度
        new_forgetting = self.ebbinghaus.apply_practice_reduction(forgetting_score)

        # Step 4-5: 刚练习完，R(t)≈1，P(recall) ≈ P(L)
        new_retention = self.ebbinghaus.get_retention_rate(new_forgetting)
        new_p_recall = p_l * new_retention
        new_mastery = new_p_recall * 100.0

        # Step 6: Wilson 置信度
        new_success = success_count + (1 if is_correct else 0)
        new_attempt = attempt_count + practice_attempt_count
        new_confidence = self.wilson.compute_confidence(new_success, new_attempt)

        return {
            "mastery_score": round(max(0.0, min(100.0, new_mastery)), 2),
            "forgetting_score": round(max(0.0, min(100.0, new_forgetting)), 2),
            "confidence_score": round(max(0.0, min(100.0, new_confidence)), 2),
            "attempt_count": new_attempt,
            "success_count": new_success,
        }

    # ──────────────────────────────────────────
    # 定时衰减更新流程（非累积衰减）
    # ──────────────────────────────────────────
    def decay_forgetting(
        self,
        mastery_score: float,
        forgetting_score: float,
        success_count: int,
        avg_attempts_to_success: float | None,
        days_since_last: float,
    ) -> dict[str, float]:
        """
        定时任务遗忘衰减更新（非累积）。

        1. 保存旧遗忘度（旧 R(t)）
        2. 从 Ebbinghaus 计算新遗忘度（新 R(t)）
        3. P(L) = P(recall)_old / R_old(t)  ← 恢复
        4. P(recall)_new = P(L) × R_new(t)  ← 重新应用

        Parameters
        ----------
        mastery_score : float
            当前掌握度 [0, 100]。
        forgetting_score : float
            当前遗忘度 [0, 100]。
        success_count : int
            成功次数。
        avg_attempts_to_success : float | None
            平均每次成功需要的尝试次数。
        days_since_last : float
            距上次练习的天数。

        Returns
        -------
        dict[str, float]
            更新后的 mastery_score, forgetting_score
        """
        # Step 1: 旧 R(t)
        old_retention = self.ebbinghaus.get_retention_rate(forgetting_score)

        # Step 2: 新遗忘度
        new_forgetting = self.ebbinghaus.compute_forgetting_score_full(
            success_count, avg_attempts_to_success, days_since_last
        )

        # Step 3: 恢复 P(L) = P(recall)_old / R_old(t)
        p_recall_old = mastery_score / 100.0
        p_l = p_recall_old / old_retention
        p_l = min(1.0, max(0.0, p_l))

        # Step 4: P(recall)_new = P(L) × R_new(t)
        new_retention = self.ebbinghaus.get_retention_rate(new_forgetting)
        p_recall_new = p_l * new_retention
        new_mastery = p_recall_new * 100.0

        return {
            "mastery_score": round(max(0.0, min(100.0, new_mastery)), 2),
            "forgetting_score": round(max(0.0, min(100.0, new_forgetting)), 2),
        }
