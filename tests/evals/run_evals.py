#!/usr/bin/env python3
"""行为回归 eval：拿真模型跑脚本化场景，对**确定性断言**打分。

设计要点（为什么是这个形状）：
- **工具层是 mock**（`mock_clawdot.py`）：不打网关、不花钱、无副作用，同一命令恒定同一
  结果 → 分数可复现，差异只来自 SKILL.md（即 GUIDE）与模型。
- **断言是正则/调用序**，不用 LLM 裁判：裁判本身会飘，回归对比就失去意义。
- 每条场景绑一条 GUIDE 规则（`why` 字段），改 GUIDE 前后跑同一批，掉分即退步。

用法：
    DEEPSEEK_API_KEY=<key> python3 tests/evals/run_evals.py [--skill <SKILL.md>]
                                                                    [--only <id,id>]
                                                                    [--json <out.json>]
无 key 时直接退出并说明（供 verify.sh 当 opt-in gate 用）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOCK = HERE / "mock_clawdot.py"
REPO = HERE.parent.parent
DEFAULT_SKILL = REPO / "dist" / "takeout-openclaw" / "SKILL.md"
API_URL = "https://api.deepseek.com/chat/completions"
MODEL = os.environ.get("EVAL_MODEL", "deepseek-chat")
MAX_LEGS = int(os.environ.get("EVAL_MAX_LEGS", "8"))

EXEC_TOOL = [{
    "type": "function",
    "function": {
        "name": "exec",
        "description": "在用户机器上执行一条 shell 命令，返回 stdout/stderr 与退出码。",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "要执行的命令"}},
            "required": ["command"],
        },
    },
}]


def chat(messages: list[dict], api_key: str) -> dict:
    body = json.dumps({"model": MODEL, "messages": messages, "tools": EXEC_TOOL,
                       "temperature": 0}, ensure_ascii=False).encode()
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read())["choices"][0]["message"]


def run_command(command: str) -> tuple[str, str, int]:
    """把模型发出的命令重定向到 mock CLI 后执行（保留原命令供断言）。

    **只放行 skill 脚本调用**：模型生成的任意 shell（`find /`、`cat ~/.env` 之类探路命令）
    不在本机执行，直接回一条合成错误——既避免 eval 在开发机上乱跑，也让"探路"这种行为
    在断言里可见（它会浪费腿数但拿不到东西）。"""
    if "clawdot.py" not in command:
        return "", "eval sandbox：本环境只允许调用 skill 脚本（scripts/clawdot.py）。", 127
    redirected = re.sub(r"\S*clawdot\.py", str(MOCK), command)
    try:
        proc = subprocess.run(redirected, shell=True, capture_output=True, text=True,
                              timeout=30, cwd=str(HERE))
    except subprocess.TimeoutExpired:
        return "", "命令超时", 124
    return proc.stdout[:4000], proc.stderr[:2000], proc.returncode


# 真实运行时（OpenClaw / Claude Code）会把 skill 安装路径注入上下文；eval 里等价补上，
# 否则模型会先花几腿去 find/ls 找脚本，测的就不是话术质量了。
SKILL_DIR = HERE.parent.parent / "skills" / "takeout"
ENV_BLOCK = f"""

