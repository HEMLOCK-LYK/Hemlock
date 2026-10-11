# 登录态一键迁移设计稿

> 状态：设计稿，未实施。用户明确要求：**具体实现需要慎重考虑**，本文先把关键决策定清楚。

## 1. 要解决什么问题

测后台系统时最烦的就是登录态：浏览器里扫码/过验证码登录好了，Hemlock 的 Agent 发请求却是未认证状态——得把 Cookie 手动复制进 Repeater 或者让 Agent 重新登录（很多站登录本身就有验证码，根本自动登不了）。

目标：**浏览器里已登录的会话，一键进入 Hemlock 的会话池，Agent 立刻能用 `send_session_request` 带着这个登录态干活。**

## 2. 现有衔接点（不重复造轮子）

- `session_manager`（进程内 SessionManager / SessionState）：cookies、headers、csrf_tokens 都已支持，`send_session_request` 直接消费
- `set_session_header(session_id, name, value)`：手动注入单条 header 的入口已存在
- 浏览器自动化（browser_driver）：Agent 已能驱动本机 Chrome/Edge，「人工登录后 Agent 接管」的场景已打通

迁移本质上是：**从某个来源抓到 Cookie/Token → 写进 session_manager → Agent 可用**。缺的只是「来源」和「保鲜」。

## 3. 三个来源（按优先级实现）

### 来源 A：浏览器自动化导出（首选，覆盖现代站点）
复用 browser_driver 的 CDP 连接：`browser_get_cookies()` 拿当前页面域名的全部 Cookie（含 HttpOnly——document.cookie 拿不到的，CDP 能拿到）+ LocalStorage/SessionStorage 里的 token（可选）。

- 用户在浏览器里登录目标站 → Hemlock「登录态迁移」入口选「从浏览器导入」→ 自动打开目标域名 → 抓 Cookie → 注入 session
- 局限：只覆盖本机浏览器场景。够用，SRC 挖洞基本都是本机

### 来源 B：抓包流量提取（兜底，零配置）
从项目的 Flow 里挑：用户浏览器走 Hemlock 代理登录过，登录后的请求头里就有 Cookie/Authorization。

- 流量列表右键「将此流量的认证头迁移到会话」→ 解析该 flow 的 Cookie/Authorization/自定义 token 头 → 注入 session
- 这个来源**纯后端逻辑**，无浏览器依赖，实现成本最低，第一期就能做

### 来源 C：手动粘贴
设置/会话面板里粘贴原始 Cookie 串或 Authorization 头。作为最后兜底，顺带支持「从 Burp 复制过来的报文头」直接粘贴解析。

## 4. 关键决策：加密存储

session_manager 目前是**进程内内存态**（重启丢失）——这是现状，迁移功能第一期保持一致，**不落盘**。

理由：

- Cookie/Token 是最高敏的凭据，落盘就要面对「用什么密钥加密」的死循环：密钥存哪？机器绑定的 DPAPI（Windows-only）会让项目数据跨平台迁移失效
- 实际工作流：迁移 → 挖一晚上 → 关软件。内存态的生命周期和真实使用模式匹配
- 用户重启软件重新迁移一次的成本，远小于「凭据明文躺在 SQLite 里」的心智负担

第二期如果确实有持久化需求（比如长周期项目），再上 DPAPI + 明确告知用户的项目级加密。设计上不堵死：SessionState 序列化接口预留。

## 5. 过期检测（保鲜）

登录态失效是最影响体验的事：Agent 拿着过期 Cookie 打半天，全是 302 跳登录页，浪费 token 还误判。

- **被动检测**：`send_session_request` 的响应处理里已有会话状态更新逻辑，补一条——响应是 30x 跳登录页 / 401 / 响应体含登录页特征时，标记该 session 为 `stale`
- **主动探测**：迁移完成时立即用一次轻量请求（GET 目标首页或用户指定的 URL）验证登录态有效，结果直接告诉用户「迁移成功，当前是 xxx 账号」（从页面内容里抽用户名，能抽到就抽）
- **Agent 感知**：session 标记 stale 后，`send_session_request` 返回值带 `session_stale: true` 提示，Agent 会自己换思路（重新请求迁移或报告用户），而不是对着登录页测半天

## 6. 交互设计（最小可用）

后端先行，前端两处：

1. **流量列表右键**：「迁移认证头到 AI 会话」（来源 B，第一期）
2. **AI 安全测试页工具栏**：「登录态迁移」按钮（来源 A，第二期）——点击后选浏览器、填目标域名，完成后 toast 显示验证结果

会话面板（现有对话模式的会话状态展示）里显示已迁移的 session 和它的存活状态。

## 7. 安全边界（慎重考虑的部分，明确写死）

- 迁移来的凭据**只进 session_manager 内存**，不写 task.steps、不进上下文压缩摘要、不进任何记忆/技能/日志——上下文管理那套蒸馏逻辑对 Cookie/Authorization 头已有脱敏意识，迁移场景要逐条核对
- `list_tag_clues` / `read_flow_content` 等工具能读到的流量里天然含凭据（抓包本身如此，与迁移无关），但迁移功能自身**不新增任何凭据外泄面**
- 浏览器导入只读 Cookie，**不动浏览器会话本身**（不登出、不清域）
- 敏感操作确认：迁移动作本身提示用户确认（和 run_command 同级），防止 Agent 自作主张把浏览器登录态拉进测试

## 8. 分期

1. **第一期**：来源 B（流量右键迁移）+ 迁移后主动验证 + stale 标记 + Agent 感知。纯后端 + 一个右键菜单项，成本最低收益最大
2. **第二期**：来源 A（浏览器 CDP 导入）+ 前端迁移面板 + 用户名抽取
3. **第三期**：来源 C（粘贴）+ 会话健康度视图 + session 多账号管理（同项目多个角色会话切换）

## 9. 风险

- CDP 抓 Cookie 需要 browser_driver 暴露新接口（现在只有 navigate/evaluate/screenshot/cookies 四个动作，cookies 已有——确认它返回的是否含 HttpOnly，不是则补 CDP Network.getAllCookies 调用）
- 来源 B 的解析要处理：多 Set-Cookie 折叠、Cookie 里的引号转义、Bearer/Basic/自定义 token 头三种形态——写死解析顺序：Authorization > Cookie > 显式 token 头
- 迁移后 Agent 行为引导：系统提示词补一句「有已迁移会话时优先 send_session_request，别瞎试未认证请求」
