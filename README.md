# Recommendation Service — 学习画像 + LeetCode 个性化推荐微服务

> FastAPI 微服务，实现 BKT 掌握度追踪、艾宾浩斯遗忘曲线、Wilson 置信区间、统一知识状态、六因子排序推荐引擎。
> 服务端口：`8003`，直连 MySQL `ptadatabase` 数据库。

---

## 算法说明

### 1. 贝叶斯知识追踪 (BKT) — `services/bkt.py`

根据学生每次练习结果（对/错）动态更新知识点的掌握概率。

**参数**（Corbett & Anderson 1995 文献标准值）：

| 参数 | 符号 | 默认值 | 含义 |
|------|------|--------|------|
| 学习转移概率 | P(T) | 0.14 | 每次练习从未掌握→掌握的概率 |
| 猜对概率 | P(G) | 0.20 | 未掌握时蒙对的概率 |
| 失误概率 | P(S) | 0.10 | 已掌握但做错的概率 |
| 初始掌握度 | P(L₀) | 0.30 | 新知识点的初始掌握概率 |

**更新公式**：

1. 学习转移（无论对错）：`P(L) = P(L) + (1 - P(L)) × P(T)`
2. 做对时贝叶斯更新：`P(L|correct) = (1-P(S))·P(L) / [(1-P(S))·P(L) + P(G)·(1-P(L))]`
3. 做错时贝叶斯更新：`P(L|wrong) = P(S)·P(L) / [P(S)·P(L) + (1-P(G))·(1-P(L))]`

### 2. 艾宾浩斯遗忘曲线 + SM-2 — `services/ebbinghaus.py`

**核心公式**：`R(t) = e^(-t/S)`，其中 `t` = 距上次练习天数，`S` = 记忆稳定性（天）。

**稳定性计算**：`S = clamp(S_base × (1+α·W_success) × 1/(1+β·ā) × D_recency, S_min, S_max)`

三个因子：
- 时间加权成功计数：`W_success = n_success × e^(-λ·Δt)`（近期成功贡献大）
- 尝试效率惩罚：`1/(1+β·ā)`（平均尝试越多→效率越低）
- 近期衰减：`D_recency = e^(-δ·Δt/(1+0.1·n_success))`

### 3. Wilson 置信区间 — `services/wilson.py`

修正小样本偏差。置信度 = 样本充足度(0~50) + 表现置信度(0~50)。

- 样本充足度：`ln(1+n) / ln(1+50) × 50`
- 表现置信度：`max(0, Wilson_Lower) × 50`
- Wilson 下界公式：`(p + z²/2n - z√(p(1-p)/n + z²/4n²)) / (1 + z²/n)`

### 4. 统一知识状态 — `services/unified_state.py`

`P(recall) = P(L) × R(t)`，将 BKT 与遗忘统一为单一回忆概率。

- **练习后**：反推 P(L) → BKT 更新 → 重置遗忘 → P(recall) ≈ P(L)
- **定时衰减**：恢复 P(L) → 新 R(t) → P(recall)_new（非累积衰减）

### 5. 推荐引擎 — `services/recall.py` + `services/ranking.py`

**6 步流水线**：
1. 画像快照 → 2. 反馈上下文 → 3. 多路召回 → 4. 六因子排序 → 5. MMR 多样性重排 → 6. 理由生成

**多路召回**：弱项(60%) + 难度进阶(25%) + 探索(15%) + 热门(补足)

**六因子排序**：

```
score = 0.45·need_match + 0.20·difficulty_fit + 0.15·P_success
      + 0.10·novelty + 0.10·quality - 0.15·repeat_penalty
```

- `need(tag) = 0.75·(1-m_norm) + 0.10·f_norm + 0.15·w_course`
- `difficulty_fit = e^(-|d_target - d_problem|)`
- `d_target = (avg_mastery/100) × 2 + 1`（映射到 Easy=1, Medium=2, Hard=3）

**MMR 多样性约束**：单标签占比 ≤ 40%，相邻 2 题不同标签。

---

## API 接口

所有接口返回统一格式：`{"code": 200, "message": "success", "data": {...}}`

JSON 字段使用 camelCase（通过 Pydantic alias 自动转换）。

### 健康检查

| Method | Path | 说明 |
|--------|------|------|
| GET | `/health` | 服务健康检查 |

### 学习画像