<environment>
本 skill 已安装在：{SKILL_DIR}
脚本调用形式：python3 {SKILL_DIR}/scripts/clawdot.py <command> [参数]
（这台机器上只有这个脚本可用，不要执行其他命令去探路。）
</environment>
"""


def run_scenario(sc: dict, system_prompt: str, api_key: str) -> dict:
    messages = [{"role": "system", "content": system_prompt}]
    calls: list[str] = []
    finals: list[str] = []

    for user_msg in sc["turns"]:
        messages.append({"role": "user", "content": user_msg})
        for _ in range(MAX_LEGS):
            msg = chat(messages, api_key)
            messages.append(msg)
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                finals.append(msg.get("content") or "")
                break
            for tc in tool_calls:
                try:
                    cmd = json.loads(tc["function"]["arguments"]).get("command", "")
                except (json.JSONDecodeError, KeyError):
                    cmd = ""
                calls.append(cmd)
                out, err, code = run_command(cmd) if cmd else ("", "空命令", 1)
                payload = out if code == 0 else f"[exit {code}]\n{err}"
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": payload or "(empty)"})
        else:
            finals.append("(未收敛：达到 MAX_LEGS)")

    return {"calls": calls, "finals": finals, "legs": len(messages)}


def check(sc: dict, run: dict) -> list[dict]:
    a = sc.get("assert", {})
    calls_blob = "\n".join(run["calls"])
    final = run["finals"][-1] if run["finals"] else ""
    results = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append({"assert": name, "ok": ok, "detail": detail[:200]})

    for pat in a.get("must_call", []):
        add(f"must_call:{pat}", bool(re.search(pat, calls_blob)), calls_blob[:200])
    for pat in a.get("must_not_call", []):
        m = re.search(pat, calls_blob)
        add(f"must_not_call:{pat}", not m, m.group(0) if m else "")
    for pat in a.get("final_must_match", []):
        add(f"final_match:{pat}", bool(re.search(pat, final)), final[:200])
    # 多轮场景里模型可能插一轮澄清问题，把脚本化的 user turn 挤位；这类断言看"任意一轮说过"
    # 而不是"最后一轮说过"，避免用例因对话节奏抖动假失败（见 payment-link 场景）。
    all_finals = "\n".join(run["finals"])
    for pat in a.get("any_final_must_match", []):
        add(f"any_final_match:{pat}", bool(re.search(pat, all_finals)), all_finals[-200:])
    for pat in a.get("final_must_not_match", []):
        m = re.search(pat, final)
        add(f"final_not_match:{pat}", not m, m.group(0) if m else "")
    for first, second in a.get("call_order", []):
        i = next((k for k, c in enumerate(run["calls"]) if re.search(first, c)), None)
        j = next((k for k, c in enumerate(run["calls"]) if re.search(second, c)), None)
        add(f"order:{first}<{second}", i is not None and (j is None or i < j),
            f"i={i} j={j}")
    for pat, limit in a.get("max_calls", []):
        n = sum(1 for c in run["calls"] if re.search(pat, c))
        add(f"max_calls:{pat}<={limit}", n <= limit, f"实际 {n} 次")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", default=str(DEFAULT_SKILL), help="被测 SKILL.md（默认 dist openclaw 包）")
    ap.add_argument("--only", default=None, help="只跑这些 id（逗号分隔）")
    ap.add_argument("--json", dest="json_out", default=None, help="把结果写到 JSON 文件")
    args = ap.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("SKIP: 需要 DEEPSEEK_API_KEY（行为 eval 要真模型；工具层是 mock，不打网关）",
              file=sys.stderr)
        return 0

    skill_path = Path(args.skill)
    if not skill_path.is_file():
        print(f"找不到 SKILL.md: {skill_path}（先跑 python3 build.py takeout）", file=sys.stderr)
        return 2
    system_prompt = skill_path.read_text() + ENV_BLOCK

    data = json.loads((HERE / "scenarios.json").read_text())
    scenarios = data["scenarios"]
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        scenarios = [s for s in scenarios if s["id"] in want]

    total = passed = 0
    report = []
    for sc in scenarios:
        try:
            run = run_scenario(sc, system_prompt, api_key)
        except Exception as e:  # noqa: BLE001 - eval 不能因单条炸掉整批
            print(f"✗ {sc['id']}: 运行异常 {type(e).__name__}: {e}", file=sys.stderr)
            report.append({"id": sc["id"], "error": str(e)[:200], "results": []})
            continue
        results = check(sc, run)
        ok_n = sum(1 for r in results if r["ok"])
        total += len(results)
        passed += ok_n
        mark = "✅" if ok_n == len(results) else "❌"
        print(f"{mark} {sc['id']}: {ok_n}/{len(results)}  (calls={len(run['calls'])})",
              file=sys.stderr)
        for r in results:
            if not r["ok"]:
                print(f"     ✗ {r['assert']}  ← {r['detail']}", file=sys.stderr)
        report.append({"id": sc["id"], "passed": ok_n, "total": len(results),
                       "calls": run["calls"], "results": results})

    print(f"\nEVAL SCORE: {passed}/{total}", file=sys.stderr)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(
            {"skill": str(skill_path), "model": MODEL, "score": [passed, total],
             "scenarios": report}, ensure_ascii=False, indent=2))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
