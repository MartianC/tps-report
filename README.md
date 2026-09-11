# tps-report · WorkBuddy 技能

在每个任务结束时，统计本任务内所有大模型请求的 TPS（每秒输出 token 数），
并在最终回复末尾输出一行汇总：

```
平均 TPS：59.4 token/s，最高 TPS：76.4 token/s
```

## 特性

- **双数据源，自动择优**：优先读 WorkBuddy trace 真值（`~/.workbuddy/traces/` 里的
  `generation` span，带真实耗时与 token 用量）；trace 异步落盘未就绪时，退回用
  会话记录（`~/.workbuddy/projects/`）按落盘边界推导耗时。推导法经 186 组样本
  与真值比对，中位相对误差 9%，20s 以上长调用误差 1~3%。
- **不编造**：任何一次调用缺少 token 数或耗时就跳过；一条有效数据都没有时
  原样输出降级提示，绝不估算。
- **一行输出**：汇总只占最终回复最后一行，最多附一句异常提示
  （慢调用 / 低于近 10 天中位水平）。
- **加权平均**：平均 TPS = Σtoken ÷ Σ秒，避免短调用拉高整体。

## 安装

1. 复制到用户级技能目录：

   ```bash
   cp -r tps-report ~/.workbuddy/skills/
   ```

2. 让技能常驻生效——在 `~/.workbuddy/MEMORY.md` 中写入常驻规则
   （幂等，详见 SKILL.md 的「MEMORY.md 的处理说明 → 安装初始化」）。
   这一步是关键：技能本身靠触发词加载，只有把规则写进用户级长期记忆，
   才能**每个任务自动执行、不依赖手动触发**。

3. 验证：

   ```bash
   python3 ~/.workbuddy/skills/tps-report/tps_task.py --cwd "$(pwd)" --verbose
   ```

## 用法

```bash
# 常规：输出一行汇总
python3 tps_task.py --cwd "$(pwd)"

# 排障 / 用户追问：逐次明细（走 stderr，不污染汇总行）
python3 tps_task.py --cwd "$(pwd)" --verbose

# 结构化输出
python3 tps_task.py --cwd "$(pwd)" --json

# 会话记录推导的每请求固定开销（秒），默认 0.35，其他环境可调
python3 tps_task.py --cwd "$(pwd)" --overhead 0
```

## 统计口径

| 项 | 定义 |
|---|---|
| TPS（单次） | `completion_tokens ÷ duration(秒)` |
| 平均 TPS | `Σ completion_tokens ÷ Σ duration`（按 token 加权） |
| 最高 TPS | 单次请求 TPS 的最大值 |
| 单位 | token/s，保留 1 位小数 |

## 兼容性

在 macOS + WorkBuddy 2.137.1 上实测。数据目录结构变化时脚本以降级提示结束，
不会报错。口径、边界与 MEMORY.md 处理规则详见 [SKILL.md](SKILL.md)。
