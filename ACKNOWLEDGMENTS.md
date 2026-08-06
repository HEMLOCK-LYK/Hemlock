# 致谢 / Acknowledgments

Hemlock 站在许多优秀项目和工具的肩膀上，在此致以诚挚的感谢。

## 内容来源

- **Yakit hack-skills（yaklang 团队）**：内置技能库的直接来源——99 个安全技能基于其官方发布的技能集合（https://github.com/yaklang/hack-skills ，MIT License）整理，署名按原项目保留。

## 灵感与设计借鉴

- **Burp Suite（PortSwigger）**：Repeater、Intruder、漏洞管理等产品功能与交互设计的灵感来源。
- **Hae（gh0stkey）**：FlowTag 正则自动标注功能的灵感来源。
- **ProjectDiscovery Nuclei**：模板化漏洞扫描的设计思路（尚未集成，N-day 自动验证的规划方向）。
- **Claude Code（Anthropic）**：上下文管理架构的启发——稳定缓存前缀、索引 + 按需、渐进式压缩。
- **OWASP**：渗透测试方法论与策略库（OWASP Top 10）的参考。

## 核心依赖（直接使用/集成）

- **mitmproxy**（MIT License）：HTTP/HTTPS 抓包代理引擎。
- **FastAPI / Uvicorn**：后端框架。
- **Tauri**：桌面应用框架。
- **React / Vite**：前端框架。
- **SQLAlchemy / SQLite**：数据持久化。
- **openai SDK**：LLM 接入层（兼容 DeepSeek / OpenAI / 通义等）。

## 情报与数据源

- **NVD（NIST）**：CVE 数据源。
- **cve.circl.lu**：CVE 检索 API。
- **DuckDuckGo**：联网搜索。
- **FOFA / Quake / Hunter / ZoomEye**：资产测绘引擎。
- **crt.sh / HackerTarget**：子域名 / DNS 数据。

## 说明

- 本项目中集成或受显著影响的第三方组件，其许可证与版权声明以各原始项目为准（如 mitmproxy 为 MIT License）。
- 若遗漏了应致谢的项目或署名有误，欢迎提交 Issue 补充指正。
