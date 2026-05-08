# 🦐 OpenShrimp · 本地优先的 Shell 智能体

> **Local-first · Safety-first · Memory-aware**
>
> OpenShrimp 是一个面向 macOS / 类 Unix 的本地 Shell 智能体：用中文描述任务，
> 它会规划、解释并在你的确认下安全地执行 Shell 命令；同时具备工作记忆、长期记忆、
> 持久画像与外部检索（本地 RAG + DuckDuckGo），让“常做的事”越用越顺。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](#-运行环境)
[![Local-first](https://img.shields.io/badge/Local--first-Ollama-success.svg)](#-使用-ollama)

---

## ✨ 特性

- **本地优先**：默认通过本地 [Ollama](https://ollama.com/) 规划命令；离线时自动回退到内置规则。
- **风险分级**：所有命令在执行前都会被静态分析为
  `no_shell / read_only / workspace_write / sensitive / forbidden` 五档。
- **强制确认**：`sensitive` 命令必须逐字键入确认语句；`forbidden` 直接拒绝。
- **审计日志**：每次规划/执行/反馈都写入 `.openshrimp/audit.log`（JSON Lines）。
- **四层记忆**：工作记忆（会话）、长期记忆（成功经验）、持久画像（偏好/规避）、
  外部记忆（本地笔记 RAG + DuckDuckGo）。详见 [docs/MEMORY.md](docs/MEMORY.md)。
- **可编辑历史**：Web UI 支持点 ✏️ 修改任意一条历史指令并重做，对话上下文自动截断。
- **子智能体生成**：从审计日志中识别高频习惯，一键 materialize 为 `agents/<name>/`
  目录（含 `agent.yaml` / `run.sh` / `prompt.md` / `README.md`）。
- **零依赖**：仅使用 Python 3.9+ 标准库；Web UI 也基于 `http.server`。

更多细节：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/SECURITY.md](docs/SECURITY.md)

---

## 📦 运行环境

- Python **3.9+**
- 可选：[Ollama](https://ollama.com/) 本地服务（默认 `http://127.0.0.1:11434`）
- macOS / Linux（目录选择器仅 macOS 支持，其他平台手动输入路径）

---

## 🚀 快速开始

### 命令行

```bash
# 仅规则模式（无需 Ollama）
python3 openshrimp.py --no-ollama "列出最近三天的图片文件"

# 仅生成计划，不执行
python3 openshrimp.py --no-ollama --dry-run "找出含有“智能体”一词的所有 docx 文件"

# 交互模式
python3 openshrimp.py --interactive
```

### Web UI

```bash
python3 web_server.py --port 8765
# 浏览器打开 http://127.0.0.1:8765/
```

Web UI 主要功能：
- 顶部下拉选择本地 Ollama 模型；
- 工作目录可手动输入或 📁 选择（macOS）；
- 三个开关：强制 dry-run / 允许联网命令 / 🌐 联网检索 (DuckDuckGo)；
- 🧠 记忆按钮：查看持久偏好与最近经验，并可手动新增偏好/规避；
- 任意一条历史指令右下角 ✏️ 可编辑后重做。

更多示例：[docs/USAGE.md](docs/USAGE.md)

---

## 🤖 使用 Ollama

```bash
# 指定模型
python3 openshrimp.py --model qwen2.5-coder:32b "列出最近三天的图片文件"

# 自定义 Ollama 地址
python3 openshrimp.py --ollama-host http://127.0.0.1:11434 "..."

# 通过环境变量
export OPENSHRIMP_MODEL=qwen3-coder:30b
export OPENSHRIMP_OLLAMA_HOST=http://127.0.0.1:11434
```

任何调用失败（连接超时、JSON 解析失败、模型不存在等）都会**自动回退**到内置规则解析，
保证离线可用。

---

## 🛡️ 安全模型

| 风险等级 | 含义 | 行为 |
|---|---|---|
| `no_shell` | 任务无需 Shell | 直接返回，不执行 |
| `read_only` | 只读探索（`ls`、`find`、`grep` …） | 直接执行 |
| `workspace_write` | 工作目录内写入（`mv`、`cp`、`mkdir` …） | 直接执行，建议 `--dry-run` 预览 |
| `sensitive` | 敏感（`rm`、`chmod`、网络、`sudo` …） | **必须逐字输入确认语句** |
| `forbidden` | 高危（`rm -rf /`、`curl ... \| sh` …） | **拒绝执行** |

策略可由 `.openshrimp/policy.json` 自定义，或 `--policy <path>` 指定。
完整策略字段说明见 [docs/SECURITY.md](docs/SECURITY.md)。

---

## 🧠 四层记忆

| 层 | 存储 | 作用 |
|---|---|---|
| WorkingMemory | 内存 | 当前会话的对话轮次，支持编辑历史后截断 |
| LongTermMemory | `.openshrimp/episodes.jsonl` | 用户标记“满意”后的成功经验，按 token 重合检索 |
| PersistentMemory | `.openshrimp/profile.json` | 跨会话偏好与规避事项 |
| ExternalMemory | `.openshrimp/notes/` + DuckDuckGo | 本地笔记 RAG + 联网摘要 |

四类记忆会被拼成一段【记忆上下文】注入 LLM 提示词。详见 [docs/MEMORY.md](docs/MEMORY.md)。

---

## 🔧 常用命令行参数

| 参数 | 说明 |
|---|---|
| `--cwd PATH` | 执行目录（默认 `.`） |
| `--prompt PATH` | 系统提示词文件（默认 `OpenShrimp_prompt.md`） |
| `--policy PATH` | 安全策略 JSON 文件 |
| `--dry-run` | 仅展示计划，不执行 |
| `--yes` | 跳过 sensitive 确认（**慎用**） |
| `--no-ollama` | 禁用 Ollama，仅规则解析 |
| `--show-script` | 打印完整 bash 脚本 |
| `--learn` | 分析审计日志，列出高频习惯 |
| `--make-agent NAME=KEY` | 根据习惯生成子智能体 |
| `--interactive` | 进入交互模式 |

完整参数：`python3 openshrimp.py --help`

---

## 🧪 运行测试

```bash
python3 -m pytest tests/ -v
```

所有测试均使用标准库 + `pytest`，不会发起真实网络请求。

---

## 📁 项目结构

```
.
├── openshrimp.py          # 智能体核心：规划 / 风险评估 / 执行 / 子智能体
├── web_server.py          # Web UI（http.server + 单文件 HTML/CSS/JS）
├── memory.py              # 四层记忆子系统
├── OpenShrimp_prompt.md   # 系统提示词
├── tests/                 # pytest 用例
├── agents/                # materialize 出来的子智能体
├── docs/                  # 架构 / 用法 / 记忆 / 安全 详细文档
├── .openshrimp/           # 运行时数据（审计日志、记忆、策略）
└── LICENSE                # MIT
```

运行时目录 `.openshrimp/` 内容：

```
.openshrimp/
├── audit.log         # 计划 / 执行 / 反馈记录（JSONL）
├── episodes.jsonl    # 长期记忆（成功经验）
├── profile.json      # 持久画像（偏好/规避）
├── notes/            # 外部记忆 RAG 语料（用户放置 .md / .txt）
└── policy.json       # （可选）安全策略覆盖
```

---

## 📚 进一步阅读

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 模块组成与数据流
- [docs/USAGE.md](docs/USAGE.md) — 命令行 & Web UI 详细示例
- [docs/MEMORY.md](docs/MEMORY.md) — 四层记忆设计与排查
- [docs/SECURITY.md](docs/SECURITY.md) — 风险分级与策略文件
- [OpenShrimp_prompt.md](OpenShrimp_prompt.md) — 系统提示词全文

---

## 📝 License

[MIT](LICENSE) © 2026 OpenShrimp Contributors
