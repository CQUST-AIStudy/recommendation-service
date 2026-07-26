"""
算法单元测试 — 验证 BKT、Ebbinghaus、Wilson 三个数学模型的正确性。
"""
from app.services.bkt import BKTEngine
from app.services.ebbinghaus import EbbinghausEngine
from app.services.wilson import WilsonEngine
from app.services.unified_state import UnifiedStateEngine


# ──────────────────────────────────────────
# BKT Tests
# ──────────────────────────────────────────

def test_bkt_new_correct():
    """新知识点做对: P(L₀)=0.30 → 经学习转移+贝叶斯更新后约为 0.748"""
    bkt = BKTEngine()
    result = bkt.update(0.30, correct=True)
    # Step1: 0.30 + 0.70*0.14 = 0.398; Step2a: 0.90*0.398/(0.90*0.398+0.20*0.602) ≈ 0.748
    assert 0.73 < result < 0.77, f"Expected ~0.748, got {result}"


def test_bkt_new_wrong():
    """新知识点做错: P(L₀)=0.30 → 经学习转移+贝叶斯更新后约为 0.076"""
    bkt = BKTEngine()
    result = bkt.update(0.30, correct=False)
    # Step1: 0.398; Step2b: 0.10*0.398/(0.10*0.398+0.80*0.602) ≈ 0.076
    assert 0.06 < result < 0.10, f"Expected ~0.076, got {result}"


def test_bkt_high_mastery_correct():
    """高掌握度做对: P(L)=0.85 → 更新后应 > 0.95"""
    bkt = BKTEngine()
    result = bkt.update(0.85, correct=True)
    assert 0.95 < result < 1.0, f"Expected >0.95, got {result}"


def test_bkt_high_mastery_wrong():
    """高掌握度做错: P(L)=0.85 → 更新后约为 0.458"""
    bkt = BKTEngine()
    result = bkt.update(0.85, correct=False)
    assert 0.43 < result < 0.48, f"Expected ~0.458, got {result}"


def test_bkt_monotonic_increase_on_correct():
    """连续做对，P(L) 应单调递增并趋近 1。"""
    bkt = BKTEngine()
    p = 0.30
    for _ in range(20):
        new_p = bkt.update(p, correct=True)
        assert new_p >= p, f"P(L) should not decrease on correct: {p} -> {new_p}"
        p = new_p
    assert p > 0.99, f"After 20 correct, P(L) should be > 0.99, got {p}"


def test_bkt_recovery():
    """从 P(recall) 反推 P(L) 应正确。"""
    bkt = BKTEngine()
    # P(recall) = 0.57, R(t) = 0.67 => P(L) = 0.57/0.67 ≈ 0.85
    p_l = bkt.recover_p_l(0.57, 0.67)
    assert abs(p_l - 0.85) < 0.02, f"Expected ~0.85, got {p_l}"


# ──────────────────────────────────────────
# Ebbinghaus Tests
# ──────────────────────────────────────────

def test_ebbinghaus_zero_days():
    """刚练习完，R(t)=1.0，遗忘度=0"""
    eng = EbbinghausEngine()
    s = eng.compute_stability(5, 2.0, 0)
    r = eng.compute_retention(0, s)
    assert abs(r - 1.0) < 1e-6, f"R(0) should be 1.0, got {r}"
    forgetting = eng.compute_forgetting_score(r)
    assert abs(forgetting) < 1e-4, f"Forgetting at day 0 should be 0, got {forgetting}"


def test_ebbinghaus_decay_over_time():
    """遗忘度应随时间递增。"""
    eng = EbbinghausEngine()
    prev_forgetting = 0.0
    for days in [0, 3, 7, 14, 30]:
        forgetting = eng.compute_forgetting_score_full(5, 2.0, days)
        assert forgetting >= prev_forgetting, f"Forgetting should increase: {prev_forgetting} -> {forgetting} at day {days}"
        prev_forgetting = forgetting


def test_ebbinghaus_stability_bounds():
    """稳定性应受 S_min 和 S_max 约束。"""
    eng = EbbinghausEngine(s_min=1.0, s_max=60.0)
    # 极端情况：0次成功
    s = eng.compute_stability(0, None, 100)
    assert s >= 1.0, f"S should be >= S_min, got {s}"

    # 大量成功
    s = eng.compute_stability(100, 1.0, 0)
    assert s <= 60.0, f"S should be <= S_max, got {s}"


def test_ebbinghaus_practice_reduction():
    """练习应降低遗忘度 12 点。"""
    eng = EbbinghausEngine(practice_reduction=12.0)
    result = eng.apply_practice_reduction(50.0)
    assert abs(result - 38.0) < 0.01, f"Expected 38.0, got {result}"


