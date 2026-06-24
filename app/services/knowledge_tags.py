"""
知识点词典 + 相关性打分 — 方案 A 的核心模块。

这个模块替代了 recall.py / ranking.py 里散落的 _TAG_MAP(20 项硬编码)和
_COURSE_WEIGHTS,统一维护:
  - 知识点中文名 (用作 student_skill_state.tag_name 的对齐键)
  - 英文同义词列表 (匹配 LeetCode 英文题面)
  - 中文关键词列表 (匹配 PTA/中文翻译题面)
  - 课程权重 (用于 ranking.compute_need 的 w_course 项)
  - 分类 (algorithm / data_structure / technique,对齐 DB 枚举)

打分函数 tag_relevance_score 把"子串 in"升级成"加权相关度 [0,1]":
  - 中文关键词命中 → 1.0  (强信号)
  - 英文整词命中   → 0.8  (中信号)
  - 英文前缀命中   → 0.5  (弱信号)
  - 标题命中加 0.2 奖励 (上限 1.0)
  - 全部不命中     → 0.0
"""
from __future__ import annotations

from typing import Any

# ──────────────────────────────────────────
# 知识点词典 (50+ 项,覆盖《数据结构》常见考点)
# ──────────────────────────────────────────

KNOWLEDGE_TAGS: dict[str, dict[str, Any]] = {
    # ===== 数据结构 =====
    "数组": {
        "category": "data_structure",
        "en": ["array", "arrays"],
        "zh": ["数组"],
        "course_weight": 0.8,
    },
    "字符串": {
        "category": "data_structure",
        "en": ["string", "strings"],
        "zh": ["字符串"],
        "course_weight": 0.7,
    },
    "链表": {
        "category": "data_structure",
        "en": ["linked list", "linked-list", "listnode"],
        "zh": ["链表", "单链表", "双向链表"],
        "course_weight": 0.9,
    },
    "栈": {
        "category": "data_structure",
        "en": ["stack", "stacks"],
        "zh": ["栈"],
        "course_weight": 0.8,
    },
    "队列": {
        "category": "data_structure",
        "en": ["queue", "queues", "deque"],
        "zh": ["队列", "双端队列"],
        "course_weight": 0.8,
    },
    "哈希表": {
        "category": "data_structure",
        "en": ["hash table", "hash map", "hashtable", "hashmap", "hashing"],
        "zh": ["哈希", "散列"],
        "course_weight": 0.7,
    },
    "树": {
        "category": "data_structure",
        "en": ["tree", "treenode", "binary tree", "bst", "binary search tree"],
        "zh": ["树", "二叉树", "二叉搜索树", "二叉查找树"],
        "course_weight": 0.9,
    },
    "二叉搜索树": {
        "category": "data_structure",
        "en": ["binary search tree", "bst"],
        "zh": ["二叉搜索树", "二叉查找树", "二叉排序树"],
        "course_weight": 0.8,
    },
    "堆": {
        "category": "data_structure",
        "en": ["heap", "priority queue", "min-heap", "max-heap"],
        "zh": ["堆", "优先队列"],
        "course_weight": 0.6,
    },
    "图": {
        "category": "data_structure",
        "en": ["graph", "graphs", "directed graph", "undirected graph"],
        "zh": ["图", "有向图", "无向图"],
        "course_weight": 0.7,
    },
    "并查集": {
        "category": "data_structure",
        "en": ["union find", "union-find", "disjoint set", "dsu"],
        "zh": ["并查集", "不相交集合"],
        "course_weight": 0.4,
    },
    "字典树": {
        "category": "data_structure",
        "en": ["trie", "prefix tree"],
        "zh": ["字典树", "前缀树", "trie 树"],
        "course_weight": 0.5,
    },
    "线段树": {
        "category": "data_structure",
        "en": ["segment tree"],
        "zh": ["线段树"],
        "course_weight": 0.4,
    },
    "树状数组": {
        "category": "data_structure",
        "en": ["binary indexed tree", "fenwick tree", "bit "],
        "zh": ["树状数组", "fenwick"],
        "course_weight": 0.3,
    },

    # ===== 算法 =====
    "排序": {
        "category": "algorithm",
        "en": ["sort", "sorting", "sorted", "merge sort", "quick sort", "quick-sort"],
        "zh": ["排序", "归并", "快排"],
        "course_weight": 0.8,
    },
    "二分查找": {
        "category": "algorithm",
        "en": ["binary search", "bisection", "bisect"],
        "zh": ["二分查找", "二分", "折半查找"],
        "course_weight": 0.7,
    },
    "深度优先搜索": {
        "category": "algorithm",
        "en": ["dfs", "depth first search", "depth-first search"],
        "zh": ["深度优先", "dfs"],
        "course_weight": 0.7,
    },
    "广度优先搜索": {
        "category": "algorithm",
        "en": ["bfs", "breadth first search", "breadth-first search"],
        "zh": ["广度优先", "bfs"],
        "course_weight": 0.7,
    },
    "回溯": {
        "category": "algorithm",
        "en": ["backtrack", "backtracking"],
        "zh": ["回溯", "回溯法"],
        "course_weight": 0.6,
    },
    "递归": {
        "category": "algorithm",
        "en": ["recursion", "recursive"],
        "zh": ["递归"],
        "course_weight": 0.7,
    },
    "分治": {
        "category": "algorithm",
        "en": ["divide and conquer", "divide-and-conquer"],
        "zh": ["分治", "分治法"],
        "course_weight": 0.6,
    },
    "动态规划": {
        "category": "algorithm",
        "en": ["dynamic programming", " dp ", "dp,", "dp.", "dp:", "(dp"],
        "zh": ["动态规划", "状态转移", "记忆化", "背包问题", "数位 dp"],
        "course_weight": 0.6,
    },
    "贪心": {
        "category": "algorithm",
        "en": ["greedy", "greedy algorithm"],
        "zh": ["贪心", "贪心算法", "贪心策略"],
        "course_weight": 0.6,
    },
    "枚举": {
        "category": "algorithm",
        "en": ["brute force", "enumeration", "enumerate"],
        "zh": ["枚举", "暴力"],
        "course_weight": 0.4,
    },
    "模拟": {
        "category": "algorithm",
        "en": ["simulation", "simulate"],
        "zh": ["模拟"],
        "course_weight": 0.4,
    },

    # ===== 技巧 =====
    "双指针": {
        "category": "technique",
        "en": ["two pointer", "two-pointer", "two pointers"],
        "zh": ["双指针"],
        "course_weight": 0.7,
    },
    "滑动窗口": {
        "category": "technique",
        "en": ["sliding window", "sliding-window"],
        "zh": ["滑动窗口"],
        "course_weight": 0.7,
    },
    "前缀和": {
        "category": "technique",
        "en": ["prefix sum", "prefix-sum", "cumulative sum", "running sum"],
        "zh": ["前缀和", "差分"],
        "course_weight": 0.6,
    },
    "差分数组": {
        "category": "technique",
        "en": ["difference array", "difference-array", "diff array"],
        "zh": ["差分", "差分数组"],
        "course_weight": 0.4,
    },
    "单调栈": {
        "category": "technique",
        "en": ["monotonic stack", "monotone stack"],
        "zh": ["单调栈"],
        "course_weight": 0.5,
    },
    "单调队列": {
        "category": "technique",
        "en": ["monotonic queue", "monotone queue"],
        "zh": ["单调队列"],
        "course_weight": 0.4,
    },
    "位运算": {
        "category": "technique",
        "en": ["bit manipulation", "bitwise", "bit operation"],
        "zh": ["位运算", "位操作"],
        "course_weight": 0.4,
    },
    "状态压缩": {
        "category": "technique",
        "en": ["bitmask", "bit mask", "state compression"],
        "zh": ["状态压缩", "状压"],
        "course_weight": 0.4,
    },
    "快速幂": {
        "category": "technique",
        "en": ["fast exponentiation", "matrix power", "quick pow"],
        "zh": ["快速幂", "矩阵快速幂"],
        "course_weight": 0.4,
    },
    "离散化": {
        "category": "technique",
        "en": ["discretization", "coordinate compression"],
        "zh": ["离散化"],
        "course_weight": 0.4,
    },
    "哈希算法": {
        "category": "technique",
        "en": ["rolling hash", "rabin karp", "rabin-karp"],
        "zh": ["字符串哈希", "滚哈"],
        "course_weight": 0.3,
    },
    "KMP": {
        "category": "technique",
        "en": ["kmp", "knuth morris pratt"],
        "zh": ["kmp", "模式匹配"],
        "course_weight": 0.3,
    },
    "拓扑排序": {
        "category": "technique",
        "en": ["topological sort", "topology sort"],
        "zh": ["拓扑排序", "拓扑"],
        "course_weight": 0.5,
    },
    "最短路": {
        "category": "technique",
        "en": ["shortest path", "dijkstra", "floyd", "bellman-ford", "spfa"],
        "zh": ["最短路", "最短路径", "迪杰斯特拉"],
        "course_weight": 0.5,
    },
    "最小生成树": {
        "category": "technique",
        "en": ["minimum spanning tree", "mst", "kruskal", "prim"],
        "zh": ["最小生成树", "kruskal", "prim"],
        "course_weight": 0.4,
    },
    "强连通分量": {
        "category": "technique",
        "en": ["strongly connected component", "scc", "tarjan"],
        "zh": ["强连通分量", "tarjan"],
        "course_weight": 0.3,
    },
    "网络流": {
        "category": "technique",
        "en": ["max flow", "min cut", "network flow", "dinic"],
        "zh": ["网络流", "最大流", "最小割"],
        "course_weight": 0.2,
    },
    "矩阵": {
        "category": "data_structure",
        "en": ["matrix", "matrices", "2d array", "2-d array"],
        "zh": ["矩阵"],
        "course_weight": 0.4,
    },
    "邻接表": {
        "category": "data_structure",
        "en": ["adjacency list", "adjacency-list"],
        "zh": ["邻接表"],
        "course_weight": 0.5,
    },
    "邻接矩阵": {
        "category": "data_structure",
        "en": ["adjacency matrix", "adjacency-matrix"],
        "zh": ["邻接矩阵"],
        "course_weight": 0.4,
    },
    "数学": {
        "category": "algorithm",
        "en": ["math", "mathematics", "number theory"],
        "zh": ["数学", "数论"],
        "course_weight": 0.4,
    },
    "几何": {
        "category": "algorithm",
        "en": ["geometry", "computational geometry", "convex hull"],
        "zh": ["几何", "计算几何"],
        "course_weight": 0.3,
    },
    "博弈论": {
        "category": "algorithm",
        "en": ["game theory", "minimax", "nim game", "sg function"],
        "zh": ["博弈论", "博弈", "minimax", "nim 博弈"],
        "course_weight": 0.3,
    },
    "随机化": {
        "category": "algorithm",
        "en": ["randomized", "randomized algorithm", "random shuffle"],
        "zh": ["随机化", "随机算法"],
        "course_weight": 0.2,
    },
    "拒绝采样": {
        "category": "algorithm",
        "en": ["rejection sampling", "rejection-sampling"],
        "zh": ["拒绝采样"],
        "course_weight": 0.2,
    },
    "蓄水池抽样": {
        "category": "algorithm",
        "en": ["reservoir sampling", "reservoir-sampling"],
        "zh": ["蓄水池抽样"],
        "course_weight": 0.2,
    },
    "二叉树遍历": {
        "category": "technique",
        "en": ["tree traversal", "inorder", "preorder", "postorder", "level order"],
        "zh": ["二叉树遍历", "前序遍历", "中序遍历", "后序遍历", "层序遍历"],
        "course_weight": 0.7,
    },
    "二分答案": {
        "category": "algorithm",
        "en": ["binary search on answer", "binary search answer", "binary search on value"],
        "zh": ["二分答案"],
        "course_weight": 0.4,
    },
    "状态压缩动态规划": {
        "category": "algorithm",
        "en": ["bitmask dp", "profile dp", "broken profile dp"],
        "zh": ["状压动态规划", "状压 dp", "状压"],
        "course_weight": 0.3,
    },
}