| Method | Path | 说明 |
|--------|------|------|
| POST | `/ai/profile/update` | 练习后更新单个技能画像（BKT+遗忘+Wilson） |
| POST | `/ai/profile/batch-update` | 批量更新多个技能画像 |
| POST | `/ai/profile/decay` | 定时遗忘衰减任务（非累积衰减） |
| GET | `/ai/profile/{studentId}` | 获取学生完整技能画像 |

**POST /ai/profile/update 请求体**：
```json
{
  "studentId": 1,
  "tagName": "动态规划",
  "isCorrect": true,
  "attemptCount": 2
}
```

**POST /ai/profile/decay 请求体**：
```json
{
  "daysThreshold": 1
}
```

### 推荐系统

| Method | Path | 说明 |
|--------|------|------|
| POST | `/ai/recommendation/generate` | 生成个性化推荐（返回 requestId） |
| GET | `/ai/recommendation/result/{requestId}` | 轮询推荐结果 |
| POST | `/ai/recommendation/exposure` | 记录推荐曝光 |
| POST | `/ai/recommendation/feedback` | 记录用户行为（click/start/complete/skip/dislike） |

**POST /ai/recommendation/generate 请求体**：
```json
{
  "studentId": 1,
  "limit": 20,
  "scene": "default"
}
```

**GET /ai/recommendation/result/{requestId} 返回**：
```json
{
  "code": 200,
  "data": {
    "requestId": "uuid",
    "status": "completed",
    "items": [
      {
        "rankNo": 1,
        "problemId": 123,
        "scoreTotal": 0.8523,
        "scoreNeedMatch": 0.78,
        "scoreDifficultyFit": 0.91,
        "scoreSuccessProb": 0.65,
        "scoreNovelty": 0.80,
        "scoreQuality": 0.85,
        "reasonText": "针对你的薄弱技能「动态规划」（掌握度 35.2%）进行强化练习。中等难度，适合提升解题能力。预计用时 30 分钟。",
        "problem": {
          "problemId": 123,
          "title": "两数之和",
          "difficulty": "Medium",
          "sourceUrl": "https://leetcode.cn/problems/two-sum/",
          "estimatedMinutes": 20
        }
      }
    ]
  }
}
```

**POST /ai/recommendation/feedback 请求体**：
```json
{
  "requestId": "uuid",
  "problemId": 123,
  "action": "complete",
  "sessionId": "session-xxx"
}
```

`action` 可选值：`click` / `start` / `complete` / `skip` / `dislike`

---

## 与 Java 后端对接

Java 后端（`backend-repo`）通过 `RestTemplate` 调用本服务：

```
Java backend → http://127.0.0.1:8003/ai/profile/update
Java backend → http://127.0.0.1:8003/ai/recommendation/generate
Java backend → http://127.0.0.1:8003/ai/recommendation/result/{requestId}
Java backend → http://127.0.0.1:8003/ai/recommendation/feedback
```

**前端代理配置**（在 `frontend-repo/vue.config.js` 中添加）：
```javascript
'/recommend': {
  target: 'http://127.0.0.1:8003',
  pathRewrite: { '^/recommend': '' },
  changeOrigin: true
}
```

本服务直连同一个 MySQL 数据库 `ptadatabase`，操作以下表：
- `student_skill_state` — 学生技能画像（读写）
- `leetcode_problem_bank` — 题库（只读）
- `leetcode_problem_tag` — 题目标签（只读）
- `leetcode_recommend_request` — 推荐请求（读写）
- `leetcode_recommend_item` — 推荐结果（读写）
- `leetcode_recommend_feedback` — 反馈行为（读写）

---

## 项目结构

```
recommendation-service/
├── pyproject.toml                  # uv 依赖管理
├── .env.example                    # 环境变量模板（含所有可配置参数）
├── README.md                       # 本文档
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI 入口：CORS、异常处理、路由注册
│   ├── api/
│   │   ├── __init__.py
│   │   ├── health.py               # GET /health
│   │   ├── skill_profile.py        # POST /ai/profile/*  GET /ai/profile/{studentId}
│   │   └── recommendation.py       # POST /ai/recommendation/*  GET /ai/recommendation/result/*
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py               # pydantic-settings 配置（所有参数可 .env 覆盖）
│   │   └── responses.py            # 统一响应格式 api_success / api_error_response
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── profile.py              # 画像相关 Pydantic 模型
│   │   └── recommendation.py       # 推荐相关 Pydantic 模型
│   ├── services/
│   │   ├── __init__.py
│   │   ├── bkt.py                  # 贝叶斯知识追踪引擎
│   │   ├── ebbinghaus.py           # 艾宾浩斯遗忘曲线 + SM-2 稳定性模型
│   │   ├── wilson.py               # Wilson 置信区间引擎
│   │   ├── unified_state.py        # 统一知识状态 P(recall) = P(L) × R(t)
│   │   ├── recall.py               # 多路召回（弱项/难度/探索/热门）
│   │   ├── ranking.py              # 六因子排序 + MMR 多样性重排 + 理由生成
│   │   ├── feedback.py             # 反馈上下文构建 + 行为记录
│   │   └── recommendation_service.py  # 推荐流水线协调器（6步流水线入口）
│   └── db/
│       ├── __init__.py
│       └── mysql_client.py         # MySQL 连接池 + CRUD 操作
└── tests/
    ├── __init__.py
    └── test_algorithms.py          # 18 个算法单元测试
```

