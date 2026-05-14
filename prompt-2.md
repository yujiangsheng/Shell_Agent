# Intent-to-Shell Agent 实现提示词

你要实现一个本地可运行的原型系统：**把用户的自然语言操作意图转化为安全、可验证、可恢复的 shell 执行流程**。  
系统中的“意图理解与 shell 生成”部分默认由本地 LLM 完成，例如 `qwen3:coder`，但整个系统不能只是“把一句话翻译成一段 bash”，而必须具备**规划、约束、安全检查、执行、验证、修正**能力。

目标不是做一个聊天界面，而是做一个 **shell-based operational agent** 的最小可用实现（MVP）。

---

## 1. 产品目标

实现一个本地 agent，支持以下闭环：

1. 接收用户输入的自然语言任务
2. 采集必要的本机上下文
3. 将任务转成结构化任务规格
4. 生成分步骤执行计划
5. 为每个步骤生成 shell 命令或 shell 脚本片段
6. 在执行前做安全审查
7. 按步骤执行
8. 对每一步执行结果进行验证
9. 如果失败，给出错误原因，并支持有限的自动修正或中止
10. 输出完整的执行日志、计划、命令、结果和验证结论

请把 shell 视为**执行后端**，而不是唯一的推理表示。系统内部应优先使用结构化数据表达任务与计划，再编译成 shell。

---

## 2. 设计原则

请严格遵守以下原则：

- 先结构化理解任务，再生成 shell
- 默认保守执行，优先只读探测，再进行写操作
- 所有高风险操作都必须被识别
- 每个执行步骤都要有前置条件、命令、后置验证
- 尽量幂等，重复执行不应造成明显破坏
- 尽量可恢复，涉及修改时优先备份或 dry-run
- 结果必须可审计，保留结构化日志
- 对实现细节保持模块化，方便未来替换 LLM、执行器和安全策略

---

## 3. 建议技术栈

如无更优理由，建议采用以下技术栈：

- 主语言：Python 3.12+
- CLI 框架：`typer` 或 `argparse`
- 数据模型：`pydantic`
- shell 执行：`subprocess`
- 结构化日志：JSON Lines
- 配置文件：YAML 或 TOML
- LLM 调用：本地模型封装层，适配 `qwen3:coder`

如需前端，不要优先做 Web UI。先把 CLI 原型做好。

---

## 4. 核心功能范围

MVP 至少支持以下任务类型：

- 文件与目录整理
- 批量重命名
- 搜索与汇总日志
- 压缩与归档
- 开发环境巡检
- Git 仓库基础检查
- 端口/进程排查

MVP 可以暂不支持：

- GUI 自动化
- 浏览器复杂交互
- 跨机器分布式执行
- 提权执行
- 全自动高风险系统变更

---

## 5. 系统架构

请按模块实现，至少包含：

### 5.1 Intent Parser

输入：用户自然语言请求  
输出：结构化任务规格 `TaskSpec`

需要提取：

- 用户目标
- 目标对象
- 操作范围
- 约束条件
- 风险偏好
- 成功标准
- 是否允许写操作

### 5.2 Context Collector

负责采集本机上下文，例如：

- 当前操作系统与 shell
- 当前工作目录
- 可用命令
- 目标路径是否存在
- 当前用户权限
- 与任务相关的文件、进程、端口、仓库状态

上下文采集必须最小化，只收集和当前任务强相关的信息。

### 5.3 Planner

输入：`TaskSpec + Context`  
输出：`ExecutionPlan`

计划必须是分步骤的，每一步都应有：

- `id`
- `goal`
- `action_type`
- `inputs`
- `risk_level`
- `expected_effect`
- `needs_confirmation`

### 5.4 Shell Generator

输入：单个步骤  
输出：结构化 shell 动作 `ShellAction`

每个 `ShellAction` 至少包含：

- `command`
- `cwd`
- `env`
- `timeout_sec`
- `dry_run_command`（如果适用）
- `parser_hint`
- `expected_exit_codes`

要求：

