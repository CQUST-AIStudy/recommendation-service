# Auto Review — recommendation-service × spider-repo integration

## Round 1 (2026-06-06) — Initial Assessment

### Assessment (Summary)
- Score: **3/10**
- Verdict: **not ready**
- Reviewer: deepseek-v4-pro

### Reviewer Raw Response

<details>
<summary>Click to expand full reviewer response</summary>

**Critical Weaknesses (Ranked):**
1. **No Use of PTA Submission History** — Spider collects rich data (submissions, scores, code) but recommendation service ignores it entirely. Skill profiles remain empty until LeetCode practice.
2. **Student Identity Mismatch** — `student_id` (int) vs `student_profile.id` + `student_no` (string). No mapping exists.
3. **Reliance on Legacy Table Names** — If spider transitions to unified-only mode, all PTA features break silently.
4. **Missing Integration Endpoints** — No webhook/callback for spider to notify when new data is available.
5. **No PTA-to-LeetCode Tag Mapping** — Even if PTA data is ingested, it can't be associated with skill tags.

</details>

### Actions Taken
1. Created `app/services/pta_ingestion.py` — Full PTA data ingestion service
2. Created `app/api/webhook.py` — 3 new endpoints for spider integration
3. Created `app/schemas/webhook.py` — Pydantic models for webhook/refresh
4. Created `sql/spider_integration.sql` — DB migration for pta_tag_mapping table
5. Extended `app/db/mysql_client.py` — 16 new query functions for spider-repo data
6. Made `get_engine()` public in `recommendation_service.py`
7. Registered webhook routers in `app/main.py`

### Status
- continuing to round 2

---

## Round 2 (2026-06-06) — After Initial Implementation

### Assessment (Summary)
- Score: **8/10**
- Verdict: **almost ready**
- Reviewer: deepseek-v4-pro

### Reviewer Raw Response

<details>
<summary>Click to expand full reviewer response</summary>

**What Works:**
1. Unified + legacy schema ingestion with clean fallback
2. Student identity mapping (student_id ↔ student_no)
3. Tag mapping with 35 DB seeds + 40+ hardcoded fallback
4. Webhook and manual refresh endpoints
5. Non-destructive updates (preserves existing LeetCode profiles)
6. Public engine access for initialization

**Remaining Weaknesses:**
1. **No Dynamic PTA Updates** — Existing skill states are skipped, not merged
2. **Coarse Tag Matching** — Substring matching can cause false positives ("数" matching "数学")

</details>

### Actions Taken
1. Implemented merge logic: PTA data now merges with existing LeetCode profiles using weighted blend
2. Refined tag extraction: keywords sorted by length (longest first) to prevent partial-word matches
3. Both unified and legacy paths now support dynamic updates

### Status
- continuing to round 3

---

## Round 3 (2026-06-06) — After Merge + Refinement

### Assessment (Summary)
- Score: **9/10**
- Verdict: **almost ready**
- Reviewer: deepseek-v4-pro

### Reviewer Raw Response

<details>
<summary>Click to expand full reviewer response</summary>

**Resolved:**
1. Dynamic PTA Updates — Weighted merge with leetcode_weight = min(1, existing/10)
2. Refined Tag Matching — Longest-first sorting prevents false positives

**New Edge-Case Weaknesses (non-blocking):**
1. PTA mastery uses raw success_rate — should use Wilson lower bound for sparse data
2. Mastery scale mismatch between LeetCode (sophisticated) and PTA (crude fraction)
3. Recency blindness — weight depends only on attempt count, not time
4. Forgetting score not updated after merge

**Recommended Refinements:**
- Use Wilson lower bound for pta_mastery
- Adjust forgetting_score based on new PTA activity
- These are incremental improvements, not blocking for first deploy

</details>

### Actions Taken
1. **Wilson lower bound for PTA mastery**: `pta_mastery = wilson_lower(success_rate, attempts) × 100` instead of raw success rate
2. **Forgetting score adjustment**: PTA submissions reduce forgetting: `new_forgetting = max(0, old_forgetting - attempts × 2.0)`
3. Applied to both unified and legacy ingestion paths

### Results
- 18/18 algorithm unit tests pass
- All files pass syntax validation

### Status
- **STOPPING** — Score 9/10 >= 6 AND verdict "almost" ∈ {"ready", "almost"}
- Ready for production integration with spider-repo

---

## Final Summary

### Integration Architecture
```
spider-repo → crawl PTA → MySQL ptadatabase
    ↓ POST /webhook/spider-import
recommendation-service (pta_ingestion.py)
    → reads unified/legacy tables
    → extracts LeetCode tags via pta_tag_mapping
    → merges PTA data with existing skill profiles
    → upserts student_skill_state
    ↓
recommendation pipeline: recall → rank → diversify → recommend
```

### Complete API Surface (14 endpoints)
| Method | Path | Purpose |
|--------|------|---------|
| GET | /health | Health check |
| POST | /ai/profile/update | Update skill after practice |
| POST | /ai/profile/batch-update | Batch update skills |
| POST | /ai/profile/decay | Scheduled forgetting decay |
| GET | /ai/profile/{studentId} | Get student skill profile |
| POST | /ai/profile/initialize | Initialize new student profile |
| POST | /ai/recommendation/generate | Async recommendation |
| GET | /ai/recommendation/result/{requestId} | Poll result |
| POST | /ai/recommendation/exposure | Record exposure |
| POST | /ai/recommendation/feedback | Record feedback |
| POST | /ai/recommendation/sync | Sync recommendation |
| POST | /webhook/spider-import | Spider crawl callback |
| POST | /internal/refresh-student | Manual PTA refresh |
| POST | /internal/refresh-class | Class-wide PTA refresh |

### New Files
1. `app/services/pta_ingestion.py` — PTA data ingestion with merge logic
2. `app/api/webhook.py` — Webhook + internal refresh endpoints
3. `app/schemas/webhook.py` — Pydantic models
4. `sql/spider_integration.sql` — DB migration script

### Modified Files
1. `app/db/mysql_client.py` — 16 new query functions
2. `app/main.py` — Router registration
3. `app/services/recommendation_service.py` — Public get_engine()

### Score Progression
| Round | Score | Verdict |
|-------|-------|---------|
| 1 | 3/10 | not ready |
| 2 | 8/10 | almost |
| 3 | 9/10 | almost |

### Method Description
recommendation-service is a FastAPI microservice that provides AI-driven student skill profiling and personalized LeetCode recommendations. It integrates with spider-repo's PTA crawling data through a webhook-based ingestion pipeline: spider-repo crawls PTA platform data and writes to MySQL's unified/legacy tables, then notifies the recommendation service via POST /webhook/spider-import. The ingestion service reads student_problem_attempt/submit_situation tables, maps PTA keywords to LeetCode skill tags via pta_tag_mapping, and merges submission statistics with existing student_skill_state using a weighted blend (Wilson lower bound for PTA mastery). The recommendation pipeline uses BKT knowledge tracing + Ebbinghaus forgetting curve + Wilson confidence to compute P(recall)=P(L)×R(t), then applies a 6-factor ranking engine with MMR diversity reranking to generate personalized problem recommendations.
