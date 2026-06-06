-- ============================================================
-- Spider-Repo 集成 — 数据库迁移脚本
-- 在 ptadatabase 上执行
-- ============================================================

-- 1. PTA → LeetCode 标签映射表
CREATE TABLE IF NOT EXISTS `pta_tag_mapping` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `pta_keyword` VARCHAR(128) NOT NULL COMMENT 'PTA 题目关键词（中文）',
    `leetcode_tag` VARCHAR(128) NOT NULL COMMENT 'LeetCode 技能标签名',
    `relevance` FLOAT NOT NULL DEFAULT 0.8 COMMENT '关联强度 [0, 1]',
    UNIQUE KEY `uk_keyword_tag` (`pta_keyword`, `leetcode_tag`),
    INDEX `idx_keyword` (`pta_keyword`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='PTA关键词到LeetCode标签的映射表';

-- 2. 插入默认映射数据
INSERT IGNORE INTO `pta_tag_mapping` (`pta_keyword`, `leetcode_tag`, `relevance`) VALUES
-- 基础数据结构
('数组', '数组', 0.9),
('字符串', '字符串', 0.9),
('链表', '链表', 0.95),
('栈', '栈', 0.95),
('队列', '队列', 0.95),
('哈希', '哈希表', 0.9),
('散列表', '哈希表', 0.9),
-- 树与图
('树', '树', 0.9),
('二叉树', '树', 0.95),
('AVL', '树', 0.85),
('图', '图', 0.9),
('广度优先', '图', 0.7),
('深度优先', '图', 0.7),
('最短路径', '图', 0.85),
('最小生成树', '图', 0.8),
('拓扑排序', '图', 0.8),
-- 算法策略
('排序', '排序', 0.95),
('查找', '二分查找', 0.8),
('二分', '二分查找', 0.9),
('递归', '递归', 0.9),
('分治', '分治', 0.9),
('动态规划', '动态规划', 0.9),
('贪心', '贪心', 0.9),
('回溯', '回溯', 0.9),
-- 其他
('堆', '堆', 0.9),
('并查集', '并查集', 0.85),
('位运算', '位运算', 0.85),
('滑动窗口', '滑动窗口', 0.9),
('双指针', '双指针', 0.9),
-- 经典算法
('Dijkstra', '图', 0.85),
('Floyd', '图', 0.8),
('Prim', '图', 0.7),
('Kruskal', '图', 0.7),
('Huffman', '贪心', 0.8),
('KMP', '字符串', 0.8),
('归并排序', '排序', 0.8),
('快速排序', '排序', 0.85);

-- ============================================================
-- 以下为 spider-repo unified schema 中推荐服务依赖的表
-- 如果 spider-repo 的 sync_to_unified_db.py 已运行，这些表应已存在。
-- 此处仅为参考，不需要在推荐服务端单独创建。
-- ============================================================

-- student_profile: 学生基础信息
-- student_problem_attempt: 学生每次提交记录
-- student_problem_state: 学生每题最新状态
-- assignment_problem: 题目信息
-- class_member: 班级成员关系
-- assignment_offering: 题目集布置信息

-- ============================================================
-- 注意事项:
-- 1. student_skill_state.student_id 与 student_profile.id 对齐
-- 2. 如果使用 legacy schema, student_id 与 student.student_id 对齐
-- 3. 推荐服务通过 pta_ingestion.py 自动检测使用哪种 schema
-- ============================================================
