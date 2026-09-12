#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tps-report：统计「当前任务」内所有大模型请求的 TPS。

═══ 统计口径 ═══
  TPS(单次) = completion_tokens / duration(秒)
      分子：该次请求「输出」的 token 数（usage.completion_tokens，含思考 token）
      分母：该次请求从发出到流式结束的耗时（秒）
  平均 TPS = Σ completion_tokens / Σ duration      —— 按 token 加权，不是算术平均
  最高 TPS = 单次请求 TPS 的最大值
  单位统一 token/s，保留 1 位小数。

═══ 数据来源（由 --agent 显式选择） ═══
  A. trace（优先，真值）
     ~/.workbuddy/traces/<pid>/trace_*.json 中 type=="generation" 的 span
     span.duration = 真实耗时(ms)；toolOutput[0].usage.completion_tokens = 输出 token
     缺点：trace 是异步落盘的，任务进行中往往还没写出来。
  B. 会话记录（实时，推导）
     ~/.workbuddy/projects/<编码路径>/<sessionId>.jsonl
     一次请求的产物在「流式结束」时批量落盘，因此：
       耗时(k) = 第 k 次请求落盘时刻 − 第 k−1 次请求的工具结果落盘时刻
       首轮用「最后一条 user 消息时刻」作为起点
     经 186 组样本与 trace 真值比对：中位相对误差 9%，20s 以上长调用误差 1~3%，
     系统偏高约 0.35s/次（请求组装 + 落盘开销），故默认扣减 OVERHEAD 秒。
  C. Codex rollout
     $CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl 中的 event_msg/token_count 事件；
     输出 token = output_tokens + reasoning_output_tokens，耗时按相邻事件时间差推导。

═══ 降级原则 ═══
  拿不到真实 token 数或真实耗时的记录一律跳过；完全没有有效数据时
  输出「无法计算」，绝不估算、不编造。