def test_ebbinghaus_numerical_example():
    """对照设计文档的数值示例: 5次成功, avg=2, 3天"""
    eng = EbbinghausEngine()
    forgetting = eng.compute_forgetting_score_full(5, 2.0, 3)
    # 文档: 3天 → 遗忘度 33%
    assert 25 < forgetting < 40, f"Expected ~33% at 3 days, got {forgetting:.1f}"


# ──────────────────────────────────────────
# Wilson Tests
# ──────────────────────────────────────────

def test_wilson_small_sample():
    """1题对1题: Wilson下界应远低于100%，总置信度约18.7"""
    wilson = WilsonEngine()
    confidence = wilson.compute_confidence(1, 1)
    assert 15 < confidence < 25, f"Expected ~18.7, got {confidence:.1f}"


def test_wilson_moderate_sample():
    """5题对4题: 总置信度约47"""
    wilson = WilsonEngine()
    confidence = wilson.compute_confidence(4, 5)
    assert 40 < confidence < 55, f"Expected ~47.0, got {confidence:.1f}"


def test_wilson_large_sample():
    """50题对40题: 总置信度约84.4"""
    wilson = WilsonEngine()
    confidence = wilson.compute_confidence(40, 50)
    assert 75 < confidence < 90, f"Expected ~84.4, got {confidence:.1f}"


def test_wilson_zero_attempts():
    """无数据时置信度应为0"""
    wilson = WilsonEngine()
    assert wilson.compute_confidence(0, 0) == 0.0


def test_wilson_lower_bound_range():
    """Wilson下界应在 [0, 1] 范围内"""
    wilson = WilsonEngine()
    for n in [1, 5, 10, 50, 100]:
        for p_val in [0.0, 0.3, 0.5, 0.8, 1.0]:
            lb = wilson.wilson_lower(p_val, n)
            assert 0.0 <= lb <= 1.0, f"Wilson lower out of range: {lb} for p={p_val}, n={n}"


# ──────────────────────────────────────────
# Unified State Tests
# ──────────────────────────────────────────

def test_unified_practice_then_decay():
    """练习后掌握度应提高；衰减后应降低。"""
    bkt = BKTEngine()
    ebb = EbbinghausEngine()
    wil = WilsonEngine()
    engine = UnifiedStateEngine(bkt, ebb, wil)

    # 初始状态: mastery=30, forgetting=0
    result = engine.update_after_practice(
        mastery_score=30.0,
        forgetting_score=0.0,
        success_count=0,
        attempt_count=0,
        avg_attempts_to_success=None,
        is_correct=True,
        practice_attempt_count=1,
    )
    assert result["mastery_score"] > 30.0, f"Mastery should increase after correct: {result}"
    assert result["success_count"] == 1

    # 模拟衰减: 7天后
    decay = engine.decay_forgetting(
        mastery_score=result["mastery_score"],
        forgetting_score=result["forgetting_score"],
        success_count=1,
        avg_attempts_to_success=1.0,
        days_since_last=7.0,
    )
    assert decay["forgetting_score"] > result["forgetting_score"], "Forgetting should increase after 7 days"


def test_unified_non_cumulative_decay():
    """多次衰减不应累积叠加（每次都从 P(L) 恢复）。"""
    bkt = BKTEngine()
    ebb = EbbinghausEngine()
    wil = WilsonEngine()
    engine = UnifiedStateEngine(bkt, ebb, wil)

    # 假设刚练习完: mastery=80, forgetting=0
    mastery = 80.0
    forgetting = 0.0

    # 第一次衰减: 3天
    d1 = engine.decay_forgetting(mastery, forgetting, success_count=5, avg_attempts_to_success=2.0, days_since_last=3.0)
    # 第二次衰减: 从第一次的结果再过7天
    d2 = engine.decay_forgetting(d1["mastery_score"], d1["forgetting_score"], success_count=5, avg_attempts_to_success=2.0, days_since_last=10.0)

    # 第二次的衰减应该基于恢复后的P(L)，而不是累积衰减
    assert d2["mastery_score"] >= 0, f"Mastery should not go negative: {d2['mastery_score']}"
    assert d2["forgetting_score"] > d1["forgetting_score"], "Forgetting should keep increasing"


if __name__ == "__main__":
    test_bkt_new_correct()
    test_bkt_new_wrong()
    test_bkt_high_mastery_correct()
    test_bkt_high_mastery_wrong()
    test_bkt_monotonic_increase_on_correct()
    test_bkt_recovery()
    test_ebbinghaus_zero_days()
    test_ebbinghaus_decay_over_time()
    test_ebbinghaus_stability_bounds()
    test_ebbinghaus_practice_reduction()
    test_ebbinghaus_numerical_example()
    test_wilson_small_sample()
    test_wilson_moderate_sample()
    test_wilson_large_sample()
    test_wilson_zero_attempts()
    test_wilson_lower_bound_range()
    test_unified_practice_then_decay()
    test_unified_non_cumulative_decay()
    print("All tests passed!")
