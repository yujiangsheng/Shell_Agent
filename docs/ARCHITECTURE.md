# 架构总览

OpenShrimp 由三个 Python 模块和一份系统提示词构成，外加运行时目录 `.openshrimp/`
与可生成的子智能体目录 `agents/`。

```
┌──────────────────────────────────────────────────────────────────────┐
│                            CLI / Web UI                              │
│   openshrimp.py main()                  web_server.py main()         │
└───────────────┬──────────────────────────────────────┬───────────────┘
                │                                      │
                ▼                                      ▼
        ┌───────────────┐                     ┌────────────────────┐
        │  OpenShrimp   │  plan() / execute() │     Handler        │
        │   (agent)     │◀────────────────────│  (HTTP routes)     │
        └───────┬───────┘                     └─────────┬──────────┘
                │                                       │
   ┌────────────┼─────────┐                  ┌──────────┴──────────┐
   ▼            ▼         ▼                  ▼                     ▼
┌──────┐   ┌────────┐  ┌──────────┐    ┌───────────┐       ┌────────────┐
│Ollama│   │ Rules  │  │  Audit   │    │ ServerState│      │   Memory   │
│ HTTP │   │fallback│  │ JSONL log│    │  (in-mem)  │      │  (4 tiers) │
└──────┘   └────────┘  └──────────┘    └───────────┘       └────────────┘
```

## 模块职责

### `openshrimp.py`

- `ShellRequest` —— 数据载体：interpreter / cwd / risk_level / command / explanation 等。
- `OpenShrimp.plan()` —— 调 Ollama；失败则走 `_fallback_plan()` 规则解析。
- `OpenShrimp.static_risk_analyze()` —— 基于正则 + 策略合并的静态风险分析。
- `OpenShrimp.execute()` —— 五档风险分级 → dry-run / 确认 / 拒绝 / 执行。
- `analyze_history()` + `materialize_sub_agent()` —— 习惯学习与子智能体生成。

### `web_server.py`

- `INDEX_HTML` —— 单文件前端（HTML + CSS + 原生 JS，无打包）。
- `ServerState` —— 持有智能体、四层记忆、在线计划表。
- `Handler` —— `do_GET` / `do_POST` 路由 → `_handle_*` 方法。
- `_handle_feedback` —— 满意写长期记忆；不满意 + 备注 → 重新规划。

### `memory.py`

详见 [MEMORY.md](MEMORY.md)。

## 数据流

1. 用户输入 → `/api/plan`
2. `Handler._handle_plan` 拼装【记忆上下文】+【对话历史】，调 `agent.plan()`
3. `agent.plan()` → `_plan_with_ollama()` 或 `_fallback_plan()` → 返回 `ShellRequest`
4. `static_risk_analyze` 合并风险等级；落表 `state.plans[plan_id]`
5. 前端渲染计划气泡 → 用户点击「执行」→ `/api/execute`
6. 执行后用户给反馈 → `/api/feedback`
   - 满意：`LongTermMemory.add_episode()` + `PersistentMemory.auto_extract()`
   - 不满意：基于 `WorkingMemory` 与原 overrides 重新规划

## 审计日志格式

`<workspace>/.openshrimp/audit.log`，每行一个 JSON 对象：

```jsonc
{ "ts": "2026-05-08T01:32:38", "event": "plan", "risk": "read_only",
  "request": { "purpose": "...", "command": "...", ... } }
{ "ts": "2026-05-08T01:32:55", "event": "executed", "command": "...",
  "exit_code": 0, "stdout_bytes": 123, "stderr_bytes": 0 }
{ "ts": "2026-05-08T01:33:12", "event": "feedback", "satisfied": true,
  "comment": "请总是用 dry-run 先看一遍" }
```

`event` 取值：`plan | executed | blocked | cancel | policy_dry_run | feedback | success`。
