# Roundtable 实施计划 v2

规则见 `CLAUDE.md`。每阶段结束：`ruff` + `pytest` 全绿 → git 提交（`phase N: ...`）。
v1（独立作答 → 互评 → 修订 → 主持汇总）已被本计划取代；此前尚未写任何代码。

---

## 标准流程（运行时）

1. **plan**：规划者理解题目 → 拆子任务 → 估算轮数 / token / 费用
2. **confirm_task**【确认点】：小组复述理解与分工；预估 >5 轮时同时给出 简单 / 中等 / 完整 三档
3. **discuss + execute**：表层群聊讨论；子任务按档位结对，独立完成
4. **reconcile**：里层比对结对结果 —— 一致采用；不一致交表层讨论；仍不一致【确认点】问用户
5. **compose**：表层汇总成最终作业，保留分歧点
6. **reveal**：显示各座位真实模型

每轮后记录官更新会议记录。步骤顺序在 `config/roundtable.yaml` 的 `pipeline:` 中配置。

---

## 阶段

### Phase 0 — 项目骨架
- `pyproject.toml`（streamlit、httpx、pydantic、pyyaml、python-dotenv；dev：pytest、pytest-asyncio、respx、ruff）
- 目录结构、`.gitignore`（`.env`、`*.db`）、`.env.example`、三个示例配置文件
- **测试**：core 不导入 streamlit；仓库中无疑似密钥

### Phase 1 — 配置加载与校验（`core/config`）
- `ModelSpec`（id, provider, model, vendor, price.input/output, tags, enabled, params）
- `RoundtableConfig`（seats、rounds_threshold、token_threshold、tiers、message_char_limit、context_rounds、pipeline、inner_role_rules）
- `PersonasConfig`（names、personalities）；标签词表在配置中声明，模型标签须属于词表
- **测试**：重复 ID、未知 provider、缺字段、非法标签、阈值非法（负数、0）、名字池 < 座位数

### Phase 2 — Provider 与计费（`core/providers`）
- `Provider` 基类、注册表、`Completion`（text, input/output/cached tokens, latency）
- `OpenRouterProvider`：httpx 异步、超时、指数退避重试、错误归一化、key 脱敏；prompt caching 透传
- `FakeProvider`（可编排脚本回复）、`DirectProvider` / `LocalProvider` 骨架
- `cost(usage, price)`；优先用 OpenRouter 返回的实际费用，缺失时按配置价格计算
- **测试**：respx 模拟重试/超时/401/429；错误信息无 key；费用计算（含缓存 token）

### Phase 3 — 版本化提示词（`core/prompts`）
- `prompts/{planner,dispatcher,scribe,member}/v1.md`
- 按 (role, version) 加载、变量渲染、缺变量报错，返回 `(text, version, sha256)`
- **测试**：版本查找、缺文件/缺变量、哈希稳定；member 模板只有 name/persona 字段差异

### Phase 4 — 分配逻辑（`core/allocation`）——中立性核心
- `pick_scribe`：最便宜（输入+输出加权价），并列按 seed；可手动覆盖
- `pick_inner_roles`：规划者/调度者按配置规则轮换，可同一模型兼任
- `draw_members`：从剩余模型抽 N 个，尽量覆盖不同厂商
- `assign_personas`：名字、性格随机分配，与厂商无关
- `make_pairs(subtask, members, tier)`：跨厂商优先；档位决定是否结对
- `pick_verifier`：排除本人与搭档
- `scrub_identity(text, known_names)`：遮掉自报身份
- **测试**（多 seed 循环）：里层 ∩ 表层 = ∅；记录官为最便宜；不自核/不核搭档；结对跨厂商（可行时）；性格分布与厂商独立；同 seed 可复现；模型不足时报清晰错误

