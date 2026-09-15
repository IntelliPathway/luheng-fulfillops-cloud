# 贡献指南

## 开发环境

- Python 3.12
- Node.js 22
- Docker Engine 24+ 与 Docker Compose v2（集成验证）

首次安装见根目录 [README.md](README.md)。

## 分支与提交

- 从最新 `main` 创建短生命周期分支；
- 一个提交只表达一个可回滚的意图；
- 推荐 Conventional Commits：`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`；
- 不提交 `.env`、数据库、真实客户数据、密钥、Token 或临时构建缓存；
- 不改写他人尚未合并的工作，不使用破坏性 Git 命令清理未知改动。

## Python 规范

- Ruff 是唯一格式与静态风格工具；配置在 `backend/pyproject.toml`；
- 新业务逻辑优先放入领域模块，不继续把所有实现塞进 `main.py`；
- 路由只负责输入、身份、状态码和输出映射；事务规则放到应用/领域服务；
- 金额使用整数分，时间写入 UTC，租户查询必须显式带 `tenant_id`；
- 高影响写操作必须考虑角色、Maker–Checker、幂等、版本和审计；
- 生产表结构必须通过顺序 SQL 迁移，不能依赖 `create_all`。

```bash
backend/.venv/bin/python -m ruff check backend/app backend/tests
backend/.venv/bin/python -m ruff format backend/app backend/tests
```

## 前端规范

- 领域状态转换优先抽成无副作用模块并用 Node Test Runner 测试；
- API 在线后禁止静默回退到浏览器写入；
- 401/403 与 API 配置错误必须区别于真正断网；
- 生产构建不得自动发送开发身份；
- 密钥只能进入请求的 `credential` 字段，不能写入 React 状态持久化或 `VITE_*`；
- 新页面遵循现有轻量 SaaS 工作台和移动端回流规则。

## 测试要求

提交前执行：

```bash
npm run verify
```

变更至少覆盖：

- 成功路径；
- 无权限、跨租户、自审或陈旧版本；
- 重复请求和冲突幂等键；
- Provider、数据库或 Worker 失败；
- 不应写入的敏感内容；
- 需要迁移时的 PostgreSQL 原地升级。

业务功能变化还应更新 `docs/product-functional-spec.md`；运行配置变化更新 `docs/configuration.md`；架构边界变化更新 `docs/architecture.md`；每次发布更新 `CHANGELOG.md` 和对应发布说明。

## Pull Request 完成定义

- [ ] 代码已经格式化且无 Ruff 错误；
- [ ] 后端与前端测试通过；
- [ ] 生产构建通过且没有新的大包警告；
- [ ] PostgreSQL 迁移可从旧基线升级；
- [ ] 安全默认值失败关闭；
- [ ] 文档与代码一致；
- [ ] 没有凭证、数据库或客户数据；
- [ ] CI 对最终提交为绿色。