# ──────────────────────────────────────────
# 反向索引:小写英文/中文关键词 → (tag, weight)
# 用于在线打分时 O(n) 扫一遍文本就能定位命中的 tag
# ──────────────────────────────────────────

def _build_keyword_index() -> dict[str, list[tuple[str, float, str]]]:
    """keyword → [(tag_name, hit_weight, source), ...]"""
    index: dict[str, list[tuple[str, float, str]]] = {}
    for tag, meta in KNOWLEDGE_TAGS.items():
        for kw in meta["zh"]:
            # 中文关键词命中 = 强信号 1.0
            index.setdefault(kw, []).append((tag, 1.0, "zh"))
        for kw in meta["en"]:
            kw_l = kw.lower()
            # 英文整词/短语命中 = 中信号 0.8
            index.setdefault(kw_l, []).append((tag, 0.8, "en"))
    return index


_KEYWORD_INDEX = _build_keyword_index()


def get_tag_meta(tag_name: str) -> dict[str, Any] | None:
    """获取某个 tag 的元数据(category, course_weight, 同义词)。"""
    return KNOWLEDGE_TAGS.get(tag_name)


def get_course_weight(tag_name: str) -> float:
    """对齐旧 _COURSE_WEIGHTS 的行为,默认 0.5。"""
    meta = KNOWLEDGE_TAGS.get(tag_name)
    return float(meta["course_weight"]) if meta else 0.5


