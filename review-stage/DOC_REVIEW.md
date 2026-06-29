# Auto Review Loop — recommendation-service 文档评审

**Topic**: md 文档的理论性、严谨性、学术性、可行性
**Target**: `推荐算法亮点与改进方向分析.md`
**Reviewer**: deepseek-v4-pro (via `mcp__llm-chat__chat`, effort=max)
**Difficulty**: medium
**Started**: 2026-06-29
**MAX_ROUNDS**: 4

> 注：本日志专门记录对《推荐算法亮点与改进方向分析.md》这一**文档**的多轮评审。
> 既有 `AUTO_REVIEW.md` 记录的是对**代码正确性**的评审，两者主题不同，互不覆盖。

---

## Round 1 (2026-06-29)

### Assessment (Summary)
- **综合评分**：6/10（四维度：理论性 7 / 严谨性 6 / 学术性 6 / 可行性 7）
- **Verdict**：输出在弱点②中段被截断，未明确给出；据语气判断处于 almost/not-ready 之间
- **关键批评**：
  1. **[严重] Wilson 名实不符 + 表述矛盾**：报告称"Wilson 小样本修正"却未给 Wilson 下界公式，且亮点4（"可用于门控"）与改进8（"门控缺失"）读起来冲突
  2. **[高] 文献张冠李戴**："Lindsey et al. (2014, DAS3H) CHI'14"——Lindsey 2014 是 DASH 模型，DAS3H 属 Choffin et al. 2019
  3. **[中]** need 公式变量未定义、权重不一致未给差异、口语化（"塌方式下跌"）、改进方案（EM/UCB）仅提名词缺细节
- **被肯定处**：非累积衰减避免双重衰减的逻辑通路"可成立"；IDF 平滑式/difficulty_fit/boost 公式无误；改进 1/2/4/8 属高价值低侵入

### Reviewer Raw Response（截断，以下为已返回部分）

<details>
<summary>展开</summary>

**A. 综合评分**：6/10
**B. 四维度**：理论性 7/10、严谨性 6/10、学术性 6/10、可行性 7/10
- 理论性：对 BKT/遗忘曲线解耦、多通道匹配、MMR 的认识总体正确；"避免双重衰减"逻辑通路可成立。但关键引用张冠李戴（误将 DAS3H 归于 Lindsey 2014）；Wilson 论述弱化。
- 严谨性：内部矛盾（亮点4称已用Wilson门控，改进8又称门控缺失）；need 公式 f/w_course 未定义；README 与 config 权重不一致未给差异。IDF/difficulty_fit/boost 公式无误，但 Wilson 下界根本没给公式。
- 学术性：口语化（"塌方式下跌"）；唯一引用 Lindsey 2014 有事实错误；EM/UCB 仅提名词缺细节。
- 可行性：多数改进保留基础模型边界；改进1/2/4/8 高价值低侵入。但改进5/6 依赖爬虫 acRate，三档难度约束下"连续难度"存疑；改进3 真 MMR 的 λ=0.7 未给依据。

**C. 关键弱点**：
1. [严重] Wilson 置信区间使用矛盾且名实不符：给出的公式仅为对数样本充足度 `ln(1+n)/ln(51)*50`，未用二项比例 Wilson 下界；亮点4 与改进8 冲突。最小修复：统一表述、补 Wilson 下界公式、排序加入门控消解矛盾。
2. [高] 关键文献引用错误：Lindsey 2014 提出的是 DASH 模型，DAS3H 属 Choffin et al. 2019。……（此后被截断）

</details>

### 验证（修复前先核实 reviewer 的两条硬伤，不盲从）

| # | 指控 | 核实结论 | 证据 |
|---|------|---------|------|
| 文献 | Lindsey 2014 ≠ DAS3H | **成立** | WebSearch：Lindsey et al. 2014 发表于 *Psychological Science*，用 **DASH** 模型；**DAS3H 是 Choffin et al. 2019 (EDM, arXiv:1905.06873)**。且仓库 README 原始引用同样有此笔误 |
| Wilson | 报告缺公式 + 亮点4/改进8 矛盾 | **报告层面成立** | 代码 `wilson.py:56-86` 实际**已用** Wilson 下界（`performance_confidence = max(0,L)×50`），但报告未给公式、且"可用于门控/门控缺失"表述易混淆 |

