# 四层记忆设计

OpenShrimp 的记忆全部由 [`memory.py`](../memory.py) 实现，**仅依赖标准库**。

## 1. WorkingMemory · 工作记忆

- **存储**：进程内 `dict[str, list[Turn]]`，按 `session_id` 隔离。
- **接口**：`get / replace / append / clear`。
- **关键点**：`replace(sid, turns)` 用于支持「编辑历史 + 重做」——前端会
  把可见对话快照整体回传，后端整段覆盖即可，无需做差异合并。
- **Turn 字段**：`role`（user/agent）、`kind`（instruction/plan/result/feedback/note）、
  `text`、`ts`、`plan_id`。

## 2. LongTermMemory · 长期情景记忆

- **存储**：`<workspace>/.openshrimp/episodes.jsonl`，每行一条成功经验。
- **何时写入**：用户在 Web UI 点 👍 满意时，由 `_handle_feedback` 写入。
- **检索**：`search(query, top_k=3)` 基于 token 重合度（`overlap_score`）。
- **每条字段**：`ts / purpose / command / risk_level / cwd / comment / tags`。

## 3. PersistentMemory · 持久画像

- **存储**：`<workspace>/.openshrimp/profile.json`。
- **结构**：

  ```json
  {
    "language": "zh-CN",
    "shell": "bash",
    "preferences": ["默认请用 dry-run", "命令注释用中文"],
    "avoid": ["不要联网"]
  }
  ```

- **自动抽取**：`PersistentMemory.auto_extract(comment)` 用关键词正则
  （`请总是 / 偏好 / 喜欢 / 不要 / 禁止 / 避免` …）从 👍 反馈备注里抽取。
- **手动编辑**：Web UI 的「🧠 记忆」面板支持新增偏好 / 规避；
  也可直接编辑 JSON 文件。

## 4. ExternalMemory · 外部知识

### 本地笔记 RAG

- 在 `<workspace>/.openshrimp/notes/` 放置任何 `.md / .txt / .rst`
  文件，会被 `search_notes(query)` 按 token 重合度检索；
- 命中时返回最佳片段的 ±N 行（默认上 4 / 下 6 行，单段 ≤ 800 字符）；
- **不做向量化、无外部依赖**——适合"几十到几百篇"的小型语料。

### DuckDuckGo Instant Answer

- 仅当 Web UI 顶部 **🌐 联网检索** 勾选时才发起请求；
- 端点：`https://api.duckduckgo.com/?q=<query>&format=json&no_html=1`；
- 解析字段：`AbstractText / AbstractURL / Answer / Definition / RelatedTopics`；
- 6 秒超时；任何异常都安静返回 `None`，不影响主流程。

## 拼装为 LLM 上下文

`render_memory_block(persistent, episodes, notes, web)` 会按下面的固定结构拼成
一段中文文本，注入到提示词的【记忆上下文】段：

```
【用户画像 · 持久偏好】
- 偏好：默认请用 dry-run
- 规避：不要联网

【长期记忆 · 过往成功经验】
- 目的：列出最近 N 天的图片文件
  命令：find . -type f \( -iname '*.jpg' -o -iname '*.png' ... \) -mtime -3
  备注：请总是用 dry-run

【外部记忆 · 本地笔记】
- 来源：.openshrimp/notes/macos-find-tips.md
  | macOS 的 BSD find 要求 \( ... \) 两侧有空格

【外部记忆 · 联网搜索】
- 摘要：…
```

空段会被自动省略；整体不超过模型上下文容忍度。

## 排查

| 现象 | 排查 |
|---|---|
| 偏好没生效 | 编辑 `profile.json` 后刷新页面；或查看 `audit.log` 中 `feedback` 事件是否含关键词。 |
| RAG 命中不到 | 笔记文件后缀必须为 `.md/.txt/.rst`；检查 `<workspace>/.openshrimp/notes/`。 |
| 联网检索没结果 | DuckDuckGo Instant Answer 仅对维基类问题有响应；非常见关键词常返回空。 |