- 优先使用标准 Unix 工具
- 正确处理空格和特殊字符
- 避免脆弱的字符串拼接
- 能明确区分 stdout、stderr、exit code

### 5.5 Safety Guard

执行前检查 shell 动作是否危险。

至少识别以下风险：

- 删除性操作
- 覆盖性写入
- 递归修改权限
- 大范围通配符
- 系统目录写入
- 涉及敏感路径的读取
- 网络下载后直接执行
- 管道掩盖错误

风险等级建议：

- `low`
- `medium`
- `high`
- `blocked`

策略建议：

- `low`: 可自动执行
- `medium`: 默认 dry-run 后执行
- `high`: 必须显式确认
- `blocked`: 直接拒绝

### 5.6 Executor

按步骤执行，要求：

- 支持 dry-run
- 支持逐步执行
- 支持执行超时
- 保存完整结果
- 若某一步失败，停止后续步骤，进入诊断流程

### 5.7 Verifier

每个步骤执行后必须验证。

例如：

- 文件移动：验证源路径减少、目标路径增加
- 创建目录：验证目录存在
- 日志搜索：验证命令输出非空或符合预期格式
- Git 检查：验证仓库状态已正确获取

验证失败要明确区分：

- 命令执行失败
- 命令执行成功但效果不符合预期

### 5.8 Repair Loop

失败后做有限修正，不允许无限重试。

建议策略：

- 基于错误输出生成诊断结论
- 允许最多 1 到 2 次修正重试
- 修正必须记录原因
- 如果仍失败，输出失败报告并停止

### 5.9 Audit Logger

持久化记录以下信息：

- 原始用户请求
- 结构化任务规格
- 上下文摘要
- 执行计划
- 每一步的 shell 命令
- 安全评估结论
- 执行输出
- 验证结果
- 最终总结

日志应可机器读取，建议 JSONL。

---

## 6. 数据结构要求

请先定义清晰的数据模型，建议至少包含：

```python
class TaskSpec(BaseModel):
    user_request: str
    goal: str
    scope: dict
    constraints: list[str]
    success_criteria: list[str]
    allow_writes: bool = False


class PlanStep(BaseModel):
    id: str
    goal: str
    action_type: str
    inputs: dict
    risk_level: str
    needs_confirmation: bool = False


class ShellAction(BaseModel):
    command: str
    cwd: str | None = None
    env: dict[str, str] = {}
    timeout_sec: int = 30
    dry_run_command: str | None = None
    expected_exit_codes: list[int] = [0]
    parser_hint: str | None = None


class ExecutionResult(BaseModel):
    step_id: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    started_at: str
    finished_at: str
    success: bool


class VerificationResult(BaseModel):
    step_id: str
    passed: bool
    summary: str
    details: dict = {}
```

可以调整字段，但不要丢失这些核心语义。

---

## 7. LLM 集成要求

本地 LLM 主要承担三件事：

1. 从自然语言生成 `TaskSpec`
2. 从 `TaskSpec + Context` 生成 `ExecutionPlan`
3. 从单个 `PlanStep` 生成 `ShellAction`

要求：

- LLM 输出必须是严格结构化 JSON
- 增加解析失败重试机制
- 增加 schema 校验
- 禁止直接信任模型输出的危险命令
- 模型生成结果必须经过安全模块审查

请把 LLM 封装成单独模块，未来可替换为别的本地模型。

---

## 8. 推荐交互方式

优先实现 CLI，例如：

```bash
python main.py run "把 ~/Downloads 里的 pdf 按月份归档到 ~/Archive 下，不要删除非 pdf 文件"
```

建议支持这些命令：

```bash
python main.py run "<task>"
python main.py plan "<task>"
python main.py dry-run "<task>"
python main.py verify <run_id>
python main.py replay <run_id>
```

行为说明：

- `plan`: 只生成任务规格、计划和命令，不执行
- `dry-run`: 执行模拟命令或只读探测
- `run`: 执行完整流程
- `verify`: 对某次运行重新做验证
- `replay`: 回放历史计划和命令