### Actions Taken（本轮 9 项修复，均已落地）
1. **亮点1 文献勘误**：改为 DASH(Lindsey et al. 2014, *Psychological Science*) + DAS3H(Choffin et al. 2019, EDM)；论点从"避免过拟合"修正为可验证的"避免掌握度被遗忘因子重复扣减"；加勘误注提示 README 同步修正
2. **亮点4 Wilson**：补 Wilson 下界公式；澄清"Wilson 已用于算置信度、但置信度未反馈到排序"是两回事，消除与改进8的矛盾
3. **亮点3 need**：补 m/f/w_course 符号定义
4. **改进4**：补 README vs config 权重对比表（need_match 0.45 vs 0.40）
5. **改进3**：补 λ 取值依据（Carbonell & Goldstein 1998 MMR 原文，0.5~0.8 区间）+ Jaccard 重叠度公式
6. **改进7**：补 EM 的 E 步（forward-backward 后验）/ M 步（期望频次重估四参数）
7. **改进10**：补 ε-greedy 衰减式 + UCB1 公式
8. **改进6**：补可行性边界（依赖 acRate；拿不到则退化为"档位×学生通过率"近似）
9. 口语化"塌方式下跌"→"双重衰减系统性低估"

### Status
- 评分 6/10 触及 STOP 阈值下沿，但存在文献事实错误等硬伤 → 已修复，进入 Round 2 复核

---

## Round 2 (2026-06-29)

### Assessment (Summary)
- **综合评分**：6/10 → **8/10** ↑
- **四维度**：理论 7 / 严谨 6→**8** / 学术 6→**8** / 可行 7
- **Verdict**：**READY** ✅
- **STOP CONDITION 满足**：score=8 ≥ 6 且 verdict ∈ {ready, almost}

### Reviewer Raw Response

<details>
<summary>展开</summary>

1. 硬伤②文献：yes。DASH（Lindsey 2014）与 DAS3H（Choffin 2019）区分清楚，勘误详实。
2. 硬伤①Wilson：yes。Wilson 下界公式正确（z=1.95 合理），明确区分"参与计算"与"未用于排序"，矛盾已消除。
3. 新问题：无。各修复段均未引入事实性错误或新矛盾。
4. 更新后综合评分：8/10
5. 四维度：理论 7 / 严谨 8 / 学术 8 / 可行 7
6. 最终判断：READY

</details>

### Actions Taken
- 无新修复（复核确认 Round 1 的 9 项修复全部生效，未引入新问题）

### Status
- **循环终止**：positive assessment（READY @ 8/10），在 MAX_ROUNDS 前提前终止

---

## Final Summary

### 评分进展
| Round | 综合 | 理论 | 严谨 | 学术 | 可行 | Verdict |
|-------|------|------|------|------|------|---------|
| 1 | 6/10 | 7 | 6 | 6 | 7 | unknown（截断） |
| 2 | **8/10** | 7 | **8** | **8** | 7 | **READY** |

### 最终结论：READY (8/10)
两轮评审后，文档在理论性/严谨性/学术性/可行性四维度均达 7~8 分。Round 1 指出的两条硬伤（Wilson 公式缺失+矛盾、文献张冠李戴）经独立核实属实并已修复，Round 2 确认无新问题。

### 提升 6→8 的核心修复
1. **文献勘误**：Lindsey 2014 DASH / Choffin 2019 DAS3H 准确归属，并提示 README 同步修正
2. **Wilson**：补二项比例下界公式 + 澄清"算置信度"与"门控排序"的区别
3. **补细节**：need 变量定义、README↔config 权重对比表、MMR λ 依据、EM 的 E/M 步、UCB1 公式、改进6 可行性边界

### 残留事项（非阻塞，不影响 READY）
- ~~仓库 `README.md` 仍有同一文献笔误~~ → **已修复**：`README.md`、`ebbinghaus.py`、`unified_state.py` 三处引用均已更正为 Lindsey et al. (2014), *Psychological Science*, 25(3), 639–647（MCM 遗忘模型）
- 可行性稳定在 7/10：改进 5/6 依赖爬虫补 `acRate`，属数据侧长期项