def get_english_synonyms(tag_name: str) -> list[str]:
    """对齐旧 _get_english_tag 的行为,返回英文同义词列表。"""
    meta = KNOWLEDGE_TAGS.get(tag_name)
    return meta["en"] if meta else []


def all_tag_names() -> list[str]:
    """返回所有知识点名(用于离线打标脚本遍历)。"""
    return list(KNOWLEDGE_TAGS.keys())


# ──────────────────────────────────────────
# 相关性打分
# ──────────────────────────────────────────

def tag_relevance_score(
    title: str,
    text: str,
    tag_name: str,
) -> float:
    """
    计算题目与某个知识点 tag 的相关性分数 ∈ [0, 1]。

    评分规则(标题是更强的信号,因为题目标题往往直接点明考点):
      - 中文关键词命中正文 → 1.0
      - 英文整词命中正文  → 0.8
      - 任意语言关键词命中标题 → 直接 1.0(标题命中 = 几乎确定是这考点)
      - 标题命中 + 正文也命中 → 1.0 (双重信号)

    Parameters
    ----------
    title : str
        title_main 字段。
    text : str
        problem_text + solution_text 拼成的全文。
    tag_name : str
        知识点中文名。

    Returns
    -------
    float
        相关性分数。未命中返回 0.0。
    """
    meta = KNOWLEDGE_TAGS.get(tag_name)
    if not meta:
        # 未收录的 tag:回退到最朴素的子串匹配,但降权 (0.5)
        tag_lower = tag_name.lower()
        combined = (title + " " + text).lower()
        return 0.5 if tag_lower and tag_lower in combined else 0.0

    title_l = (title or "").lower()
    text_l = (text or "").lower()

    body_score = 0.0
    title_hit = False

    for kw in meta["zh"]:
        if not kw:
            continue
        if kw in title:
            title_hit = True
        if kw in text:
            body_score = max(body_score, 1.0)

    for kw in meta["en"]:
        if not kw:
            continue
        kw_l = kw.lower()
        if kw_l in title_l:
            title_hit = True
        if kw_l in text_l:
            body_score = max(body_score, 0.8)

    # 标题命中 = 强信号,直接给 1.0
    if title_hit:
        return 1.0

    return body_score


def detect_tags_for_problem(
    title: str,
    text: str,
    min_score: float = 0.5,
    max_tags: int = 5,
) -> list[tuple[str, float, str]]:
    """
    对一道题扫所有知识点,返回 Top-K 候选标签。

    用于离线打标脚本:对 leetcode_problem_tag 表里没有标签的题批量补全。

    Returns
    -------
    list[(tag_name, score, category)]
        按 score 降序、最多 max_tags 条,所有 score >= min_score。
    """
    hits: list[tuple[str, float, str]] = []
    for tag_name, meta in KNOWLEDGE_TAGS.items():
        score = tag_relevance_score(title, text, tag_name)
        if score >= min_score:
            hits.append((tag_name, score, meta["category"]))

    hits.sort(key=lambda x: x[1], reverse=True)
    return hits[:max_tags]
