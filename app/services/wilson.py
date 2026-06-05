"""
Wilson Confidence Interval — 置信度计算（小样本统计修正）

Reference: Wilson, E. B. (1927).
"Probable Inference, the Law of Succession, and Statistical Inference".
Journal of the American Statistical Association, 22(158), 209-212.
"""
from __future__ import annotations

import math


class WilsonEngine:
    """Wilson 置信区间引擎"""

    def __init__(self, z: float = 1.95):
        """
        Parameters
        ----------
        z : float
            正态分布分位数。1.96 对应 95% 置信度（设计文档用 1.95）。
        """
        self.z = z

    def wilson_lower(self, p: float, n: int) -> float:
        """
        Wilson 置信区间下界。

        Wilson_Lower = (p + z²/(2n) - z·√(p(1-p)/n + z²/(4n²))) / (1 + z²/n)

        Parameters
        ----------
        p : float
            成功率（成功次数/总次数），范围 [0, 1]。
        n : int
            总尝试次数。

        Returns
        -------
        float
            Wilson 下界，范围 [0, 1]。
        """
        if n <= 0:
            return 0.0

        z = self.z
        z2 = z * z
        p_clamped = max(0.0, min(1.0, p))

        denominator = 1.0 + z2 / n
        center = p_clamped + z2 / (2.0 * n)
        radicand = p_clamped * (1.0 - p_clamped) / n + z2 / (4.0 * n * n)

        return (center - z * math.sqrt(radicand)) / denominator

    def compute_confidence(self, success_count: int, attempt_count: int) -> float:
        """
        计算综合置信度分数 [0, 100]。

        总置信度 = 样本充足度(0~50) + 表现置信度(0~50)

        Parameters
        ----------
        success_count : int
            成功次数。
        attempt_count : int
            总尝试次数。

        Returns
        -------
        float
            置信度分数，范围 [0, 100]。
        """
        if attempt_count <= 0:
            return 0.0

        p = success_count / attempt_count

        # 样本充足度: ln(1+n) / ln(1+50) × 50, 范围 [0, 50]
        sample_sufficiency = math.log(1 + attempt_count) / math.log(1 + 50) * 50.0

        # 表现置信度: max(0, Wilson_Lower) × 50, 范围 [0, 50]
        wilson = self.wilson_lower(p, attempt_count)
        performance_confidence = max(0.0, wilson) * 50.0

        return sample_sufficiency + performance_confidence