"""

import argparse
import datetime as dt
import glob
import json
import os
import statistics
import sys

HOME = os.path.expanduser("~")
TRACES = os.path.join(HOME, ".workbuddy", "traces")
PROJECTS = os.path.join(HOME, ".workbuddy", "projects")
CODEX_HOME = os.path.expanduser(os.environ.get("CODEX_HOME", "~/.codex"))
CODEX_SESSIONS = os.path.join(CODEX_HOME, "sessions")

OVERHEAD = 0.35  # 实测偏移（秒），可 --overhead 覆盖
NO_DATA = "TPS：本任务未采集到有效的 token 用量或耗时数据，无法计算（不估算、不猜测）"
GEN_TYPES = ("reasoning", "message", "function_call")


# ---------------------------------------------------------------- 工具
def enc(cwd):
    return cwd.strip("/").replace("/", "-").replace(":", "-")


def find_session(cwd=None, max_age_h=72):
    """定位当前会话 transcript。优先按 cwd 编码匹配目录，其次取最近修改的。"""
    cutoff = dt.datetime.now().timestamp() - max_age_h * 3600
    files = [f for f in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))
             if not f.endswith(".file-rollback.ndjson")]
    fresh = [f for f in files if os.path.getmtime(f) >= cutoff] or files
    if not fresh:
        return None
    if cwd:
        e = enc(cwd)
        hit = [f for f in fresh if os.path.basename(os.path.dirname(f)) == e]
        if hit:
            return max(hit, key=os.path.getmtime)
    return max(fresh, key=os.path.getmtime)


def task_start(path):
    """任务起点 = transcript 中最后一条 role=user 消息的时间戳(ms)。"""
    last, first = None, None
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            ts = o.get("timestamp")
            if not isinstance(ts, (int, float)):
                continue
            if first is None:
                first = ts
            if o.get("type") == "message" and o.get("role") == "user":
                last = ts
    except Exception:
        return None
    return last if last is not None else first


def find_codex_session(cwd=None, max_age_h=72):
    """定位 Codex rollout transcript；优先匹配 session_meta.cwd。"""
    cutoff = dt.datetime.now().timestamp() - max_age_h * 3600
    files = glob.glob(os.path.join(CODEX_SESSIONS, "**", "rollout-*.jsonl"), recursive=True)
    fresh = [f for f in files if os.path.getmtime(f) >= cutoff] or files
    if not fresh:
        return None
    if cwd:
        hits = []
        for path in fresh:
            try:
                with open(path, encoding="utf-8") as stream:
                    first = json.loads(next(stream))
                meta = first.get("payload") or {}
                if meta.get("cwd") == cwd or (meta.get("context") or {}).get("cwd") == cwd:
                    hits.append(path)
            except (OSError, StopIteration, json.JSONDecodeError):
                continue
        if hits:
            return max(hits, key=os.path.getmtime)
    return max(fresh, key=os.path.getmtime)


def codex_task_start(path):
    """返回 Codex transcript 中最后一条用户消息的时间戳（毫秒）。"""
    last = None
    try:
        for line in open(path, encoding="utf-8"):
            try:
                obj = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if obj.get("type") != "response_item":
                continue
            item = obj.get("payload") or {}
            if item.get("type") != "message" or item.get("role") != "user":
                continue
            ts = iso(obj.get("timestamp"))
            if ts:
                last = ts.timestamp() * 1000
    except OSError:
        return None
    return last


def codex_calls(path, t0_ms, now):
    """读取 Codex token_count 事件，按相邻事件时间差推导生成耗时。"""
    events, skipped = [], 0
    try:
        for line in open(path, encoding="utf-8"):
            try:
                obj = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if obj.get("type") != "event_msg":
                continue
            payload = obj.get("payload") or {}
            if payload.get("type") != "token_count":
                continue
            usage = (payload.get("info") or {}).get("last_token_usage") or {}
            try:
                tok = int(usage.get("output_tokens") or 0) + int(usage.get("reasoning_output_tokens") or 0)
                ts = iso(obj.get("timestamp"))
            except (TypeError, ValueError):
                tok, ts = 0, None
            if tok > 0 and ts and ts.timestamp() * 1000 >= t0_ms:
                events.append((ts, tok))
            elif ts and ts.timestamp() * 1000 >= t0_ms:
                skipped += 1
    except OSError:
        return [], 0
    events.sort(key=lambda x: x[0])
    out = []
    prev = dt.datetime.fromtimestamp(t0_ms / 1000).astimezone()
    for ts, tok in events:
        dur = (ts - prev).total_seconds()
        if dur > 0:
            out.append({"ts": ts, "dur": dur, "tok": tok, "model": "current", "reasoning": 0})
        else:
            skipped += 1
        prev = ts
    return out, skipped


# ------------------------------------------------- 数据源 A：trace 真值
def iso(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone()
    except Exception:
        return None


def trace_spans(t_min, t_max=None, mtime_from=None):
    out, skipped = [], 0
    files = glob.glob(os.path.join(TRACES, "*", "trace_*.json"))
    if mtime_from:
        files = [f for f in files if os.path.getmtime(f) >= mtime_from]
    for p in files:
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            skipped += 1
            continue
        for s in d.get("spans") or []:
            if s.get("type") != "generation":
                continue
            try:
                o = s.get("toolOutput")
                o = json.loads(o) if isinstance(o, str) else o
                o = o[0] if isinstance(o, list) and o else o
                tok = int(o["usage"]["completion_tokens"])
                dur = float(s["duration"])
            except Exception:
                skipped += 1
                continue
            if tok <= 0 or dur <= 0:
                skipped += 1
                continue
            st = iso(s.get("startedAt"))
            if st is None or (t_min and st < t_min) or (t_max and st > t_max):
                continue
            det = (o.get("usage") or {}).get("completion_tokens_details") or {}
            out.append({"ts": st, "dur": dur / 1000.0, "tok": tok,
                        "model": o.get("model") or "unknown",
                        "reasoning": int(det.get("reasoning_tokens") or 0)})
    out.sort(key=lambda c: c["ts"])
    return out, skipped


# --------------------------------------- 数据源 B：会话记录推导
def session_calls(path, t0_ms, overhead):
    """从 transcript 提取本任务内每次请求：落盘时刻、输出 token、上一轮工具结束时刻。"""
    calls, cur, skipped = [], [], 0
    try:
        lines = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    except Exception:
        return [], 0

    for o in lines:
        t = o.get("type")
        ts = o.get("timestamp")
        if t in GEN_TYPES:
            cur.append(o)
            m = o.get("message") or {}
            if isinstance(m, dict) and "usage" in m:
                try:
                    tok = int((m["usage"] or {}).get("output_tokens") or 0)
                except Exception:
                    tok = 0
                if tok > 0 and cur and isinstance(ts, (int, float)):
                    if ts >= t0_ms - 5000:  # 只统计本任务
                        calls.append({"write": ts, "tok": tok, "results_end": None})
                elif tok <= 0:
                    skipped += 1
                cur = []
        elif t == "function_call_result":
            if calls and isinstance(ts, (int, float)):
                calls[-1]["results_end"] = ts
        else:
            cur = []

    # 计算耗时：本次落盘 − 上一轮边界
    prev_end = t0_ms
    out = []
    for c in calls:
        start = prev_end if c["results_end"] is None else None
        if start is None:
            # 本次自身无工具结果时，边界沿用上一轮结束
            start = prev_end
        dur = (c["write"] - start) / 1000.0 - overhead
        if dur > 0.05:
            out.append({"ts": dt.datetime.fromtimestamp(c["write"] / 1000).astimezone(),
                        "dur": dur, "tok": c["tok"], "model": "current",
                        "reasoning": 0})
        else:
            skipped += 1
        prev_end = c["results_end"] or c["write"]
    return out, skipped


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=("codex", "workbuddy"), required=True,
                    help="选择会话来源：codex 或 workbuddy（必填）")
    ap.add_argument("--cwd", help="当前工作区绝对路径（用于定位会话 transcript）")
    ap.add_argument("--overhead", type=float, default=OVERHEAD,
                    help=f"会话记录推导的每请求固定开销(秒)，默认 {OVERHEAD}")
    ap.add_argument("--no-baseline", action="store_true", help="不做历史基线对比")
    ap.add_argument("--verbose", action="store_true", help="打印逐次明细（排障用）")
    ap.add_argument("--json", dest="as_json", action="store_true")
    a = ap.parse_args()

    # 由调用方明确选择 Agent，避免 Codex/WorkBuddy 同时存在时的歧义。
    source = a.agent
    session = find_codex_session(a.cwd) if source == "codex" else find_session(a.cwd)
    if not session:
        print(NO_DATA)
        print(f"原因：未定位到 {source} 会话 transcript，无法确定任务边界。", file=sys.stderr)
        return 0

    t0_ms = codex_task_start(session) if source == "codex" else task_start(session)
    if not t0_ms:
        print(NO_DATA)
        print("原因：transcript 中没有可用的时间戳。", file=sys.stderr)
        return 0
    t0 = dt.datetime.fromtimestamp(t0_ms / 1000).astimezone()
    now = dt.datetime.now().astimezone()

    # 首选真值源
    if source == "codex":
        calls, skipped = codex_calls(session, t0_ms, now)
    else:
        calls, skipped = trace_spans(t0, now, mtime_from=t0.timestamp() - 120)
        source = "trace"
        if not calls:
            calls, skipped = session_calls(session, t0_ms, a.overhead)
            source = "session"

    if a.verbose:
        print(f"[debug] 会话={os.path.basename(session)} 起点={t0:%H:%M:%S} "
              f"数据源={source} 命中={len(calls)} 跳过={skipped}", file=sys.stderr)
        for c in calls:
            print(f'  {c["ts"]:%H:%M:%S} {c["model"][:26]:<26} {c["dur"]:>7.2f}s '
                  f'{c["tok"]:>7,}tok {c["tok"] / c["dur"]:>7.1f}', file=sys.stderr)

    if not calls:
        print(NO_DATA)
        print(f"原因：{t0:%H:%M:%S} 之后没有 carry usage/duration 的模型调用记录。",
              file=sys.stderr)
        return 0

    tot_tok = sum(c["tok"] for c in calls)
    tot_sec = sum(c["dur"] for c in calls)
    avg = tot_tok / tot_sec
    peak = max(calls, key=lambda c: c["tok"] / c["dur"])
    top = peak["tok"] / peak["dur"]
    models = sorted({c["model"] for c in calls})

    note = ""
    if not a.no_baseline:
        hist, _ = trace_spans(t0 - dt.timedelta(days=10), t0) if source != "codex" else ([], 0)
        if len(hist) >= 10:
            med = statistics.median(c["tok"] / c["dur"] for c in hist)
            if med > 0 and avg < med * 0.6:
                note = f"；低于近 10 天中位水平（{med:.1f} token/s），性能可能下降"
    if not note:
        slow = sum(1 for c in calls if c["dur"] > 60)
        if slow:
            note = f"；含 {slow} 次耗时超 60s 的慢调用"

    line = f"平均 TPS：{avg:.1f} token/s，最高 TPS：{top:.1f} token/s"
    if a.as_json:
        print(json.dumps({
            "avg_tps": round(avg, 1), "max_tps": round(top, 1), "calls": len(calls),
            "total_tokens": tot_tok, "total_seconds": round(tot_sec, 2),
            "source": source, "models": models, "skipped": skipped,
            "peak": {"model": peak["model"], "duration_s": round(peak["dur"], 2),
                     "tokens": peak["tok"], "tps": round(top, 1)},
            "note": note.lstrip("；"), "line": line,
        }, ensure_ascii=False))
    else:
        print(line + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