### Phase 5 — SQLite 存储（`core/storage`）
- 表：sessions、seats（角色/名字/性格/model_id）、calls（角色、提示词版本+哈希、token、费用、耗时、错误）、messages（群聊发言，含发言人与轮次）、minutes（版本化）、subtasks、pair_results、checkpoints（类型、卡片内容、用户选择、附言）
- 迁移脚本 + `schema_version`
- 匿名视图：reveal 前的查询接口不含 model_id
- **测试**：读写、迁移重复执行幂等、匿名视图无模型名

### Phase 6 — 记录官（`core/scribe`）
- 输入：上一版记录 + 本轮新发言；输出结构化 minutes（结论/决定、分工进度、关键原文、未决问题与分歧）
- **关键原文由代码保证逐字**：记录官只给出 `message_id + 引文`，代码校验引文确为原文子串，不符则从原文截取替换（便宜模型容易改写公式/数字）
- "查原文"：按 message_id 从库中取完整发言
- 杂活：是否该结束、剩余轮数估计、用量统计
- **测试**：Fake 下记录完整；篡改引文被纠正；查原文返回正确发言；每版入库

### Phase 7 — 里层（`core/inner`）
- schema：`Plan`（子任务、所需标签、预估轮数/token）、`Dispatch`（分配、结对、下一步动作）、`ConfirmationCard`（现状/选项/代价/推荐）、`TierOption`
- 结构化调用器：JSON 解析 → 失败重试一次 → 降级（如使用默认分配）并记录
- 估算：轮数、token、费用；阈值判断；三档方案生成
- **测试**：schema 校验；坏 JSON 重试与降级；轮数 ≤/> 阈值、token 超阈值的触发；按标签分配无模型分支

### Phase 8 — 表层（`core/surface`）
- 群聊引擎：发言顺序（随机 + 被点名/被质疑者优先）、"跳过"、长度上限（提示约束 + 超长截断标记）
- 上下文构造：会议记录 + 最近 K 轮 + 身份遮蔽后的原文
- 子任务执行：只给相关上下文；结对双方独立调用
- 用户以"组长"身份插话进入群聊
- **测试**：结对双方上下文互不包含对方内容；上下文只含记录 + 最近 K 轮；跳过与截断；发给模型的内容无真实模型名

### Phase 9 — 编排引擎（`core/orchestrator`）
- `Step` 协议 + 注册表；内置 plan / confirm_task / discuss / execute / reconcile / compose / reveal
- 状态机：每步结束持久化；遇确认点进入 `awaiting_user`，用户回复后继续
- 失败处理：组员失败则跳过；有搭档则单独采用并标记"未双重验证"；表层 < 2 人时暂停问用户
- **测试**：Fake 全流程跑通；各确认点按规则触发；从库中恢复后状态一致、不重复调用已完成步骤；自定义步骤无需改引擎

### Phase 10 — 服务接口（`core/service.py`）
- `start`、`respond_checkpoint(continue/modify/stop, note)`、`user_message`、`resume`、`get_view(revealed)`、`reveal`、`history`、进度事件订阅
- **测试**：reveal 前任何返回值不含模型身份

### Phase 11 — Streamlit 界面（`ui/`）
- 主区：群聊（名字 + 头像色块）、组长输入框
- 侧边栏（可折叠）：计划、分工表、子任务进度、结对比对、当前会议记录、token/费用
- 确认卡片（继续 / 修改 / 停止）、揭晓按钮、历史记录（含会议记录各版本）
- 缺 key 时友好提示
- **测试**：AppTest + Fake 服务：reveal 前无模型名、确认卡片三按钮可用、插话写入群聊

### Phase 12 — 收尾
- 速率限制与并发控制、README（安装、`.env`、加模型/性格/步骤/提示词版本示例）、可选 GitHub Actions CI

---

## 默认值（采用 v2 文档第 11 节）
1. 表层 3 座；规划者+调度者可同一模型兼任；记录官独立 → 每场至少 5 个不同模型
2. 组员失败：跳过；有搭档则单独采用并标"未双重验证"；表层 < 2 人暂停问用户
3. 单步预估 > 20k token 时询问
4. 发言上限 200 字/条（子任务产出不限）
5. 上下文保留最近 2 轮原文
6. 轮数阈值 5 轮
