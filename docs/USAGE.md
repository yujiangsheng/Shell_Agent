# 使用示例（CLI & Web UI）

## 命令行

### 基本用法

```bash
# 仅展示计划
python3 openshrimp.py --no-ollama --dry-run "列出最近三天的图片文件"

# 直接执行（read_only 任务无需确认）
python3 openshrimp.py --no-ollama "列出最近三天的图片文件"

# 显示完整 bash 脚本（含 set -euo pipefail 与注释）
python3 openshrimp.py --no-ollama --show-script "找出超过 100MB 的文件"
```

### 使用 Ollama 模型

```bash
python3 openshrimp.py --model qwen2.5-coder:32b "把 *.jpeg 重命名为 *.jpg（先 dry-run）"
```

输出示例：

```
[PLAN] 目的: 把当前目录下所有 .jpeg 重命名为 .jpg（dry-run）
[PLAN] 风险: workspace_write
[PLAN] 解释: 这是 dry-run，仅打印将要执行的 mv 命令；确认无误后去掉 echo …
[PLAN] 命令:
find . -type f -name '*.jpeg' -print0 | while IFS= read -r -d '' f; do
  echo mv "$f" "${f%.jpeg}.jpg"; done
```

### 敏感操作

```bash
python3 openshrimp.py "删除 /tmp 下 30 天前的 .log 文件"
# OpenShrimp 会先生成 dry-run 命令，并要求你输入确认语句：
# [CONFIRM] 请完整输入：确认列出：/tmp 下 30 天前的 .log 文件
```

可加 `--yes` 跳过确认（**仅自动化场景使用**）。

### 交互模式

```bash
python3 openshrimp.py --interactive
# OpenShrimp> 列出最近三天的图片文件
# OpenShrimp> 把它们都复制到 ~/Desktop/recent
# OpenShrimp> exit
```

### 习惯学习与子智能体

```bash
# 列出已经达到 3 次以上的高频习惯
python3 openshrimp.py --learn

# 选择某个 key 生成子智能体
python3 openshrimp.py --make-agent recent_images=列出最近N天的图片文件
# 之后可直接：
bash agents/recent_images/run.sh --dry-run
```

## Web UI

```bash
python3 web_server.py --port 8765
```

UI 关键流程：

1. **选择模型** —— 顶部下拉自动加载本地 Ollama 模型；自动选第一项。
2. **设置工作目录** —— 输入或 📁 选择（macOS）。
3. **开关**：
   - 强制 dry-run：所有计划都不会真正执行。
   - 允许联网命令：放行 `curl/wget/ssh/git pull` 等网络命令到 `read_only`。
   - 🌐 联网检索：调用 DuckDuckGo Instant Answer 摘要。
4. **发送指令** → 渲染【计划气泡】，包含：
   - 风险徽章、目的、解释；
   - 命令 / 完整脚本切换；
   - 复制按钮；
   - sensitive 时的逐字确认输入框；
   - ▶ 执行 / 取消。
5. **执行结果气泡** —— stdout/stderr 折叠面板 + 👍 / 👎 反馈。
6. **编辑历史** —— 任何用户气泡右下角 ✏️ → 修改文本 → 「保存并重做」会
   自动截断该气泡之后的所有内容并重新规划。
7. **🧠 记忆面板** —— 查看持久偏好 / 规避 / 最近经验，可手动新增。

## REST API（程序化调用）

```bash
# 1) 生成计划
curl -s http://127.0.0.1:8765/api/plan \
     -H 'Content-Type: application/json' \
     -d '{"query":"列出最近三天的图片文件","cwd":"'"$PWD"'","conversation":[]}'

# 2) 执行
curl -s http://127.0.0.1:8765/api/execute \
     -H 'Content-Type: application/json' \
     -d '{"plan_id":"<返回的 plan_id>"}'

# 3) 反馈
curl -s http://127.0.0.1:8765/api/feedback \
     -H 'Content-Type: application/json' \
     -d '{"plan_id":"<...>","satisfied":true,"comment":"请总是先 dry-run"}'
```
