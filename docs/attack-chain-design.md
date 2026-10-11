# 攻击链（Attack Chain）设计稿

> 状态：设计稿，未实施。定稿后再动手写代码。

## 1. 要解决什么问题

现在的 Hemlock 挖洞是「点状」的：Agent 发了一堆请求、验证了几个漏洞、报了几个 finding，但这些动作之间的**因果链路**没有被记录——从入口资产如何一步步走到拿数据，这条路径只存在于当时的对话上下文里，任务结束就散了。

攻击链功能把这条路径固化下来：

- **汇报时能讲清楚故事**：SRC 报告里最值钱的是「如何从一个小口子走到大危害」，攻击链就是这个故事的结构化载体。
- **挖洞时能看清进度**：当前测到的节点挂在链的哪个位置、还缺什么环节（比如「已拿到 SQL 注入点但还没拿到数据」），Agent 和用户都一眼可见。
- **知识沉淀**：成功的攻击链可以作为经验记忆保存，下次遇到同类目标有参照。

## 2. 数据模型

两张表，不引入新依赖：

```python
class AttackChain(Base):
    __tablename__ = "attack_chains"
    id: str            # chn_xxx
    project_id: str    # 索引，项目隔离
    task_id: str       # 来源任务（可空：手工创建的链不挂任务）
    name: str          # 如 "OA 系统 → SQL 注入 → 拖库"
    status: str        # draft / confirmed / failed
    created_at / updated_at

class AttackNode(Base):
    __tablename__ = "attack_nodes"
    id: str            # and_xxx
    chain_id: str      # 索引
    node_type: str     # asset / vuln / credential / action / loot
    title: str         # "SQL 注入点：/api/search 的 q 参数"
    detail: str        # JSON 文本：node_type 相关的结构化字段
    source_step: int   # 关联 task.steps 的步骤序号（依据可追溯），可空
    flow_id: str       # 关联证据流量，可空
    x / y: float       # 画布坐标（前端拖拽后持久化）
```

节点类型与 detail 内容约定：

| node_type | detail 里放什么 | 来源 |
|-----------|----------------|------|
| asset | url / host / 技术栈 | recon 结果、目标 URL |
| vuln | 漏洞类型 / 端点 / 参数 / payload | report_finding、verify_vulnerability |
| credential | 用户名 / 密码 / token（**脱敏存储**，只存前 4 后 4） | 登录成功、注入出数据 |
| action | 执行了什么操作（如「读取 admin 表」「上传 shell 未成功」） | Agent 工具调用摘要 |
| loot | 拿到了什么（数据库名 / 内网 IP / 文件） | 注入结果、翻找结果 |

**边不单独建表**：节点间连线关系存 `AttackChain.edges`（JSON 文本列），`[{from: and_x, to: and_y, label: "利用"}]`。原因：链的拓扑是顺序生长的，单列表足够；独立表会让「拖一个节点重连」的写入变复杂，收益不成比例。数据量大不了再拆。

## 3. 自动构建（核心）

Agent 干活时**不要求它显式画链**（那会打断测试节奏），而是从 task.steps 的因果序列里自动抽取：

- `recon_search` / `http_probe` / 目标确认 → asset 节点
- `report_finding` / `verify_vulnerability`（通过）→ vuln 节点，flow_id 挂验证流量
- `send_session_request` 且响应含 Set-Cookie + 30x 跳转后台、`login` 语义的工具结果 → credential 节点
- `run_request_chain` 每一跳 → action 节点链
- `get_database_info` / 注入出数据的工具结果 → loot 节点

**连线规则**（按时间顺序 + 语义衔接）：

1. 同一资产下的发现顺序连：asset → vuln → action → loot，形成主干
2. 新 vuln 的验证流量中引用了前面 credential（Cookie/Authorization）→ 该 vuln 挂到 credential 下游
3. 步骤序号（source_step）保证可追溯：前端点节点能跳到对应的对话步骤和流量

自动构建的时机：**任务结束 + 每次分界线推进后**各跑一次增量抽取（幂等：按 source_step 去重）。不在每个工具调用点实时插——避免拖慢主循环，也避免中间态反复改链。

自动构建只出 **draft** 状态的链。Agent（或用户）确认后转 confirmed。

## 4. Agent 工具

三个，放核心组：

- `view_attack_chain()` — 返回当前项目链的节点/边概览（Agent 看进度、决定下一步往哪打）
- `update_attack_chain(chain_id?, action=add_node|add_edge|confirm, ...)` — Agent 主动补链（比如它明确知道「这个口子是用前面的密码登进去的」）
- `export_attack_chain(chain_id)` — 导出 Markdown 攻击路径描述，直接进漏洞报告

系统提示词补一段使用纪律：自动链已存在时不重复手画；链断了主动补关键边。

## 5. 前端

React Flow 画布（新增依赖 `@xyflow/react`，MIT 许可）：

- 项目详情页新增「攻击链」标签页；draft 链灰色、confirmed 链彩色
- 节点按 type 五种配色/图标（asset 灰、vuln 红、credential 橙、action 蓝、lost 绿）
- 拖拽连线 = add_edge（带「依据」可选标签）；拖动节点更新 x/y（防抖持久化）
- 节点点击：右侧抽屉显示 detail + 关联流量（复用现有数据包查看器）+ 跳转对话步骤
- 一个项目多条链，顶部链切换器

## 6. 报告集成

`report_finding` 生成的报告正文里，如果该 finding 是某个 confirmed 链的节点，追加「攻击路径」小节：链的 ASCII/Markdown 有向图 + 每步依据（step 摘要）。导出报告时同步带出。

## 7. 分期

1. **第一期**：表 + 自动构建 + Agent 三工具（无前端画布，链用文本视图展示）——先把数据沉淀跑起来
2. **第二期**：React Flow 画布 + 手工编辑 + 报告集成
3. **第三期**：链模板记忆（成功链存为经验，新任务自动推荐相似起点）

## 8. 风险与边界

- 自动构建的「语义衔接」规则容易过度聪明——第一期只做时间顺序主干 + credential 依赖一条规则，宁可漏连不误连，漏的让 Agent 手动补
- credential 脱敏是硬要求：入库一律只存掩码，完整值只在当轮对话上下文里
- 链数据跟着项目走，删项目级联删
