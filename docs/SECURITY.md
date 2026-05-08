# 安全模型与策略

OpenShrimp 在执行任何 Shell 命令前都会做**静态风险分析**，并允许通过策略文件
扩展规则。请务必理解下列五档语义。

## 风险等级

| 级别 | 含义 | 行为 |
|---|---|---|
| `no_shell` | 任务无需执行 Shell | 直接返回 |
| `read_only` | 只读探索（`ls`/`find`/`grep`/`cat`） | 直接执行 |
| `workspace_write` | 工作目录内写入（`mv`/`cp`/`mkdir`/`touch`） | 直接执行；建议 `--dry-run` 预览 |
| `sensitive` | 敏感（`rm`/`chmod`/`chown`/`sudo`/网络命令/`pip install`…） | **必须逐字键入确认语句** |
| `forbidden` | 高危（`rm -rf /`、`curl ... \| sh`、`chmod 777 /`） | **直接拒绝执行** |

最终风险 = `max(LLM 计划风险, 静态分析风险)`，对调用方安全友好。

## 内置黑名单

详见 [`openshrimp.py`](../openshrimp.py) 中的 `FORBIDDEN_PATTERNS`、
`SENSITIVE_PATTERNS`、`NETWORK_PATTERNS`。摘要：

- `forbidden`：`rm -rf /`、`rm -fr /`、`curl|sh`、`wget|sh`、`chmod 777 /`、`find / -delete`
- `sensitive`：`rm`、`mv`、`chmod`、`chown`、`sudo`、`kill(all)`、`launchctl`、
  `curl`、`wget`、`ssh`、`scp`、`rsync`、`pip install`、`npm install`
- `network`：`curl`、`wget`、`ssh`、`scp`、`rsync ::`、`git pull/push/fetch`、`pip install`、`npm install`

## 策略文件

放在 `<workspace>/.openshrimp/policy.json`，或用 `--policy <path>` 指定。
全部字段：

```json
{
  "force_dry_run": false,
  "allow_network": false,
  "max_timeout_seconds": 300,
  "extra_forbidden_patterns": [],
  "extra_sensitive_patterns": [],
  "audit_log_relative": ".openshrimp/audit.log"
}
```

| 字段 | 说明 |
|---|---|
| `force_dry_run` | 全局打开后所有计划都不会真正执行 |
| `allow_network` | 默认 `false`：网络命令会被加权为 `sensitive`；置 `true` 则按命令本身风险 |
| `max_timeout_seconds` | 子进程超时上限，会截断 LLM 给出的过大 timeout |
| `extra_forbidden_patterns` | 用户自定义正则（追加到黑名单） |
| `extra_sensitive_patterns` | 同上 |
| `audit_log_relative` | 审计日志位置（相对 workspace） |

## 审计日志

每次「计划 / 执行 / 反馈 / 拒绝」都会落地到 `audit.log`（JSON Lines）。
合规性 + 复盘都依赖它。常用字段：

```jsonc
{ "ts": "...", "event": "plan",     "risk": "sensitive", "request": { ... } }
{ "ts": "...", "event": "blocked",  "reason": "forbidden", "command": "..." }
{ "ts": "...", "event": "executed", "command": "...", "exit_code": 0, ... }
{ "ts": "...", "event": "feedback", "satisfied": true, "comment": "..." }
```

## 推荐实践

- **保留 `force_dry_run=true`** 进行新场景试跑；满意后再关闭。
- **不要使用 `--yes`** 在生产环境；它会绕过 sensitive 的逐字确认。
- **不要在 home 目录的根（`~`）作为 workspace**，以免审计与策略文件被误删。
- **定期清理** `.openshrimp/audit.log`，或将其加入备份。
- **关注 OWASP A03（Injection）**：所有变量都应用双引号包围；`find` 推荐
  `-print0` + `while IFS= read -r -d ''` 以避免特殊字符注入。
