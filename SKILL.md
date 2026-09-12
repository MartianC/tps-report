---
name: tps-report
description: 统计当前任务内大模型请求的 TPS（每秒输出 token 数），并在任务最终回复末尾输出一行「平均 TPS：X，最高 TPS：Y」。触发时机：任何任务准备输出最终回复之前自动执行一次，无需用户要求。触发词：TPS、tokens per second、每秒 token、输出速度、模型速度、统计 TPS、汇报 TPS、刚才速度多少、性能下降。兼容 WorkBuddy 与 Codex，会话数据缺失时明确输出无法计算，不估算、不编造。
---

# tps-report：任务结束的 TPS 汇报

## 铁律

1. **只报真实数据**。任何一次调用缺少 `token 用量` 或 `耗时` 就跳过；
   一条有效数据都没有时，输出降级提示，**绝不估算、绝不编造数值**。
2. **一行，放在最终回复的最后一行**，不刷屏、不加解释段落。
3. 统计范围严格限定为**当前任务**（本次用户提问之后）的所有大模型请求，
   不掺入历史任务的数据。

## 统计口径

| 项 | 定义 |
|---|---|
| TPS（单次） | `completion_tokens ÷ duration(秒)`，即每秒输出的 token 数 |
| 平均 TPS | `Σ completion_tokens ÷ Σ duration` —— 按 token 加权，**不是算术平均** |
| 最高 TPS | 单次请求 TPS 的最大值 |
| 单位 | 统一 `token/s`，保留 **1 位小数** |
| 分子 | 该次请求的输出 token（`usage.completion_tokens`，含思考 token） |
| 分母 | 该次请求从发出到流式结束的耗时（秒） |

## 触发条件

满足任一条件即执行：

1. **常驻自动触发（主要方式）**：任何任务，在写出最终回复之前执行一次统计，
   把结果附在最终回复末尾。包括纯问答、写代码、改文件、检索、生成文档——
   只要本次任务触发过模型请求就报。不需要用户提出要求，也不要等用户问。
2. **用户主动询问**：用户提到「TPS」「每秒 token」「输出速度」「刚才的速度」
   「统计/汇报 TPS」「是不是变慢了」等，立即执行并可以给出明细（`--verbose`）。
3. **不触发的情况**：用户明确说「这次不用报」时跳过一次；纯闲聊（无模型请求、
   无工具调用）若脚本返回降级提示，按降级模板原样输出即可，不必展开解释。

## 数据来源与采集方式（由 Agent 指定环境）

执行脚本时必须传入 `--agent codex` 或 `--agent workbuddy`。该参数只决定读取哪种
产品的会话目录；选择 `workbuddy` 后，脚本仍会在 WorkBuddy trace 与会话记录之间自动择优。

- **A. trace 真值（优先）**
  `~/.workbuddy/traces/<pid>/trace_*.json` 中 `type == "generation"` 的 span：
  `span.duration` = 真实耗时(ms)；`toolOutput[0].usage.completion_tokens` = 输出 token。
  缺点：trace 异步落盘，任务进行中常常还没写出来。

- **B. 会话记录推导（实时兜底）**
  `~/.workbuddy/projects/<编码路径>/<sessionId>.jsonl`。
  一次请求的产物在**流式结束时批量落盘**，故
  `耗时(k) = 第 k 次落盘时刻 − 第 k−1 次工具结果落盘时刻`，首轮以最后一条 user 消息为起点。
  经 186 组样本与 trace 真值比对：中位相对误差 9%，20s 以上长调用误差 1~3%，
  系统偏高约 0.35s/次（请求组装 + 落盘开销），脚本默认扣减该偏移。
  该偏移在本机（macOS + WorkBuddy 2.137.1）标定；其他环境如有偏差，
  用 `--overhead` 调整或设为 0。

- **C. Codex rollout（Codex 适配）**
  `CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`（默认 `~/.codex/sessions/`）中的
  `event_msg`/`token_count` 事件。输出 token 使用 `output_tokens + reasoning_output_tokens`；
  单次耗时用本任务起点或上一条 token_count 事件的时间差推导。字段缺失时按降级规则跳过。

## 执行步骤

1. 在输出最终回复前，执行：

```bash
PY="$HOME/.workbuddy/binaries/python/envs/default/bin/python"
[ -x "$PY" ] || PY=python3
"$PY" "$HOME/.workbuddy/skills/tps-report/scripts/tps_task.py" --agent workbuddy --cwd "$(pwd)"
```

