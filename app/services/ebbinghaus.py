"""
Ebbinghaus Forgetting Curve + SM-2 Stability Model — 遗忘度计算

Reference:
- Ebbinghaus, H. (1885/1964). "Memory: A Contribution to Experimental Psychology".
- SuperMemo SM-2 algorithm heuristics (P. A. Wozniak, 1997).
- Lindsey, R. V., et al. (2014). DAS3H model. CHI '14.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta


class EbbinghausEngine:
    """艾宾浩斯遗忘曲线 + SM-2 稳定性模型"""

    def __init__(
        self,
        s_base: float = 5.0,
        s_max: float = 60.0,
        s_min: float = 1.0,
        alpha: float = 0.12,
        beta: float = 0.08,
        lam: float = 0.03,
        delta: float = 0.05,
        practice_reduction: float = 12.0,
    ):
        self.s_base = s_base            # S_base: 基础稳定性（天）
        self.s_max = s_max              # S_max: 稳定性上限
        self.s_min = s_min              # S_min: 稳定性下限
        self.alpha = alpha              # α: 成功次数增益系数
        self.beta = beta                # β: 尝试次数惩罚系数
        self.lam = lam                  # λ: 时间衰减率（半衰期~23天）
        self.delta = delta              # δ: 近期衰减率
        self.practice_reduction = practice_reduction  # PR: 每次练习遗忘度降低量

    def compute_stability(
        self,
        success_count: int,
        avg_attempts_to_success: float | None,
        days_since_last: float,
    ) -> float:
        """
        计算记忆稳定性 S。

        S = S_base × min(S_max, (1 + α·W_success) × 1/(1 + β·ā) × D_recency)

        Parameters
        ----------
        success_count : int
            累计成功次数。
        avg_attempts_to_success : float | None
            平均每次成功需要的尝试次数。
        days_since_last : float
            距上次练习的天数。

        Returns
        -------
        float
            记忆稳定性 S（天）。
        """
        # 因子1: 时间加权成功计数 W_success = n_success × e^(-λ·Δt)
        w_success = success_count * math.exp(-self.lam * days_since_last)

        # 因子2: 尝试效率惩罚 1/(1 + β·ā)
        avg_a = avg_attempts_to_success if avg_attempts_to_success is not None and avg_attempts_to_success > 0 else 1.0
        efficiency = 1.0 / (1.0 + self.beta * avg_a)

        # 因子3: 近期衰减因子 D_recency = e^(-δ·Δt / (1 + n_success×0.1))
        d_recency = math.exp(-self.delta * days_since_last / (1.0 + success_count * 0.1))

        # 综合稳定性: S = clamp(S_base × 三因子乘积, S_min, S_max)
        raw = (1.0 + self.alpha * w_success) * efficiency * d_recency
        s = self.s_base * raw
        return max(self.s_min, min(self.s_max, s))

    def compute_retention(self, days_since: float, stability: float) -> float:
        """
        计算记忆保留率 R(t)。

        R(t) = e^(-t/S)

        Parameters
        ----------
        days_since : float
            距上次练习的天数 t。
        stability : float
            记忆稳定性 S（天）。

        Returns
        -------
        float
            记忆保留率，范围 [0, 1]。
        """
        if stability <= 0:
            return 0.0
        return math.exp(-days_since / stability)

    def compute_forgetting_score(self, retention: float) -> float:
        """
        遗忘度 = (1 - R(t)) × 100

        Parameters
        ----------
        retention : float
            记忆保留率 R(t)。

        Returns
        -------
        float
            遗忘度分数，范围 [0, 100]。
        """
        return (1.0 - retention) * 100.0

    def compute_forgetting_score_full(
        self,
        success_count: int,
        avg_attempts_to_success: float | None,
        days_since_last: float,
    ) -> float:
        """一步计算遗忘度：先算稳定性 → 保留率 → 遗忘度"""
        s = self.compute_stability(success_count, avg_attempts_to_success, days_since_last)
        r = self.compute_retention(days_since_last, s)
        return self.compute_forgetting_score(r)

    def apply_practice_reduction(self, forgetting_score: float) -> float:
        """
        练习后降低遗忘度。

        Parameters
        ----------
        forgetting_score : float
            当前遗忘度。

        Returns
        -------
        float
            练习后的遗忘度。
        """
        return max(0.0, forgetting_score - self.practice_reduction)

    def get_retention_rate(self, forgetting_score: float) -> float:
        """
        从遗忘度计算保留率 R(t) = 1 - forgetting/100

        Parameters
        ----------
        forgetting_score : float
            遗忘度 [0, 100]。

        Returns
        -------
        float
            保留率 [0, 1]。
        """
        return max(0.01, 1.0 - forgetting_score / 100.0)
