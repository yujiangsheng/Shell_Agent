#!/usr/bin/env python3
"""OpenShrimp 智能体核心（``openshrimp``）。

定位：本地优先、安全优先的 macOS / 类 Unix Shell 智能体。本文件包含：

* :class:`ShellRequest`        —— 计划请求的不可变描述。
* :class:`OpenShrimp`          —— 智能体主类：``plan()`` -> ``execute()``
  两阶段。支持本地 Ollama（首选）与规则回退。
* 顶层函数 :func:`render_script` / :func:`analyze_history` /
  :func:`materialize_sub_agent`。

安全模型详见 ``docs/SECURITY.md``；记忆集成详见 ``docs/MEMORY.md``。

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


IMAGE_EXTENSIONS = [
    "*.jpg",
    "*.jpeg",
    "*.png",
    "*.gif",
    "*.webp",
    "*.bmp",
    "*.heic",
    "*.tiff",
]


FORBIDDEN_PATTERNS = [
    r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+/(\s|$)",
    r"\brm\s+-[a-z]*f[a-z]*r[a-z]*\s+/(\s|$)",
    r"\bcurl\b[^|\n]*\|\s*(sh|bash)\b",
    r"\bwget\b[^|\n]*\|\s*(sh|bash)\b",
    r"\bchmod\s+777\s+/",
    r"\bfind\s+/\s+-delete\b",
]


SENSITIVE_PATTERNS = [
    r"\brm\b",
    r"\bmv\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bsudo\b",
    r"\bkill(all)?\b",
    r"\blaunchctl\b",
    r"\bcurl\b",
    r"\bwget\b",
    r"\bssh\b",
    r"\bscp\b",
    r"\brsync\b",
    r"\bpython\s+-m\s+pip\s+install\b",
    r"\bpip\s+install\b",
    r"\bnpm\s+install\b",
]


DEFAULT_POLICY = {
    "force_dry_run": False,
    "allow_network": False,
    "max_timeout_seconds": 300,
    "extra_forbidden_patterns": [],
    "extra_sensitive_patterns": [],
    "audit_log_relative": ".openshrimp/audit.log",
}


NETWORK_PATTERNS = [
    r"\bcurl\b",
    r"\bwget\b",
    r"\bssh\b",
    r"\bscp\b",
    r"\brsync\b[^\n]*::",
    r"\bgit\s+(clone|pull|push|fetch)\b",
    r"\bpip\s+install\b",
    r"\bnpm\s+install\b",
]


@dataclass
class ShellRequest:
    """表示一次 Shell 计划的完整上下文。

    Attributes:
        tool: 固定为 ``"local_shell"``，保留作为将来扩展点。
        interpreter: ``bash | sh | ash | csh | tcsh``，默认 ``bash``。
        cwd: 执行目录，应为绝对路径。
        timeout_seconds: 最大运行秒数，会被策略 ``max_timeout_seconds`` 截断。
        risk_level: ``no_shell | read_only | workspace_write | sensitive | forbidden``。
        requires_confirmation: 是否需要交互式确认。
        purpose: 一句话任务目的（中文）。
        command: 可执行的 Shell 命令或多行脚本。
        confirmation_text: ``sensitive`` 时要求用户逐字重复的确认语句。
        explanation: 面向不懂 Shell 用户的逐步说明（3-6 句中文）。
    """

    tool: str
    interpreter: str
    cwd: str
    timeout_seconds: int
    risk_level: str
    requires_confirmation: bool
    purpose: str
    command: str
    confirmation_text: Optional[str] = None
    explanation: Optional[str] = None


def render_script(req: "ShellRequest") -> str:
    """Render a ShellRequest as a complete, runnable bash script."""
    interpreter = req.interpreter or "bash"
    if interpreter == "bash":
        shebang = "#!/usr/bin/env bash"
        strict = "set -euo pipefail"
    elif interpreter == "sh":
        shebang = "#!/bin/sh"
        strict = "set -eu"
    else:
        shebang = f"#!/usr/bin/env {interpreter}"
        strict = ""
    explanation = (req.explanation or "").strip()
    purpose = (req.purpose or "OpenShrimp generated script").strip()
    risk = req.risk_level or "read_only"
    header = [shebang, "", f"# 目的: {purpose}", f"# 风险: {risk}"]
    if explanation:
        for line in explanation.splitlines():
            header.append(f"# 说明: {line}")
    header.append(f"# 由 OpenShrimp 自动生成")
    body = []
    if strict:
        body.append(strict)
        body.append("")
    body.append(req.command.strip() or ":")
    return "\n".join(header) + "\n\n" + "\n".join(body) + "\n"


# ---------- Habit analysis & sub-agent materialization ----------

_PURPOSE_NORMALIZE_RE = re.compile(r"\d+")


def _normalize_purpose(purpose: str) -> str:
    """Collapse digits so '最近3天的图片' and '最近7天的图片' map together."""
    return _PURPOSE_NORMALIZE_RE.sub("N", (purpose or "").strip())


def analyze_history(audit_log_path: Path, min_count: int = 3) -> list[dict]:
    """Read audit.log and group plans by normalized purpose.

    Returns a list of habit summaries sorted by count desc:
    {key, sample_purpose, count, sample_command, last_ts}
    """
    if not audit_log_path.exists():
        return []
    groups: dict[str, dict] = {}
    try:
        for line in audit_log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event") != "plan":
                continue
            req = rec.get("request", {}) or {}
            purpose = req.get("purpose", "") or ""
            cmd = req.get("command", "") or ""
            if not purpose or not cmd:
                continue
            key = _normalize_purpose(purpose)
            g = groups.setdefault(key, {
                "key": key,
                "sample_purpose": purpose,
                "sample_command": cmd,
                "count": 0,
                "last_ts": rec.get("ts", ""),
                "interpreter": req.get("interpreter", "bash"),
                "risk_level": req.get("risk_level", "read_only"),
                "explanation": req.get("explanation"),
            })
            g["count"] += 1
            ts = rec.get("ts", "")
            if ts and ts > g.get("last_ts", ""):
                g["last_ts"] = ts
                g["sample_command"] = cmd
                g["sample_purpose"] = purpose
    except OSError:
        return []
    items = [g for g in groups.values() if g["count"] >= min_count]
    items.sort(key=lambda x: x["count"], reverse=True)
    return items


def _safe_agent_name(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9_-]+", "_", name)
    name = name.strip("_-") or "agent"
    return name[:48]


def materialize_sub_agent(
    name: str,
    habit: dict,
    agents_dir: Path,
    overwrite: bool = False,
) -> Path:
    """Create a sub-agent directory based on a habit summary.

    Layout matches OpenShrimp_prompt.md §14: agent.yaml + run.sh + prompt.md + README.md.
    Returns the agent directory path.
    """
    safe = _safe_agent_name(name)
    target = agents_dir / safe
    if target.exists() and not overwrite:
        raise FileExistsError(f"子智能体目录已存在: {target}")
    target.mkdir(parents=True, exist_ok=True)

    purpose = habit.get("sample_purpose", safe)
    command = habit.get("sample_command", "true")
    interpreter = habit.get("interpreter", "bash")
    risk = habit.get("risk_level", "read_only")
    explanation = habit.get("explanation") or ""
    confirm_required = risk in {"sensitive", "workspace_write"}

    agent_yaml = (
        f"name: {safe}\n"
        f"description: \"{purpose}\"\n"
        f"entrypoint: \"./run.sh\"\n"
        f"shell: \"{interpreter}\"\n"
        f"risk_level: \"{risk}\"\n"
        f"confirmation_required: {str(confirm_required).lower()}\n"
        f"privacy:\n"
        f"  local_only: true\n"
        f"  redaction: true\n"
        f"inputs: []\n"
        f"outputs: []\n"
        f"learned_from:\n"
        f"  count: {int(habit.get('count', 0))}\n"
        f"  last_ts: \"{habit.get('last_ts', '')}\"\n"
    )
    (target / "agent.yaml").write_text(agent_yaml, encoding="utf-8")

    # run.sh: bash with strict mode + dry-run flag pass-through
    run_sh_lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# 子智能体: {safe}",
        f"# 目的: {purpose}",
        f"# 风险: {risk}",
        "# 由 OpenShrimp 根据用户使用习惯自动生成",
        "",
        'DRY_RUN="${DRY_RUN:-0}"',
        'if [[ "${1:-}" == "--dry-run" ]]; then DRY_RUN=1; fi',
        "",
        'if [[ "$DRY_RUN" == "1" ]]; then',
        '  echo "[DRY-RUN] 将要执行的命令:"',
        f"  cat <<'__CMD__'\n{command}\n__CMD__",
        "  exit 0",
        "fi",
        "",
        command,
        "",
    ]
    run_path = target / "run.sh"
    run_path.write_text("\n".join(run_sh_lines), encoding="utf-8")
    run_path.chmod(0o755)

    prompt_md = (
        f"# {safe} 子智能体提示词\n\n"
        f"用途：{purpose}\n\n"
        f"风险等级：{risk}\n\n"
        f"说明：{explanation or '由 OpenShrimp 学习用户高频指令自动生成。'}\n\n"
        "调用约定：\n"
        "- 默认在用户工作目录运行。\n"
        "- 支持 --dry-run 仅展示命令。\n"
        "- 不联网、不读取敏感路径。\n"
    )
    (target / "prompt.md").write_text(prompt_md, encoding="utf-8")

    readme = (
        f"# {safe}\n\n"
        f"由 OpenShrimp 学习生成的子智能体。\n\n"
        f"- 触发示例：{purpose}\n"
        f"- 累计触发次数：{habit.get('count', 0)}\n"
        f"- 最近一次：{habit.get('last_ts', '')}\n\n"
        "## 使用\n\n"
        "```bash\n"
        f"bash agents/{safe}/run.sh           # 直接执行\n"
        f"bash agents/{safe}/run.sh --dry-run # 仅查看命令\n"
        "```\n"
    )
    (target / "README.md").write_text(readme, encoding="utf-8")

    return target


class OpenShrimp:
    """OpenShrimp 智能体主类。

    生命周期：实例化 → :py:meth:`plan` 生成 :class:`ShellRequest` →
    :py:meth:`execute` 静态分析 + 可选确认 + 执行。所有关键事件都记入
    ``self.audit_log_path`` 指向的 JSON Lines 审计日志。

    构造参数：
        prompt_path:  系统提示词 Markdown 文件路径。
        ollama_host:  Ollama 服务 URL（结尾不带 ``/``）。
        model:        默认模型名，可被 Web UI 覆盖。
        use_ollama:   ``False`` 时不进行联网调用，直接走规则解析。
        policy_path:  可选的安全策略 JSON；未传时会去
            ``<workspace>/.openshrimp/policy.json`` 寻找。
        workspace:    项目根路径，也是审计/记忆文件的宝点。
    """

    def __init__(
        self,
        prompt_path: Path,
        ollama_host: str = "http://127.0.0.1:11434",
        model: str = "qwen3-coder:30b",
        use_ollama: bool = True,
        policy_path: Optional[Path] = None,
        workspace: Optional[Path] = None,
    ) -> None:
        self.prompt_path = prompt_path
        self.ollama_host = ollama_host.rstrip("/")
        self.model = model
        self.use_ollama = use_ollama
        self.system_prompt = self._load_system_prompt(prompt_path)
        self.workspace = (workspace or Path.cwd()).resolve()
        self.policy = self._load_policy(policy_path)
        self.audit_log_path = self._resolve_audit_path()

    def _load_policy(self, policy_path: Optional[Path]) -> dict:
        merged = dict(DEFAULT_POLICY)
        candidates = []
        if policy_path is not None:
            candidates.append(policy_path)
        candidates.append(self.workspace / ".openshrimp" / "policy.json")
        for p in candidates:
            if p and p.exists():
                try:
                    user_cfg = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(user_cfg, dict):
                        merged.update(user_cfg)
                        break
                except (OSError, json.JSONDecodeError) as e:
                    print(f"[WARN] 无法解析策略文件 {p}: {e}", file=sys.stderr)
        return merged

    def _resolve_audit_path(self) -> Path:
        rel = self.policy.get("audit_log_relative") or DEFAULT_POLICY["audit_log_relative"]
        return (self.workspace / rel).resolve()

    def _audit(self, event: str, payload: dict) -> None:
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                "event": event,
                **payload,
            }
            with self.audit_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"[WARN] 审计日志写入失败: {e}", file=sys.stderr)

    def _load_system_prompt(self, prompt_path: Path) -> str:
        if not prompt_path.exists():
            return "你是 OpenShrimp，一个本地优先、安全优先的 Shell 智能体。"
        return prompt_path.read_text(encoding="utf-8")

    @staticmethod
    def _extract_json_object(text: str) -> Optional[dict]:
        """Best-effort extraction of a JSON object from LLM output.

        Handles ```json fences, <think>...</think> blocks, and surrounding prose.
        """
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
        if fence:
            cleaned = fence.group(1)
        try:
            obj = json.loads(cleaned)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(cleaned[start : end + 1])
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    def plan(self, user_query: str, cwd: Path,
             conversation: Optional[list[dict]] = None,
             memory_block: Optional[str] = None) -> ShellRequest:
        """生成计划。

        - user_query: 当前最后一条用户输入。
        - conversation: 可选的完整对话历史，每条形如
          {role: 'user'|'agent', kind: 'instruction'|'plan'|'result'|'feedback', text: str}。
        - memory_block: 可选的记忆上下文（持久偏好 + 长期经验 + 外部检索）。
        """
        if self.use_ollama:
            planned = self._plan_with_ollama(
                user_query, cwd,
                conversation=conversation,
                memory_block=memory_block,
            )
            if planned is not None:
                return self._normalize_request(planned, cwd)
        return self._fallback_plan(user_query, cwd)

    @staticmethod
    def _format_conversation(conversation: list[dict]) -> str:
        lines: list[str] = []
        for i, turn in enumerate(conversation, 1):
            role = turn.get("role", "user")
            kind = turn.get("kind", "")
            text = (turn.get("text") or "").strip()
            if not text:
                continue
            tag = "用户" if role == "user" else "智能体"
            if kind:
                tag = f"{tag}/{kind}"
            # 过长截断，避免提示爆炸
            if len(text) > 800:
                text = text[:800] + "…"
            lines.append(f"{i}. [{tag}] {text}")
        return "\n".join(lines)

    def _plan_with_ollama(self, user_query: str, cwd: Path,
                          conversation: Optional[list[dict]] = None,
                          memory_block: Optional[str] = None) -> Optional[ShellRequest]:
        history_text = ""
        if conversation:
            history_text = self._format_conversation(conversation)
        memory_text = (memory_block or "").strip()

        context_sections: list[str] = []
        if memory_text:
            context_sections.append("【记忆上下文（持久偏好 / 历史经验 / 外部检索）】\n" + memory_text)
        if history_text:
            context_sections.append(
                "【对话历史】\n"
                "请仔细阅读以下完整对话，区分两种情形：\n"
                "  (a) 这是一条全新的指令；\n"
                "  (b) 用户对前面计划/执行结果不满意，要求重做或微调；此时请基于上下文调整方案，"
                "明确指出与上次的差异，并避免重复同样的错误。\n\n" + history_text
            )

        context_block = ("\n\n".join(context_sections) + "\n\n") if context_sections else ""

        prompt = textwrap.dedent(
            f"""
            你是一个 Shell 编程专家。用户不熟悉 Shell，希望你帮他将中文指令翻译为可靠、可读、可复现的 bash 命令或脚本。
            请输出严格 JSON（不要额外文本、不要 markdown 围栏），字段如下：
            {{
              "tool": "local_shell",
              "interpreter": "bash",
              "cwd": "{cwd}",
              "timeout_seconds": 60,
              "risk_level": "no_shell|read_only|workspace_write|sensitive|forbidden",
              "requires_confirmation": true,
              "purpose": "一句话目的",
              "command": "完整可执行的 bash 命令或多行脚本，可使用管道、条件、循环、find/awk/sed/grep 等",
              "confirmation_text": "若 sensitive 则给出完整确认语句",
              "explanation": "面向不懂 Shell 的用户逐步解释该命令做什么、为什么这样写，全中文，3-6 句"
            }}

            要求：
            - 优先生成一行可执行命令；复杂任务可使用 bash heredoc 或 `bash -c '...'` 结构。
            - 变量加双引号；find 推荐用 -print0 与 while IFS= read -r -d '' 配合。
            - 使用 bash 严格模式时可写为 `set -euo pipefail; ...`。
            - 不要变更系统、不要联网、不要访问敏感路径（~/.ssh 、/etc 等）。
            - 翻译、复制、重命名等修改类任务默认 workspace_write，应先生成 dry-run 或使用 -i。
            - 删除、mv 覆盖、改权限、杀进程默认 sensitive，且 command 应先是 dry-run 列出候选。
            - macOS/BSD find 要求 `\\( ... \\)` 分组两侧有空格。
            - 如果对话历史显示用户在“重做/不满意/要修改”，请明确改进方向（例如换一种 find 语法、改用 dry-run、限制目录范围等），不要重复同一条命令。
            - 若“记忆上下文”中已存在与当前任务高度相似的成功经验，可优先复用其命令风格。

            示例输入 -> 输出 command 及 explanation:
            1) “找出当前目录下所有超过 100MB 的文件”
               command: find . -type f -size +100M -exec ls -lh {{}} \\; | awk '{{print $5, $9}}'
               explanation: 递归查找过大文件；-size +100M 筛选；ls -lh 展示人读大小；awk 只保留大小与路径。
            2) “统计每个子目录的总大小并按大小排序”
               command: du -sh */ 2>/dev/null | sort -hr
               explanation: du -sh 汇总每个子目录大小；sort -hr 按人读大小递减排序。
            3) “把所有 .jpeg 重命名为 .jpg（先 dry-run）”
               risk_level: workspace_write
               command: find . -type f -name '*.jpeg' -print0 | while IFS= read -r -d '' f; do echo mv "$f" "${{f%.jpeg}}.jpg"; done
               explanation: 这是 dry-run，只打印将要执行的 mv 命令；确认结果后去掉 echo 即可真正重命名。
            4) “查找最近 7 天修改过且同时包含 TODO 和 FIXME 的 .py 文件”
               command: find . -type f -name '*.py' -mtime -7 -print0 | xargs -0 grep -l 'TODO' | xargs grep -l 'FIXME'
               explanation: 先用 find 拿到近 7 天修改的 .py；再两次 grep -l 依次筛选同时包含两个关键词的文件。

            {context_block}用户当前请求（即对话中最后一条用户消息）：{user_query}
            """
        ).strip()

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        req = urllib.request.Request(
            url=f"{self.ollama_host}/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 — 任意失败均回退到本地规则
            return None

        content = payload.get("message", {}).get("content", "").strip()
        if not content:
            return None
        obj = self._extract_json_object(content)
        if not isinstance(obj, dict):
            return None
        return ShellRequest(
            tool=str(obj.get("tool", "local_shell")),
            interpreter=str(obj.get("interpreter", "bash")),
            cwd=str(obj.get("cwd", str(cwd))),
            timeout_seconds=int(obj.get("timeout_seconds", 60)),
            risk_level=str(obj.get("risk_level", "read_only")),
            requires_confirmation=bool(obj.get("requires_confirmation", False)),
            purpose=str(obj.get("purpose", "执行本地任务")),
            command=str(obj.get("command", "")),
            confirmation_text=(
                str(obj.get("confirmation_text"))
                if obj.get("confirmation_text") is not None
                else None
            ),
            explanation=(
                str(obj.get("explanation"))
                if obj.get("explanation") is not None
                else None
            ),
        )

    def _fallback_plan(self, user_query: str, cwd: Path) -> ShellRequest:
        q = user_query.strip()

        days_match = re.search(r"最近\s*(\d+)\s*天", q)
        days = int(days_match.group(1)) if days_match else 3
        if re.search(r"(图片|照片|image|img)", q, flags=re.IGNORECASE):
            ext_expr = " -o ".join([f"-iname '{ext}'" for ext in IMAGE_EXTENSIONS])
            cmd = (
                "find . -type f \\( " + ext_expr + " \\) "
                f"-mtime -{days} -print"
            )
            return ShellRequest(
                tool="local_shell",
                interpreter="bash",
                cwd=str(cwd),
                timeout_seconds=60,
                risk_level="read_only",
                requires_confirmation=False,
                purpose=f"列出最近{days}天的图片文件",
                command=cmd,
                explanation=(
                    f"使用 find 递归查找当前目录下的文件；"
                    f"通过多个 -iname 区分大小写医匹配常见图片后缀；"
                    f"-mtime -{days} 表示修改时间在{days}天以内。"
                ),
            )

        keyword_match = re.search(r"[“\"']([^”\"']+)[”\"']", q)
        keyword = keyword_match.group(1) if keyword_match else "智能体"
        if re.search(r"(docx|word)", q, flags=re.IGNORECASE) or (
            "docx" in q.lower()
            or ("智能体" in q and re.search(r"(找|查|搜索|包含|含有)", q))
        ):
            quoted_kw = shlex.quote(keyword)
            cmd = (
                "find . -type f -name '*.docx' -print0 | "
                "while IFS= read -r -d '' f; do "
                f"if textutil -convert txt -stdout \"$f\" 2>/dev/null | grep -q -- {quoted_kw}; "
                "then printf '%s\\n' \"$f\"; fi; "
                "done"
            )
            return ShellRequest(
                tool="local_shell",
                interpreter="bash",
                cwd=str(cwd),
                timeout_seconds=120,
                risk_level="read_only",
                requires_confirmation=False,
                purpose=f"查找包含“{keyword}”的 docx 文件",
                command=cmd,
                explanation=(
                    "先用 find 递归找出所有 .docx；"
                    "再用 macOS 自带的 textutil 将其转为纯文本；"
                    f"然后用 grep 检查是否包含关键词「{keyword}」，命中则输出路径。"
                ),
            )

        # 删除/清理类意图：识别后构造 sensitive 计划，先 dry-run 列候选，确认后再执行
        if re.search(r"(删除|清理|清除|移除|清空)", q):
            age_match = re.search(r"(\d+)\s*天", q)
            age = int(age_match.group(1)) if age_match else 30
            ext_match = re.search(r"\.([a-zA-Z0-9]+)\s*文件?", q)
            ext = ext_match.group(1).lower() if ext_match else "log"
            target_match = re.search(r"([\w./~-]+)\s*目录", q)
            target = target_match.group(1) if target_match else "."
            target_q = shlex.quote(target)
            cmd = (
                f"echo '[DRY-RUN] 候选文件如下:' && "
                f"find {target_q} -type f -name '*.{ext}' -mtime +{age} -print"
            )
            return ShellRequest(
                tool="local_shell",
                interpreter="bash",
                cwd=str(cwd),
                timeout_seconds=60,
                risk_level="sensitive",
                requires_confirmation=True,
                purpose=f"列出 {target} 下 {age} 天前的 .{ext} 文件（dry-run，不删除）",
                command=cmd,
                confirmation_text=f"确认列出：{target} 下 {age} 天前的 .{ext} 文件",
                explanation=(
                    f"这是一个安全的 dry-run：只在屏幕上列出 {target} 下修改时间超过 {age} 天的 .{ext} 文件，"
                    "不会真正删除。确认候选列表后，你可以要求下一步：举例如“把它们移到回收目录”。"
                ),
            )

        return ShellRequest(
            tool="local_shell",
            interpreter="bash",
            cwd=str(cwd),
            timeout_seconds=30,
            risk_level="read_only",
            requires_confirmation=False,
            purpose="只读探索当前目录以理解任务上下文",
            command="find . -maxdepth 3 -type f -print",
        )

    def _normalize_request(self, req: ShellRequest, default_cwd: Path) -> ShellRequest:
        interpreter = req.interpreter if req.interpreter in {"sh", "bash", "csh", "tcsh", "ash"} else "bash"
        risk = req.risk_level if req.risk_level in {
            "no_shell",
            "read_only",
            "workspace_write",
            "sensitive",
            "forbidden",
        } else "read_only"
        timeout = max(1, min(req.timeout_seconds, 300))
        cwd = str(default_cwd if not req.cwd else Path(req.cwd))
        return ShellRequest(
            tool="local_shell",
            interpreter=interpreter,
            cwd=cwd,
            timeout_seconds=timeout,
            risk_level=risk,
            requires_confirmation=req.requires_confirmation,
            purpose=req.purpose or "执行本地任务",
            command=req.command.strip(),
            confirmation_text=req.confirmation_text,
            explanation=req.explanation,
        )

    def static_risk_analyze(self, command: str) -> str:
        """对 ``command`` 进行静态安全分析，返回五档风险标签之一。

        优先级：``forbidden`` > “联网且未明示允许” → ``sensitive``
        > 其他敏感词 → ``sensitive`` > 默认 ``read_only``。空命令返回 ``no_shell``。
        """
        cmd = command.strip()
        if not cmd:
            return "no_shell"

        forbidden = list(FORBIDDEN_PATTERNS) + list(self.policy.get("extra_forbidden_patterns", []))
        for p in forbidden:
            if re.search(p, cmd, flags=re.IGNORECASE):
                return "forbidden"

        if not self.policy.get("allow_network", False):
            for p in NETWORK_PATTERNS:
                if re.search(p, cmd, flags=re.IGNORECASE):
                    return "sensitive"

        sensitive = list(SENSITIVE_PATTERNS) + list(self.policy.get("extra_sensitive_patterns", []))
        for p in sensitive:
            if re.search(p, cmd, flags=re.IGNORECASE):
                return "sensitive"

        return "read_only"

    def execute(self, req: ShellRequest, dry_run: bool = False, yes: bool = False) -> int:
        """执行一个计划。

        顺序：合并静态风险 → 打印 PLAN → 写审计 → forbidden 拦截→ ``no_shell`` 跳过
        → 策略强制 dry-run → 用户传入 dry-run → sensitive 交互确认 → 超时截断 → 子进程运行。

        Args:
            req:     待执行的计划。
            dry_run: ``True`` 时仅打印计划、不启动进程。
            yes:     ``True`` 时跳过 ``sensitive`` 交互确认（CLI ``--yes``）。

        Returns:
            进程退出码。拦截/取消返回 ≥ 1 的不同值以便于调用方区分：``2`` forbidden、
            ``3`` 确认不匹配、``124`` 超时、``1`` 一般错误。
        """
        static_risk = self.static_risk_analyze(req.command)
        final_risk = self._max_risk(req.risk_level, static_risk)

        print(f"[PLAN] 目的: {req.purpose}")
        print(f"[PLAN] 风险: {final_risk}")
        if req.explanation:
            print(f"[PLAN] 解释: {req.explanation}")
        if req.command:
            print("[PLAN] 命令:")
            print(req.command)

        self._audit("plan", {"risk": final_risk, "request": asdict(req)})

        if final_risk == "forbidden":
            print("[BLOCKED] 该命令触发 forbidden 策略，已拒绝执行。", file=sys.stderr)
            self._audit("blocked", {"reason": "forbidden", "command": req.command})
            return 2

        if final_risk == "no_shell":
            print("[INFO] 当前请求无需执行 Shell。")
            return 0

        if self.policy.get("force_dry_run", False) and not dry_run:
            print("[POLICY] 策略强制 dry-run，不执行命令。")
            self._audit("policy_dry_run", {"command": req.command})
            return 0

        if dry_run:
            print("[DRY-RUN] 已启用 dry-run，不执行命令。")
            return 0

        if final_risk == "sensitive" and not yes:
            confirmation = req.confirmation_text or "确认执行敏感操作"
            print("[CONFIRM] 敏感操作需要确认。")
            print(f"[CONFIRM] 请完整输入：{confirmation}")
            try:
                user_input = input("确认语句> ").strip()
            except EOFError:
                user_input = ""
            if user_input != confirmation:
                print("[CANCEL] 确认语句不匹配，取消执行。", file=sys.stderr)
                self._audit("cancel", {"reason": "confirmation_mismatch"})
                return 3

        if not req.command:
            print("[ERROR] 命令为空，无法执行。", file=sys.stderr)
            return 1

        max_to = int(self.policy.get("max_timeout_seconds", DEFAULT_POLICY["max_timeout_seconds"]))
        if req.timeout_seconds > max_to:
            req = ShellRequest(**{**asdict(req), "timeout_seconds": max_to})

        return self._run_shell(req)

    @staticmethod
    def _max_risk(r1: str, r2: str) -> str:
        order = {"no_shell": 0, "read_only": 1, "workspace_write": 2, "sensitive": 3, "forbidden": 4}
        return r1 if order.get(r1, 1) >= order.get(r2, 1) else r2

    def _run_shell(self, req: ShellRequest) -> int:
        cwd = Path(req.cwd)
        if not cwd.exists() or not cwd.is_dir():
            print(f"[ERROR] cwd 不存在或不是目录: {cwd}", file=sys.stderr)
            return 1

        shell = req.interpreter
        if shell == "bash":
            cmd = ["bash", "-lc", req.command]
        elif shell == "sh":
            cmd = ["sh", "-lc", req.command]
        elif shell == "ash":
            cmd = ["ash", "-lc", req.command]
        elif shell == "csh":
            cmd = ["csh", "-fc", req.command]
        elif shell == "tcsh":
            cmd = ["tcsh", "-fc", req.command]
        else:
            cmd = ["bash", "-lc", req.command]

        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=req.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            print(f"[ERROR] 命令超时（>{req.timeout_seconds}s）", file=sys.stderr)
            return 124

        if result.stdout:
            print("[STDOUT]")
            print(self._truncate_output(result.stdout))
        if result.stderr:
            print("[STDERR]", file=sys.stderr)
            print(self._truncate_output(result.stderr), file=sys.stderr)

        print(f"[DONE] 退出码: {result.returncode}")
        self._audit(
            "executed",
            {
                "command": req.command,
                "cwd": req.cwd,
                "interpreter": req.interpreter,
                "exit_code": int(result.returncode),
                "stdout_bytes": len(result.stdout or ""),
                "stderr_bytes": len(result.stderr or ""),
            },
        )
        return int(result.returncode)

    @staticmethod
    def _truncate_output(text: str, limit: int = 200000) -> str:
        if len(text) <= limit:
            return text
        head = text[: limit // 2]
        tail = text[-limit // 2 :]
        return f"{head}\n...<TRUNCATED>...\n{tail}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenShrimp: 本地 Shell 智能体")
    parser.add_argument("query", nargs="?", help="自然语言任务，例如：列出最近三天的图片文件")
    parser.add_argument("--cwd", default=".", help="执行目录，默认当前目录")
    parser.add_argument("--prompt", default="OpenShrimp_prompt.md", help="系统提示词路径")
    parser.add_argument("--model", default=os.getenv("OPENSHRIMP_MODEL", "qwen3-coder:30b"), help="Ollama 模型名")
    parser.add_argument("--ollama-host", default=os.getenv("OPENSHRIMP_OLLAMA_HOST", "http://127.0.0.1:11434"), help="Ollama 地址")
    parser.add_argument("--no-ollama", action="store_true", help="禁用 Ollama，仅用规则解析")
    parser.add_argument("--dry-run", action="store_true", help="仅展示计划与命令，不执行")
    parser.add_argument("--yes", action="store_true", help="跳过 sensitive 确认（慎用）")
    parser.add_argument("--interactive", action="store_true", help="交互模式，连续输入任务")
    parser.add_argument("--policy", default=None, help="安全策略 JSON 文件路径")
    parser.add_argument("--show-script", action="store_true", help="打印生成的完整 bash 脚本")
    parser.add_argument("--learn", action="store_true", help="分析历史习惯并列出可生成的子智能体")
    parser.add_argument("--make-agent", metavar="NAME=KEY",
                        help="根据习惯生成子智能体；使用 --learn 查看 key；格式：name=key")
    parser.add_argument("--agents-dir", default="agents", help="子智能体存放目录")
    parser.add_argument("--min-count", type=int, default=3, help="习惯计数阈值")
    return parser


def run_single(agent: OpenShrimp, query: str, cwd: Path, dry_run: bool, yes: bool, show_script: bool = False) -> int:
    req = agent.plan(query, cwd)
    if show_script:
        print("[SCRIPT] 完整脚本:")
        print(render_script(req))
    return agent.execute(req, dry_run=dry_run, yes=yes)


def run_interactive(agent: OpenShrimp, cwd: Path, dry_run: bool, yes: bool) -> int:
    print("OpenShrimp 交互模式，输入 exit 退出。")
    while True:
        try:
            q = input("OpenShrimp> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q.lower() in {"exit", "quit", "q"}:
            return 0
        code = run_single(agent, q, cwd=cwd, dry_run=dry_run, yes=yes)
        if code != 0:
            print(f"[WARN] 上一条任务返回非零退出码: {code}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cwd = Path(args.cwd).resolve()

    agent = OpenShrimp(
        prompt_path=Path(args.prompt).resolve(),
        ollama_host=args.ollama_host,
        model=args.model,
        use_ollama=not args.no_ollama,
        policy_path=Path(args.policy).resolve() if args.policy else None,
        workspace=cwd,
    )

    if args.interactive:
        return run_interactive(agent, cwd=cwd, dry_run=args.dry_run, yes=args.yes)

    if args.learn:
        habits = analyze_history(agent.audit_log_path, min_count=args.min_count)
        if not habits:
            print("[INFO] 未发现足够高频的习惯（阈值："
                  f"{args.min_count} 次）。请多使用后重试。")
            return 0
        print("[INFO] 发现以下高频习惯：")
        for h in habits:
            print(f"- key={h['key']}\n  样本目的: {h['sample_purpose']}\n  次数: {h['count']}\n  最近: {h['last_ts']}\n  命令: {h['sample_command']}")
        print("\n使用 --make-agent name=key 生成子智能体。")
        return 0

    if args.make_agent:
        if "=" not in args.make_agent:
            print("[ERROR] --make-agent 格式为 name=key", file=sys.stderr)
            return 2
        name, _, key = args.make_agent.partition("=")
        habits = analyze_history(agent.audit_log_path, min_count=1)
        habit = next((h for h in habits if h["key"] == key), None)
        if habit is None:
            print(f"[ERROR] 未找到 key={key} 的习惯。请先运行 --learn。", file=sys.stderr)
            return 2
        agents_dir = (cwd / args.agents_dir).resolve()
        try:
            target = materialize_sub_agent(name, habit, agents_dir)
        except FileExistsError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            return 1
        print(f"[OK] 已生成子智能体：{target}")
        print(f"[OK] 调用：bash {target.relative_to(cwd)}/run.sh [--dry-run]")
        return 0

    if not args.query:
        parser.print_help(sys.stderr)
        return 2

    return run_single(agent, args.query, cwd=cwd, dry_run=args.dry_run, yes=args.yes, show_script=args.show_script)


if __name__ == "__main__":
    raise SystemExit(main())
