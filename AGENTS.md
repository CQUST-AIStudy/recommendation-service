# AGENTS.md

## 语言与协作

- 默认使用中文说明、计划、总结与验证结果。
- 代码标识符、库名、接口字段名、命令参数可保留英文。
- 修改前先阅读相关代码、配置、类型定义与调用链，优先最小必要改动。
- 不修改与当前任务无关的文件，不回退用户已有改动。

## 项目技术栈

- 本项目是 Python 3.11 + FastAPI 微服务。
- 依赖管理使用 `uv`，以 `pyproject.toml` 和 `uv.lock` 为准。
- 服务入口为 `app.main:app`，默认端口 `8003`。
- 配置通过环境变量或 `.env` 读取，核心配置位于 `app/core/config.py`。
- MySQL 访问集中在 `app/db/mysql_client.py`。

## Docker 部署约定

- 推荐服务容器名与服务名默认使用 `recommendation-service`。
- 容器内监听 `0.0.0.0:8003`。
- 同服务器其他容器通过 Docker 网络服务名访问：
  - `http://recommendation-service:8003`
- 数据库默认通过 Docker 网络服务名访问：
  - `DB_HOST=mysql`
  - `DB_PORT=3306`
- 默认使用外部 Docker 网络 `cqust-ai-net`；其他服务需要加入同一网络。
- 不在仓库中提交真实 `.env`、`.env.docker` 或任何敏感信息。

## Python 规范

- 必须使用类型标注。
- FastAPI 路由保持轻量，业务逻辑放在 Service 层或等价业务层。
- 请求和响应优先使用 Pydantic。
- 明确处理错误，不吞错、不静默失败。

## 验证

- 算法测试优先运行：

```powershell
uv run pytest tests/test_algorithms.py -v
```

- 静态检查优先运行：

```powershell
uv run ruff check .
```

- Docker 配置和构建验证优先运行：

```powershell
docker compose config
docker compose build
```

- 未执行验证时，不声称已验证通过。
