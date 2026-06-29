# Recommendation Service — 学习画像 + LeetCode 个性化推荐微服务

> FastAPI 微服务，实现 BKT 掌握度追踪、艾宾浩斯遗忘曲线、Wilson 置信区间、统一知识状态、六因子排序推荐引擎、**三层混合题目-知识点匹配(词典 + TF-IDF + 语义向量)**。
> 服务端口：`8003`，直连 MySQL `ptadatabase` 数据库。

---

## 目录

1. [算法说明](#算法说明)
   - 1.1 [学生画像侧:BKT / 艾宾浩斯 / Wilson](#11-学生画像侧bkt--艾宾浩斯--wilson)
   - 1.2 [推荐引擎整合(新)](#12-推荐引擎整合新)
   - 1.3 [题目-知识点侧:三层混合匹配(新)](#13-题目-知识点侧三层混合匹配新)
2. [API 接口](#api-接口)
3. [与 Java 后端对接](#与-java-后端对接)
4. [项目结构](#项目结构)
5. [启动方式](#启动方式)
6. [离线预计算流程(新)](#离线预计算流程新)
7. [参数配置](#参数配置)
8. [理论基础](#理论基础)
9. [算法验证](#算法验证)

---

## 算法说明

### 1.1 学生画像侧:BKT / 艾宾浩斯 / Wilson

> 以下四个模型回答"学生会不会、记不记得、置信度高不高",
> 是**学生侧**的数字特征建模。原始实现见 `bkt.py` / `ebbinghaus.py` / `wilson.py` / `unified_state.py`。

#### 1.1.1 贝叶斯知识追踪 (BKT) — `services/bkt.py`

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

#### 1.1.2 艾宾浩斯遗忘曲线 + SM-2 — `services/ebbinghaus.py`

**核心公式**：`R(t) = e^(-t/S)`，其中 `t` = 距上次练习天数，`S` = 记忆稳定性（天）。

**稳定性计算**：`S = clamp(S_base × (1+α·W_success) × 1/(1+β·ā) × D_recency, S_min, S_max)`

三个因子：
- 时间加权成功计数：`W_success = n_success × e^(-λ·Δt)`（近期成功贡献大）
- 尝试效率惩罚：`1/(1+β·ā)`（平均尝试越多→效率越低）
- 近期衰减：`D_recency = e^(-δ·Δt/(1+0.1·n_success))`

#### 1.1.3 Wilson 置信区间 — `services/wilson.py`

修正小样本偏差。置信度 = 样本充足度(0~50) + 表现置信度(0~50)。

- 样本充足度：`ln(1+n) / ln(1+50) × 50`
- 表现置信度：`max(0, Wilson_Lower) × 50`
- Wilson 下界公式：`(p + z²/2n - z√(p(1-p)/n + z²/4n²)) / (1 + z²/n)`

#### 1.1.4 统一知识状态 — `services/unified_state.py`

`P(recall) = P(L) × R(t)`，将 BKT 与遗忘统一为单一回忆概率。

- **练习后**：反推 P(L) → BKT 更新 → 重置遗忘 → P(recall) ≈ P(L)
- **定时衰减**：恢复 P(L) → 新 R(t) → P(recall)_new（非累积衰减）

### 1.2 推荐引擎整合 — `services/recall.py` + `services/ranking.py`

> 把 1.1 的学生画像 + 1.3 的题目特征组合成最终推荐。

**6 步流水线**：
1. 画像快照 → 2. 反馈上下文 → 3. 多路召回 → 4. 六因子排序 → 5. MMR 多样性重排 → 6. 理由生成

**多路召回(6 路)**：
| 召回源 | 默认配额 | 说明 |
|--------|---------|------|
| 弱项标签召回 | 60% | 取掌握度最低的 5 个 tag,跨 algorithm/data_structure/technique 三类查 tag 表 |
| 难度进阶召回 | 25% | 按平均掌握度映射目标难度 |
| 探索召回 | 15% | 找练习次数 <3 的未探索标签 |
| 错题本召回 | 20% | 从学生未掌握错题的 tag 反向拉候选(需 student_id) |
| **语义近邻召回(新)** | 15% | 用题向量做余弦近邻检索,需要离线算 embedding,未启用时自动降级 |
| 热门题兜底 | 余下补足 | 按 quality_score 排序 |

**六因子排序**：

```
score = 0.45·need_match + 0.20·difficulty_fit + 0.15·P_success
      + 0.10·novelty + 0.10·quality - 0.15·repeat_penalty + 0.10·wrong_question
```

- `need(tag) = 0.75·(1-m_norm) + 0.10·f_norm + 0.15·w_course`
- `difficulty_fit = e^(-|d_target - d_problem|)`
- `d_target = (avg_mastery/100) × 2 + 1`（映射到 Easy=1, Medium=2, Hard=3）
- `need_match` 中的 `relevance_score` 来自三层匹配(见 1.3),不再是固定 1.0

**MMR 多样性约束**：单标签占比 ≤ 40%，相邻 2 题不同标签。

**推荐理由生成**:每条推荐项携带可解释文本,包含 3 个信息:
1. 本题主标签 + 相关度分数(来自三层匹配)
2. 学生在该知识点的掌握度 + 历史练习次数
3. 难度档位说明

---

### 1.3 题目-知识点侧:三层混合匹配(新)

> 解决"如何从题目文字抽象出知识点特征"的核心问题,
> 借鉴抖音/淘宝推荐系统的 **multi-channel hybrid matching** 架构。
>
> 答辩要点:**离线把题目特征算好,在线只做检索**,不在推荐时调用 AI 通读题面。

#### 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: 词典加权匹配(方案 A)                              │
│    knowledge_tags.py — 54 个知识点,中英双语同义词            │
│    打分:中文命中 1.0 / 英文整词命中 0.8 / 标题命中直接 1.0   │
│    用途:强信号,离线一次性把所有题打标写入 tag 表             │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: TF-IDF 质心模型(方案 B)                          │
│    tfidf_model.py — 从已标注题聚合每个 tag 的代表向量         │
│    纯 Python 实现,无 scikit-learn 依赖                      │
│    用途:补 Layer 1 没覆盖到的题(同义词、跨语言表达)         │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: 语义向量(方案 C,可选)                          │
│    embedding_model.py — SentenceTransformer (bge-small-zh)   │
│    用 PyTorch 离线算 embedding 存 BLOB,在线暴力余弦         │
│    用途:防漏召回,理解"DP" = "Dynamic Programming"          │
└─────────────────────────────────────────────────────────────┘
        ↓ 离线预计算 → leetcode_problem_tag + leetcode_problem_embedding ↓
┌─────────────────────────────────────────────────────────────┐
│  在线:6 路召回 + 六因子排序(relevance_score 加权)          │
└─────────────────────────────────────────────────────────────┘
```

#### Layer 1:词典加权匹配(方案 A)— `services/knowledge_tags.py`

**词典规模**:54 个知识点,分 3 类:
- `data_structure` (17 项):数组/字符串/链表/栈/队列/哈希表/树/BST/堆/图/并查集/字典树/线段树/树状数组/矩阵/邻接表/邻接矩阵
- `algorithm` (19 项):排序/二分查找/DFS/BFS/回溯/递归/分治/动态规划/贪心/枚举/模拟/数学/几何/博弈论/随机化/拒绝采样/蓄水池抽样/二分答案/状态压缩 DP
- `technique` (18 项):双指针/滑动窗口/前缀和/差分/单调栈/单调队列/位运算/状态压缩/快速幂/离散化/哈希算法/KMP/拓扑排序/最短路/最小生成树/SCC/网络流/二叉树遍历

**打分函数 `tag_relevance_score(title, text, tag_name)`**:
```
中文关键词命中正文  → 1.0
英文整词命中正文    → 0.8
任意语言命中标题    → 直接 1.0(标题是强信号)
全部不命中          → 0.0
未收录 tag          → 朴素子串匹配,降权 0.5
```

**在线打分函数 `detect_tags_for_problem(title, text)`**:对一道题扫所有 54 个知识点,返回 Top-K 候选标签(按分数降序)。

#### Layer 2:TF-IDF 质心模型(方案 B)— `services/tfidf_model.py`

**核心思路**:对每个知识点 tag,聚合所有已标注该 tag 的题的 TF-IDF 向量,取平均得到 tag 质心。对新题算 TF-IDF 后与所有 tag 质心算余弦相似度。

**实现特点**:
- **纯 Python** TF-IDF,无 scikit-learn 依赖
- 分词器:英文按单词、中文按单字(粗粒度但鲁棒)
- IDF 用 sklearn 平滑公式 `log((1+N)/(1+df)) + 1`
- 向量 L2 归一化,余弦 = 点积

**质心公式**:
```
centroid[tag] = normalize( Σ relevance_score_i · tfidf(problem_i) )
```

**新题打分**:
```
score(tag | problem) = cosine(tfidf(problem), centroid[tag])
```

#### Layer 3:语义向量(方案 C)— `services/embedding_model.py`

**模型**:BAAI/bge-small-zh(中文优秀,200MB 左右)
**存储**:BLOB 紧凑二进制(little-endian float32),2-3 千题 × 384 维约 4MB
**降级机制**:
- 未装 `sentence-transformers` → 跳过本层
- embedding 表为空 → 跳过本层
- 任何异常 → 返回空 list,主流程继续

**在线召回流程** `recall_by_semantic`:
1. 加载所有题向量
2. 对每个弱项 tag,从该 tag 标注的题聚合质心向量
3. 在候选池里找余弦 ≥ 0.30 的题
4. 多 tag 命中时取最高分
5. 返回 Top-K 题目

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

Docker 部署后，Java 后端（`backend-repo`）和本服务应加入同一个 Docker 网络，例如 `cqust-ai-net`。Java 后端通过 Docker 服务名调用本服务：

```
Java backend → http://recommendation-service:8003/ai/profile/update
Java backend → http://recommendation-service:8003/ai/recommendation/generate
Java backend → http://recommendation-service:8003/ai/recommendation/result/{requestId}
Java backend → http://recommendation-service:8003/ai/recommendation/feedback
Java backend → http://recommendation-service:8003/webhook/spider-import
Java backend → http://recommendation-service:8003/internal/refresh-student
Java backend → http://recommendation-service:8003/internal/refresh-class
```

本地开发时仍可使用 `http://127.0.0.1:8003`。

**前端代理配置**（本地开发时在 `frontend-repo/vue.config.js` 中添加）：
```javascript
'/recommend': {
  target: 'http://127.0.0.1:8003',
  pathRewrite: { '^/recommend': '' },
  changeOrigin: true
}
```

本服务直连同一个 MySQL 数据库 `ptadatabase`，操作以下表：
- `student_skill_state` — 学生技能画像（读写）
- `leetcode_problem_bank` — 题库（只读,在线)
- `leetcode_problem_tag` — 题目标签(读写,离线打标脚本写入)
- `leetcode_problem_embedding` — 题向量 BLOB(方案 C,离线脚本写入,可选)
- `leetcode_recommend_request` — 推荐请求（读写）
- `leetcode_recommend_item` — 推荐结果（读写）
- `leetcode_recommend_feedback` — 反馈行为（读写）

---

## 项目结构

```
recommendation-service/
├── AGENTS.md                       # 项目协作与部署约定
├── Dockerfile                      # 推荐服务容器镜像
├── docker-compose.yml              # Docker 一键部署编排
├── .dockerignore                   # Docker 构建上下文忽略规则
├── .env.docker.example             # Docker 环境变量模板
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
│   │   ├── knowledge_tags.py       # [新/方案 A] 54 知识点词典 + 加权打分
│   │   ├── tfidf_model.py          # [新/方案 B] TF-IDF 质心模型(纯 Python)
│   │   ├── embedding_model.py      # [新/方案 C] 语义向量 + 优雅降级
│   │   ├── recall.py               # 6 路召回(弱项/难度/探索/错题本/语义/热门)
│   │   ├── ranking.py              # 六因子排序 + MMR 多样性重排 + 理由生成
│   │   ├── feedback.py             # 反馈上下文构建 + 行为记录
│   │   ├── wrong_question_features.py  # 错题本上下文
│   │   └── recommendation_service.py   # 推荐流水线协调器(6 步入口)
│   └── db/
│       ├── __init__.py             # 函数 re-export
│       └── mysql_client.py         # MySQL 连接池 + CRUD 操作
├── scripts/
│   ├── backfill_problem_tags.py    # [新] 方案 A+B 离线打标脚本
│   └── compute_embeddings.py       # [新] 方案 C 离线算题向量脚本
├── sql/
│   ├── spider_integration.sql      # 爬虫集成表
│   └── V13__create_leetcode_problem_embedding.sql  # [新] 方案 C 题向量表
└── tests/
    ├── __init__.py
    ├── test_algorithms.py          # 18 个原算法测试(BKT/艾宾浩斯/Wilson)
    ├── test_knowledge_tags.py      # [新] 14 个方案 A 测试
    ├── test_tfidf_model.py         # [新] 12 个方案 B 测试
    ├── test_embedding_model.py     # [新] 13 个方案 C 测试
    └── services/
        └── test_ranking_wrong_question.py  # 错题本加权排序测试
```

---

## 启动方式

### Docker 部署

本仓库只编排推荐服务，MySQL、Java 后端、爬虫服务等已有服务需要加入同一个 Docker 网络。

```powershell
# 1. 创建同服务器服务互访网络（已存在会提示重复，可忽略）
docker network create cqust-ai-net

# 2. 复制 Docker 环境变量模板
Copy-Item .env.docker.example .env.docker

# 3. 编辑 .env.docker，填入真实数据库账号密码
# DB_HOST 默认使用同网络内 MySQL 服务名 mysql

# 4. 构建并启动
docker compose up -d --build

# 5. 查看状态
docker compose ps
```

容器默认不发布宿主机端口，只暴露给同一 Docker 网络内的服务访问：

```
http://recommendation-service:8003/health
http://recommendation-service:8003/docs
```

如果需要临时从宿主机直接调试，可在 `docker-compose.yml` 的服务下增加端口映射：

```yaml
ports:
  - "8003:8003"
```

### 本地开发

```powershell
# 1. 安装依赖
uv sync --extra dev

# 2. 配置环境变量
Copy-Item .env.example .env
# 编辑 .env，填入数据库密码等

# 3. 启动服务
uv run uvicorn app.main:app --host 0.0.0.0 --port 8003 --reload

# 4. 访问 API 文档
# http://127.0.0.1:8003/docs
```

---

## 离线预计算流程（新）

> 三层混合匹配架构依赖离线预计算。
> 推荐服务首次启动后、有数据但还没跑过打标脚本时,
> 推荐效果会很差(relevance_score 全是 1.0、语义召回为空)。
> 请按下面顺序跑一遍。

### Step 1:启用方案 A(词典加权打标)

对所有 leetcode_problem_tag 表里没有标签的题,用 54 知识点词典自动打标:

```powershell
cd recommendation-service
uv run python scripts/backfill_problem_tags.py
```

可选参数:
- `--dry-run` 只打印不写库
- `--rescore` 强制重打所有题(覆盖已有的 1.0 relevance_score)
- `--min-score 0.5` 相关度阈值
- `--max-tags 5` 单题最多打几个标签

### Step 2:启用方案 B(TF-IDF 二次召回)

对方案 A 没覆盖到的题,用 TF-IDF 从已标注题学统计特征:

```powershell
uv run python scripts/backfill_problem_tags.py --tfidf-only --tfidf-min-score 0.08
```

TF-IDF 分数通常 < 0.5,阈值要低。

### Step 3:启用方案 C(可选,语义向量)

需要先装 PyTorch + sentence-transformers,以及建表:

```powershell
# 1. 装依赖
uv run pip install sentence-transformers

# 2. 在 MySQL 里建表
mysql -uroot -p ptadatabase < sql/V13__create_leetcode_problem_embedding.sql

# 3. 离线算 embedding(首次会下载模型约 200MB)
uv run python scripts/compute_embeddings.py

# 可选:换英文模型
uv run python scripts/compute_embeddings.py --model BAAI/bge-small-en-v1.5
```

跑完后,在线推荐会自动启用语义召回第 5 路(无需重启服务)。

### 验证预计算结果

```sql
-- 方案 A/B 后:tag 表应该有真实 relevance_score,不再全是 1.0
SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN relevance_score = 1.0 THEN 1 ELSE 0 END) AS legacy_ones,
    SUM(CASE WHEN is_primary = 1 THEN 1 ELSE 0 END) AS primary_count
FROM leetcode_problem_tag;

-- 方案 C 后:embedding 表应该有数据
SELECT model_name, COUNT(*) AS n FROM leetcode_problem_embedding GROUP BY model_name;
```

---

## 参数配置

所有参数均可在 `.env` 或 `.env.docker` 文件中覆盖，默认值与设计文档一致：

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

### 学生画像侧

| 模型 | 引用 |
|------|------|
| BKT | Corbett, A. T., & Anderson, J. R. (1995). "Knowledge Tracing". *UMUAI*, 4(4), 253-278. |
| 遗忘曲线 | Ebbinghaus, H. (1885). "Memory: A Contribution to Experimental Psychology". |
| BKT+遗忘混合 | Lindsey, R. V., Shroyer, J. D., Pashler, H., & Mozer, M. C. (2014). "Improving Students' Long-Term Knowledge Retention Through Personalized Review"（MCM 遗忘模型驱动的个性化复习调度）. *Psychological Science*, 25(3), 639–647. |
| 时间感知KT | Upadhyay, S., et al. (2021). "Robust Knowledge Tracing". *L@S '21*. |
| 置信区间 | Wilson, E. B. (1927). *JASA*, 22(158), 209-212. |
| SM-2 | Wozniak, P. A. (1997). "SuperMemo: Theoretical Background". |

### 题目-知识点匹配侧(新)

| 模型 | 引用 |
|------|------|
| TF-IDF | Salton, G., & Buckley, C. (1988). "Term-weighting approaches in automatic text retrieval". *Information Processing & Management*, 24(5), 513-523. |
| Rocchio 分类(质心) | Rocchio, J. J. (1971). "Relevance Feedback in Information Retrieval". *The SMART Retrieval System*, 313-323. |
| Sentence Embedding | Reimers, N., & Gurevych, I. (2019). "Sentence-BERT". *EMNLP '19*. |
| bge 模型 | Xiao et al. (2023). "C-Pack: Packed Resources For General Chinese Embeddings". *arXiv:2309.07597*. |
| Multi-channel 召回 | Davidson, J., et al. (2010). "The YouTube Video Recommendation System". *RecSys '10*. |

---

## 算法验证

### 运行全部测试

```powershell
# 方式 1:用 pytest
uv run pytest tests/ -v

# 方式 2:不装 pytest 也能跑(每个 test 文件都内置 __main__)
uv run python tests/test_algorithms.py
uv run python tests/test_knowledge_tags.py
uv run python tests/test_tfidf_model.py
uv run python tests/test_embedding_model.py
```

### 测试覆盖(共 57 个用例)

| 文件 | 数量 | 覆盖范围 |
|------|------|---------|
| `test_algorithms.py` | 18 | BKT 单调性、艾宾浩斯衰减、Wilson 边界、统一状态非累积衰减 |
| `test_knowledge_tags.py` | 14 | 词典规模 ≥50、中英命中加权、标题强信号、4 道真实题分类 |
| `test_tfidf_model.py` | 12 | 分词、向量 L2 归一化、余弦、质心分类 DP/树 |
| `test_embedding_model.py` | 13 | BLOB 序列化、余弦、Top-K 召回、降级逻辑 |

### 端到端验证

```powershell
# 完整 import + 推荐流水线 smoke test
uv run python -c "
from app.services.recommendation_service import generate_recommendation
from app.services.recall import recall_by_semantic
from app.services.knowledge_tags import KNOWLEDGE_TAGS
print(f'Knowledge tags: {len(KNOWLEDGE_TAGS)}')
print('OK')
"
```

### 答辩常见问题应对

| 老师可能问 | 你这样答 |
|---|---|
| 怎么从文字匹配到题目? | 三层混合架构:① 词典加权匹配(54 知识点带中英同义词)② TF-IDF 质心模型(从已标注题学统计特征)③ 语义向量(bge-small-zh embedding),分层兜底 |
| 关键词怎么选? | 人工词典 54 项 + 自动从 PTA 已标注题的统计特征双向来源 |
| 2-3 千题怎么不靠 AI 实时跑? | 全部离线预计算:tag 表预打标、TF-IDF 模型预训练、embedding 预存,在线只是 SQL 查询 + 暴力余弦,延迟 <50ms |
| AI 模型在哪? | 只用 SentenceTransformer 做 embedding(一次性离线),在线推荐不依赖任何 AI 调用,稳定可控 |
| 如果 Python 服务挂了? | 三层都有降级:embedding 没装 → 跳过语义召回;TF-IDF 训练不出 → 跳过 B 阶段;词典匹配失效 → 回退到子串匹配。永远不会整体崩 |
