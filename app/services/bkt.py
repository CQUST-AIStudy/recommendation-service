"""
Bayesian Knowledge Tracing (BKT) — 掌握概率动态更新

Reference: Corbett, A. T., & Anderson, J. R. (1995).
"Knowledge Tracing: Modeling the Acquisition of Procedural Knowledge".
User Modeling and User-Adapted Interaction, 4(4), 253-278.
"""
from __future__ import annotations



class BKTEngine:
    """贝叶斯知识追踪引擎"""

    def __init__(
        self,
        p_transfer: float = 0.14,
        p_guess: float = 0.20,
        p_slip: float = 0.10,
        p_initial: float = 0.30,
    ):
        self.p_transfer = p_transfer  # P(T): 学习转移概率
        self.p_guess = p_guess        # P(G): 猜对概率
        self.p_slip = p_slip          # P(S): 失误概率
        self.p_initial = p_initial    # P(L₀): 初始掌握度

    def update(self, p_l: float, correct: bool) -> float:
        """
        单步 BKT 更新。

        Parameters
        ----------
        p_l : float
            当前潜在掌握概率 P(L_{n-1})，范围 [0, 1]。
        correct : bool
            本次练习是否做对。

        Returns
        -------
        float
            更新后的 P(L)，范围 [0, 1]。
        """
        # Step 1: 学习转移（无论对错都会发生）
        p_l = p_l + (1.0 - p_l) * self.p_transfer

        # Step 2: 贝叶斯后验更新
        if correct:
            # P(L|correct) = (1-P(S))·P(L) / [(1-P(S))·P(L) + P(G)·(1-P(L))]
            numerator = (1.0 - self.p_slip) * p_l
            denominator = numerator + self.p_guess * (1.0 - p_l)
        else:
            # P(L|wrong) = P(S)·P(L) / [P(S)·P(L) + (1-P(G))·(1-P(L))]
            numerator = self.p_slip * p_l
            denominator = numerator + (1.0 - self.p_guess) * (1.0 - p_l)

        if denominator < 1e-12:
            return p_l

        p_l = numerator / denominator
        return max(0.0, min(1.0, p_l))

    def recover_p_l(self, p_recall: float, retention: float) -> float:
        """
        从存储的 P(recall) 反推潜在掌握度 P(L)。

        P(recall) = P(L) × R(t)  =>  P(L) = P(recall) / R(t)

        Parameters
        ----------
        p_recall : float
            存储的回忆概率（mastery_score / 100）。
        retention : float
            当前记忆保留率 R(t) = max(0.01, 1 - forgetting/100)。

        Returns
        -------
        float
            恢复后的 P(L)，范围 [0, 1]。
        """
        if retention < 1e-6:
            return p_recall
        p_l = p_recall / retention
        return min(1.0, max(0.0, p_l))
