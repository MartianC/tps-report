# tps-report · A Codex / WorkBuddy Skill

**English** | [简体中文](README_zh.md)

At the end of every task, it measures the TPS (tokens per second) of all model requests made
during that task and appends a one-line summary to the final reply. Works with both Codex and
WorkBuddy:

```
平均 TPS：xxx token/s，最高 TPS：xxx token/s
```

*(The script prints this summary line in Chinese; the format is
`Average TPS: xxx token/s, Peak TPS: xxx token/s`.)*

![What the automatic end-of-task TPS report looks like](docs/usage.png)

## Features

- **No manual action needed.** Once the skill is installed and registered as a standing rule,
  it fires automatically at the end of every task and appends the summary line to the final
  reply. You can also ask "what was the TPS just now?" at any time.
- **Never fabricates numbers.** Any call missing token counts or duration is skipped; when no
  valid data exists at all, the degradation notice is printed verbatim — never an estimate.
- **One line only.** The summary takes exactly the last line of the final reply, plus at most
  one extra clause for anomalies (slow calls / below the trailing 10-day median).
- **Token-weighted average.** Average TPS = Σ tokens ÷ Σ seconds, so short calls cannot
  inflate the overall figure.

## Installation

Copy the sentence below to your agent and send it — everything else (downloading the files,
writing `MEMORY.md`, verifying the install) is handled automatically:

```
请帮我安装 tps-report 技能： https://github.com/MartianC/tps-report, 按要求初始化并验证安装。
```

In Codex, simply place this directory under `$CODEX_HOME/skills/tps-report/` (default
`~/.codex/skills/tps-report/`). Codex transcripts are auto-discovered from
`~/.codex/sessions/`, so no `MEMORY.md` entry is required. The manual verification command is
the same as for WorkBuddy:

```bash
python3 scripts/tps_task.py --agent codex --cwd "$(pwd)" --verbose
```

## Usage

**No day-to-day action is required** — once the skill is resident, every task is measured and
reported automatically. The commands below are only for manual verification, troubleshooting,
or on-demand queries.

`--agent` is required: pass `codex` for Codex, `workbuddy` for WorkBuddy. The script never
switches between the two environments on its own.

```bash
# Normal: print the one-line summary
python3 scripts/tps_task.py --agent codex --cwd "$(pwd)"

# Troubleshooting / follow-up questions: per-call detail (written to stderr, keeping the summary line clean)
python3 scripts/tps_task.py --agent codex --cwd "$(pwd)" --verbose

# Structured output
python3 scripts/tps_task.py --agent codex --cwd "$(pwd)" --json

# Fixed per-request overhead (seconds) derived from session records; default 0.35, tunable in other environments
python3 scripts/tps_task.py --agent codex --cwd "$(pwd)" --overhead 0
```

## Measurement Definitions

| Item | Definition |
|---|---|
| TPS (single call) | `completion_tokens ÷ duration (seconds)` |
| Average TPS | `Σ completion_tokens ÷ Σ duration` (token-weighted) |
| Peak TPS | Maximum single-call TPS |
| Unit | token/s, rounded to 1 decimal place |

## Compatibility

Verified on macOS with WorkBuddy 2.137.1 and on Codex Desktop rollout transcripts. If the data
directory layout changes, the script exits with the degradation notice instead of raising an
error. For the full definitions, edge cases, and standing-rule handling, see [SKILL.md](SKILL.md).