---

## 9. 安全与约束要求

这是本项目最关键的部分之一。

必须实现：

- 危险命令检测
- 路径白名单或当前目录限制机制
- 写操作显式开关
- dry-run 优先
- 高风险操作确认机制
- 命令超时
- 错误输出记录

建议额外实现：

- 对文件修改先自动备份
- 对批量操作先显示影响样本
- 对 shell 命令做 AST 或规则级检查

明确禁止默认自动执行以下行为：

- `rm -rf` 形式的广泛删除
- 修改系统关键目录
- 静默覆盖大量文件
- 下载远程脚本后直接执行
- 未经确认的递归权限修改

---

## 10. 示例任务

请至少用以下任务验证系统：

### 示例 1：文件整理

输入：

`把 ~/Downloads 里的 pdf 按月份归档到 ~/Archive 下，不要删别的文件`

预期：

- 探测 Downloads 中的 pdf
- 生成目标目录
- 移动 pdf
- 验证移动结果

### 示例 2：日志汇总

输入：

`统计 logs 目录下所有日志里最近一天出现 ERROR 的次数，并按文件输出`

预期：

- 收集日志文件
- 搜索最近一天 ERROR
- 汇总每个文件计数
- 输出结构化结果

### 示例 3：端口排查

输入：

`看看 8000 端口有没有被占用，如果有，告诉我是什么进程，不要杀掉它`

预期：

- 只读查询端口状态
- 返回进程信息
- 不做写操作

### 示例 4：Git 巡检

输入：

`检查当前仓库是否干净，当前分支是什么，有没有未推送提交`

预期：

- 获取 git status
- 获取分支名
- 获取 ahead/behind 信息
- 输出结论

---

## 11. 项目结构建议

建议使用如下目录结构：

```text
intent_shell_agent/
  main.py
  config/
    default.yaml
  agent/
    models.py
    intent_parser.py
    context_collector.py
    planner.py
    shell_generator.py
    safety_guard.py
    executor.py
    verifier.py
    repair_loop.py
    logger.py
    llm_client.py
  prompts/
    intent_parser.txt
    planner.txt
    shell_generator.txt
  tests/
    test_intent_parser.py
    test_planner.py
    test_safety_guard.py
    test_executor.py
    test_end_to_end.py
  runs/
```

你可以调整结构，但必须保持边界清楚。

---

## 12. 实现要求

请直接实现代码，不要只输出架构说明。

代码要求：

- 先实现最小闭环
- 再逐步补强安全与验证
- 模块边界清楚
- 类型定义明确
- 保留扩展点
- 关键逻辑有测试
- 尽量写成真实可运行项目，而不是示意代码

---

## 13. 测试要求

至少实现以下测试：

- `TaskSpec` 解析结果校验
- `ExecutionPlan` 结构校验
- 危险命令拦截测试
- 普通只读命令执行测试
- 文件整理任务的端到端测试
- 命令失败后的修正或停止逻辑测试

测试应优先使用临时目录，不要污染真实用户环境。

---

## 14. 验收标准

完成后，系统至少应满足：

1. 能处理自然语言任务并生成结构化计划
2. 能为计划步骤生成可执行 shell 命令
3. 能在执行前识别明显危险命令
4. 能执行低风险任务并输出日志
5. 能在执行后验证结果
6. 能在失败时给出清晰诊断
7. 能通过至少一个文件整理类任务的端到端测试

---

## 15. 输出要求

请产出：

- 完整项目代码
- 运行说明
- 配置说明
- 测试代码
- 至少一个示例运行结果

在实现过程中，优先保证：

- 可靠性
- 清晰性
- 安全性
- 可维护性

不要把重点放在 UI、美化或花哨交互上。

---

## 16. 补充建议

如果你认为一步到位实现全部功能过大，请按以下顺序递进：

1. 只读任务闭环
2. 低风险文件操作闭环
3. 安全审查增强
4. 自动验证增强
5. 有限失败修正
6. 历史回放与审计

优先把骨架搭稳，再往上叠能力。