若当前 Agent 是 Codex，则使用其 Python 环境并传入 `--agent codex`：

```bash
python3 "$CODEX_HOME/skills/tps-report/scripts/tps_task.py" --agent codex --cwd "$(pwd)"
```

2. 取脚本 stdout 的**唯一一行**作为汇总行，原样附到最终回复最后一行。
3. 若脚本输出的是降级提示，原样附出，不要改写成"约 XX"之类的猜测值。
4. 需要排障或用户追问时加 `--verbose`（明细走 stderr，不污染汇总行）
   或 `--json`（结构化输出）。

## 输出模板

正常（附在最终回复最后一行，独立成行）：

```
平均 TPS：59.4 token/s，最高 TPS：76.4 token/s
```

异常提示（可选，拼在同一行末尾，最多一句）：

```
平均 TPS：31.2 token/s，最高 TPS：48.0 token/s；含 1 次耗时超 60s 的慢调用
平均 TPS：25.7 token/s，最高 TPS：40.1 token/s；低于近 10 天中位水平（52.3 token/s），性能可能下降
```

降级（数据缺失，原样输出）：

```
TPS：本任务未采集到有效的 token 用量或耗时数据，无法计算
```

## 常驻规则的处理说明

WorkBuddy 依赖 `~/.workbuddy/MEMORY.md`（用户级长期记忆）中的一条常驻规则；
Codex 由技能自动发现与用户级指令共同保证常驻执行。处理规则如下：

**何时读取**

- 通常**无需主动读取**：WorkBuddy 会在每次会话启动时把 MEMORY.md 自动注入上下文。
- 仅当上下文中看不到「每次任务必做：结尾汇报 TPS」小节、且怀疑规则缺失时
  （新装用户、记忆被清理、换了机器），才 Read 一次该文件确认；
  确认缺失后按下方「安装初始化」补写，不要凭空假设规则存在。

**何时更新**

- **只在安装/首次配置时**追加一次规则块（见下），且必须幂等：
  先检查文件中是否已有「每次任务必做：结尾汇报 TPS」小节，已存在则跳过，绝不重复追加。
- 日常运行本技能**不得**改写 MEMORY.md——TPS 统计结果是一次性输出，
  不属于需要长期记忆的内容，写进去只会污染用户记忆。
- 更新前必须先 Read 该文件，再用 Edit 做精准插入；**禁止整文件覆盖**，
  用户记忆中可能有与本技能无关的规则和偏好。
- 卸载/停用本技能时，应同步移除该规则块。

**注意事项**

- MEMORY.md 是跨项目注入的用户级记忆，写入务必克制，规则块保持精简
  （单次会话对该文件的写入量有上限，约 4KB）。
- 规则块内容与 SKILL.md 不一致时，以 SKILL.md 为准，并顺手修正规则块。

**WorkBuddy 安装初始化（幂等追加以下内容到 `~/.workbuddy/MEMORY.md` 顶部标题之后）**

````markdown
## 每次任务必做：结尾汇报 TPS（强制，跨项目通用）

**每个任务在输出最终回复之前**，必须跑一次 TPS 统计，并把结果作为最终回复的
**最后一行**独立输出。不需要用户要求，也不要等用户问。

```bash
PY="$HOME/.workbuddy/binaries/python/envs/default/bin/python"
[ -x "$PY" ] || PY=python3
"$PY" "$HOME/.workbuddy/skills/tps-report/scripts/tps_task.py" --agent workbuddy --cwd "$(pwd)"
```

- 取 stdout 的唯一一行原样附在末尾，格式：`平均 TPS：X.X token/s，最高 TPS：Y.Y token/s`
- 若输出降级提示（"未采集到…无法计算"），原样附出，**不要改写成估算值、不要编造**
- 详细口径、数据来源、降级规则见技能 `~/.workbuddy/skills/tps-report/SKILL.md`
````

## 为什么这样算（避免被"改坏"）

- 分母必须是**单次请求的耗时**，不能是"整个任务挂钟时间"——
  后者把工具执行、用户等待都算进去，TPS 会被严重低估。
- 平均必须**加权**（总 token ÷ 总秒数）。算术平均会让一次几十 token 的短调用
  拉高整体，失去意义。
- 不要为了"有数字"而用字符数 ÷ 4 去估 token，也不要拿别的任务的数据顶替。
