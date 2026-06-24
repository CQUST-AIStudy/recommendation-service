-- ========== LeetCode 题向量表 (方案 C: 向量语义检索) ==========
-- 创建时间: 2026-06-24
-- 说明: 题向量通过 BAAI/bge-small-zh (或类似) 模型离线预计算,
--       存为 BLOB。在线推荐时直接加载做余弦相似度,无 GPU 依赖。
--       表可选 - 没建表/没数据时推荐服务优雅降级到方案 A+B。

CREATE TABLE IF NOT EXISTS leetcode_problem_embedding (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    problem_id BIGINT NOT NULL,
    model_name VARCHAR(128) NOT NULL COMMENT '编码模型名,如 BAAI/bge-small-zh',
    dim INT NOT NULL COMMENT '向量维度',
    embedding_blob BLOB NOT NULL COMMENT '紧凑二进制 (little-endian float32)',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_problem_model (problem_id, model_name),
    KEY idx_model (model_name),
    CONSTRAINT fk_embedding_problem
      FOREIGN KEY (problem_id) REFERENCES leetcode_problem_bank(id)
      ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LeetCode 题向量表(方案 C)';