---

## 启动方式

```bash
# 1. 安装依赖
cd recommendation-service
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入数据库密码等

# 3. 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload

# 4. 访问 API 文档
# http://127.0.0.1:8003/docs
```

---

## 参数配置

所有参数均可在 `.env` 文件中覆盖，默认值与设计文档一致：

| 配置组 | 关键参数 | 说明 |
|--------|----------|------|
| BKT | `BKT_P_TRANSFER=0.14` | 学习转移概率 |
| BKT | `BKT_P_GUESS=0.20` | 猜对概率 |
| BKT | `BKT_P_SLIP=0.10` | 失误概率 |
| BKT | `BKT_P_INITIAL=0.30` | 初始掌握度 |
| Ebbinghaus | `EBBINGHAUS_S_BASE=5.0` | 基础稳定性（天） |
| Ebbinghaus | `EBBINGHAUS_S_MAX=60.0` | 稳定性上限 |
| Ebbinghaus | `EBBINGHAUS_ALPHA=0.12` | 成功次数增益系数 |
| Ebbinghaus | `EBBINGHAUS_PR=12.0` | 练习遗忘度降低量 |
| Wilson | `WILSON_Z=1.95` | 正态分布分位数 |
| 排序权重 | `WEIGHT_NEED_MATCH=0.45` | 薄弱匹配度权重 |
| 排序权重 | `WEIGHT_DIFFICULTY_FIT=0.20` | 难度适配权重 |
| 排序权重 | `WEIGHT_SUCCESS_PROB=0.15` | 通过概率权重 |
| 排序权重 | `WEIGHT_NOVELTY=0.10` | 新颖度权重 |
| 排序权重 | `WEIGHT_QUALITY=0.10` | 题目质量权重 |
| 排序权重 | `WEIGHT_REPEAT_PENALTY=0.15` | 重复惩罚权重 |
| 召回 | `RECALL_WEAK_RATIO=0.60` | 弱项召回配额 |
| 召回 | `RECALL_DIFFICULTY_RATIO=0.25` | 难度进阶配额 |
| 召回 | `RECALL_EXPLORATION_RATIO=0.15` | 探索召回配额 |
| 反馈 | `FEEDBACK_DELTA_COMPLETE=0.10` | 完成行为分数增量 |
| 反馈 | `FEEDBACK_DELTA_DISLIKE=-0.35` | 不喜欢行为分数增量 |
| 多样性 | `DIVERSITY_MAX_TAG_RATIO=0.40` | 单标签最大占比 |

---

## 理论基础

| 模型 | 引用 |
|------|------|
| BKT | Corbett, A. T., & Anderson, J. R. (1995). "Knowledge Tracing". *UMUAI*, 4(4), 253-278. |
| 遗忘曲线 | Ebbinghaus, H. (1885). "Memory: A Contribution to Experimental Psychology". |
| BKT+遗忘混合 | Lindsey, R. V., et al. (2014). DAS3H model. *CHI '14*. |
| 时间感知KT | Upadhyay, S., et al. (2021). "Robust Knowledge Tracing". *L@S '21*. |
| 置信区间 | Wilson, E. B. (1927). *JASA*, 22(158), 209-212. |
| SM-2 | Wozniak, P. A. (1997). "SuperMemo: Theoretical Background". |

---

## 算法验证

运行 `tests/test_algorithms.py` 验证全部 18 个测试用例：

```bash
python -m pytest tests/test_algorithms.py -v
```

覆盖范围：
- BKT：新知识点做对/做错、高掌握度做对/做错、单调递增、P(L) 恢复
- Ebbinghaus：零天遗忘、时间衰减、稳定性边界、练习降低遗忘、数值对照
- Wilson：小/中/大样本、零尝试、边界范围
- Unified：练习后提升+衰减后降低、非累积衰减验证
