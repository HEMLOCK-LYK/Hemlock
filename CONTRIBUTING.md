# Contributing to Hemlock

感谢你对 Hemlock 项目的关注！本文档介绍了参与项目开发的流程和规范。

## 开发环境搭建

### 前置要求

- Node.js 18+
- Python 3.10+
- Rust (stable) + cargo-tauri 2.x
- Windows 10/11 x64（当前主要支持平台）

### 搭建步骤

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/hemlock.git
cd hemlock

# 2. 后端环境
cd backend
python -m virtualenv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env 配置 LLM API Key

# 3. 前端环境
cd ../frontend
npm install

# 4. 启动开发服务
# 终端 1：启动后端
cd backend && python -m hemlock.cli

# 终端 2：启动前端
cd frontend && npm run dev

# 或使用开发脚本
.\dev.ps1
```

## 项目结构

```
hemlock/
├── backend/           # Python 后端 (FastAPI + mitmproxy)
│   ├── hemlock/
│   │   ├── api/       # API 路由层
│   │   ├── core/      # 核心逻辑 (Agent 编排器、上下文管理)
│   │   ├── models/    # 数据模型
│   │   ├── schemas/   # Pydantic Schema
│   │   └── services/  # 业务服务层
│   └── tests/         # 测试
├── frontend/          # 前端 (React 18 + TypeScript + Vite)
│   └── src/
│       ├── api/       # API 客户端
│       ├── components/# UI 组件
│       ├── views/     # 页面视图
│       ├── context/   # React Context
│       ├── hooks/     # 自定义 Hooks
│       └── utils/     # 工具函数
├── proxy-addon/       # mitmproxy 代理插件
├── src-tauri/         # Tauri 桌面壳 (Rust)
├── docs/              # 项目文档
└── build.ps1          # 构建脚本
```

## 开发规范

### 代码风格

**Python 后端：**
- 遵循 PEP 8 规范
- 使用类型注解（Type Hints）
- 函数和类需添加 docstring
- 异步优先，避免阻塞事件循环

**TypeScript 前端：**
- 严格模式 TypeScript
- 函数组件 + Hooks 模式
- 性能敏感组件使用 `memo` 包裹
- 事件处理函数使用 `useCallback` 稳定引用

### Git 提交规范

使用 Conventional Commits 格式：

```
<type>(<scope>): <subject>

<body>
```

**Type 类型：**
- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 文档更新
- `refactor`: 代码重构
- `perf`: 性能优化
- `test`: 测试相关
- `chore`: 构建/工具变更

**示例：**
```
feat(agent): 添加上下文压缩机制
fix(proxy): 修复流量未被记录的问题
docs(readme): 更新安装说明
```

### 分支策略

- `main`: 稳定发布分支
- `dev`: 开发分支
- `feature/<name>`: 功能分支
- `fix/<name>`: 修复分支

## Pull Request 流程

1. 从 `dev` 分支创建功能分支
2. 开发并确保本地测试通过
3. 提交 PR 到 `dev` 分支
4. PR 需包含：
   - 清晰的变更描述
   - 相关 issue 链接（如有）
   - 测试说明
5. 等待 Code Review

## 测试

```bash
# 后端测试
cd backend
.\.venv\Scripts\python.exe -m pytest -v

# 前端构建检查
cd frontend
npm run build
```

## 安全相关

Hemlock 是安全测试工具，开发时请注意：

- 不要在代码中硬编码 API Key 或敏感信息
- 不要提交 `.env` 文件或数据库文件
- 渗透测试功能需遵循授权测试原则
- 敏感操作（DoS、注入）需有确认机制

## 问题反馈

- Bug 报告：请使用 GitHub Issues，附上复现步骤和环境信息
- 功能建议：欢迎在 Issues 或 Discussions 中讨论
- 安全漏洞：请私聊维护者，不要公开 Issue

## License

提交的代码将遵循 MIT License。
