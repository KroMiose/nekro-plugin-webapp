# 开发文档

> 面向开发者的完整开发指南

## 📋 目录

- [环境要求](#环境要求)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [开发流程](#开发流程)
- [测试指南](#测试指南)
- [部署流程](#部署流程)
- [常见问题](#常见问题)

## 🔧 环境要求

### 必需工具

| 工具                | 版本要求  | 用途                       |
| ------------------- | --------- | -------------------------- |
| **Node.js**         | >= 18.0.0 | Worker 开发和构建          |
| **npm** 或 **pnpm** | 最新版本  | 包管理器                   |
| **Python**          | >= 3.10   | 插件开发                   |
| **Wrangler CLI**    | >= 3.0.0  | Cloudflare Worker 开发工具 |

### 可选工具

- **Git** - 版本控制
- **VSCode** - 推荐编辑器（支持 TypeScript 和 Python）
- **Cloudflare 账号** - 用于部署测试

## 📁 项目结构

```
nekro-plugin-webapp/
├── 📦 Python 插件部分
│   ├── __init__.py          # 插件入口，沙盒方法
│   ├── plugin.py            # 插件配置
│   ├── models.py            # Pydantic 数据模型
│   ├── handlers.py          # FastAPI 路由
│   └── pyproject.toml       # Python 依赖配置
│
├── 🌐 Cloudflare Worker 部分
│   └── worker/
│       ├── src/
│       │   ├── index.ts     # Worker 主入口
│       │   ├── init.ts      # 数据库初始化
│       │   ├── auth.ts      # 认证模块
│       │   ├── storage.ts   # 数据库操作
│       │   └── types.ts     # TypeScript 类型定义
│       ├── schema.sql       # D1 数据库 Schema
│       ├── wrangler.toml    # Worker 配置文件
│       ├── package.json     # Node.js 依赖
│       └── tsconfig.json    # TypeScript 配置
│
├── 🎨 管理界面
│   └── static/
│       └── index.html       # Vue 3 单文件应用
│
└── 📚 文档
    ├── README.md            # 用户文档
    ├── DEPLOYMENT.md        # 部署指南
    ├── DEVELOPMENT.md       # 本文档
    └── prompt.md            # 项目构建提示词
```

## 🚀 快速开始

### 1. 克隆项目

如果你是从 NekroAgent 插件目录开始：

```bash
cd data/nekro_agent/plugins/workdir/nekro-plugin-webapp/
```

如果你是独立开发这个项目：

```bash
git clone https://github.com/your-username/nekro-plugin-webapp.git
cd nekro-plugin-webapp
```

### 2. 安装 Python 依赖

推荐使用 `uv`（超快速 Python 包管理器）：

```bash
# 安装 uv（如果没有）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖
uv sync
```

或使用传统的 pip：

```bash
pip install -e .
```

### 3. 安装 Worker 依赖

```bash
cd worker

# 使用 pnpm
pnpm install
```

### 4. 安装 Wrangler CLI

Wrangler 是 Cloudflare Workers 的官方 CLI 工具：

```bash
# 全局安装
npm install -g wrangler

# 或使用 pnpm
pnpm add -g wrangler

# 验证安装
wrangler --version
```

### 5. 登录 Cloudflare

```bash
wrangler login
```

这会打开浏览器，让你授权 Wrangler 访问你的 Cloudflare 账号。

## 💻 开发流程

### Python 插件开发

#### 本地运行 NekroAgent

```bash
# 回到项目根目录
cd /path/to/nekro-agent

# 启动 NekroAgent
python -m nekro_agent
```

插件会自动加载，访问：`http://localhost:8021/plugins/nekro_plugin_webapp/`

#### 修改插件代码

1. **修改配置**：编辑 `plugin.py`
2. **修改数据模型**：编辑 `models.py`
3. **修改沙盒方法**：编辑 `__init__.py`
4. **修改路由**：编辑 `handlers.py`

修改后需要重启 NekroAgent 才能生效。

#### 代码规范

```bash
# 使用 ruff 检查代码（NekroAgent 项目自带）
ruff check .

# 自动修复
ruff check --fix .

# 格式化代码
ruff format .
```

### Worker 开发

#### 本地开发服务器

```bash
cd worker

# 启动开发服务器
wrangler dev

# 或指定端口
wrangler dev --port 8787
```

这会启动一个本地 Worker，通常在 `http://localhost:8787`。

**特性：**

- 🔥 热重载：修改代码自动刷新
- 📊 实时日志：在终端查看请求日志
- 🗄️ 本地 D1：使用 SQLite 模拟 D1 数据库

#### 创建本地 D1 数据库

```bash
# 创建本地测试数据库
wrangler d1 create webapp-db-local

# 复制返回的 database_id
# 更新 wrangler.toml 中的 database_id
```

#### 初始化本地数据库

```bash
# 执行 schema
wrangler d1 execute webapp-db-local --file=schema.sql --local

# 查看表结构
wrangler d1 execute webapp-db-local --command="SELECT name FROM sqlite_master WHERE type='table';" --local
```

#### 查询本地数据库

```bash
# 查看所有页面
wrangler d1 execute webapp-db-local --command="SELECT * FROM pages;" --local

# 查看所有密钥
wrangler d1 execute webapp-db-local --command="SELECT key_id, key_name, usage_count FROM api_keys;" --local

# 插入测试数据
wrangler d1 execute webapp-db-local --command="INSERT INTO pages (page_id, title, description, html_content, created_by, created_at, is_active) VALUES ('test123', 'Test Page', 'Test Description', '<html><body>Test</body></html>', 'admin', $(date +%s)000, 1);" --local
```

#### 环境变量管理

**开发环境**（使用 `.dev.vars` 文件）：

```bash
cd worker

# 创建 .dev.vars 文件
cat > .dev.vars << EOF
DB_INITIALIZED=false
EOF
```

`.dev.vars` 文件会被 `.gitignore` 忽略，不会提交到仓库。

## 📦 构建和部署

### 本地构建

Worker 构建由 Wrangler 自动处理，无需手动构建。

**检查配置：**

```bash
cd worker

# 验证配置文件
wrangler whoami

# 验证 wrangler.toml
cat wrangler.toml
```

### 部署到 Cloudflare

**方式一：自动部署（推荐）**

连接 GitHub 仓库后，每次推送都会自动部署：

```bash
git add .
git commit -m "feat: add new feature"
git push origin main
```

Cloudflare 会自动检测更改并部署。

**方式二：手动部署**

```bash
cd worker

# 部署到生产环境
wrangler deploy

# 部署到预览环境
wrangler deploy --env preview
```

### 创建生产数据库

```bash
# 创建生产 D1 数据库
wrangler d1 create webapp-db

# 初始化生产数据库
wrangler d1 execute webapp-db --file=schema.sql

# 更新 wrangler.toml 中的 database_id
```

### 注意事项

管理员密钥存储在数据库中：

- 首次部署后访问管理界面
- 在首次设置页面设置管理员密钥
- 密钥存储在 D1 数据库的 `settings` 表中
- 无需配置任何环境变量

### 发布流程

1. **本地测试**：确保所有功能正常
2. **提交代码**：
   ```bash
   git add .
   git commit -m "feat: your changes"
   ```
3. **推送到 GitHub**：
   ```bash
   git push origin main
   ```
4. **验证部署**：
   - 访问 Cloudflare Dashboard 查看部署状态
   - 测试生产环境 API
5. **回滚**（如果需要）：
   - 在 Cloudflare Dashboard 中选择之前的部署版本
   - 点击 "Rollback to this deployment"

## 🔍 常见问题

### Q: Wrangler 命令找不到？

```bash
# 确认安装
npm list -g wrangler

# 重新安装
npm install -g wrangler@latest
```

### Q: D1 数据库连接失败？

检查：

1. `wrangler.toml` 中的 `database_id` 是否正确
2. 数据库是否已创建：`wrangler d1 list`
3. 绑定名称是否为 `DB`

### Q: 如何清空本地数据库？

```bash
# 删除所有页面
wrangler d1 execute webapp-db-local --command="DELETE FROM pages;" --local

# 删除所有密钥
wrangler d1 execute webapp-db-local --command="DELETE FROM api_keys;" --local

# 重置自增ID
wrangler d1 execute webapp-db-local --command="DELETE FROM sqlite_sequence;" --local
```

### Q: TypeScript 类型错误？

```bash
# 更新类型定义
npm install --save-dev @cloudflare/workers-types@latest

# 清理缓存
rm -rf node_modules package-lock.json
npm install
```

## 📚 推荐资源

### 项目文档

- [README.md](./README.md) - 用户文档和部署指南
- [QUICK_START.md](./QUICK_START.md) - 常用命令速查表
- [CONTRIBUTING.md](./CONTRIBUTING.md) - 如何贡献代码

### 官方文档

- [Cloudflare Workers 文档](https://developers.cloudflare.com/workers/)
- [D1 数据库文档](https://developers.cloudflare.com/d1/)
- [Wrangler CLI 文档](https://developers.cloudflare.com/workers/wrangler/)
- [TypeScript 文档](https://www.typescriptlang.org/docs/)

## 🤝 贡献指南

### 代码风格

- **Python**: 遵循 PEP 8，使用 `ruff` 检查
- **TypeScript**: 使用 Prettier 格式化
- **注释**: 关键逻辑添加注释
- **类型**: TypeScript 严格模式，完整类型注解

### 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat: 添加新功能
fix: 修复 bug
docs: 更新文档
style: 代码格式调整
refactor: 重构代码
test: 添加测试
chore: 构建/工具相关
```

### Pull Request 流程

1. Fork 项目
2. 创建特性分支：`git checkout -b feature/amazing-feature`
3. 提交更改：`git commit -m 'feat: add amazing feature'`
4. 推送分支：`git push origin feature/amazing-feature`
5. 创建 Pull Request

---

**Happy Coding! 🎉**

如有问题，请提交 [Issue](https://github.com/your-repo/issues) 或加入我们的讨论组。
