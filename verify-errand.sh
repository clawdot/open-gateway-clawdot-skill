#!/usr/bin/env bash
# Verification gate for the errand (跑腿) skill —— MCP transport + 能力分格.
# 镜像 verify.sh（takeout）的门禁到 errand。Green 是必要非充分（真下单/短信绑定人核）。
# 跑：bash verify-errand.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$ROOT/skills/errand/scripts/errand.py"
FAILED=0

step() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '  ✅ %s\n' "$1"; }
bad()  { printf '  ❌ %s\n' "$1"; FAILED=1; }

# EG1a — compiles
step "EG1a py_compile"
if python3 -m py_compile "$SCRIPT"; then ok "compiles"; else bad "py_compile failed"; fi

# EG1b — argparse smoke（子命令制）
step "EG1b argparse smoke"
python3 "$SCRIPT" --help >/dev/null 2>&1 && ok "--help exits 0" || bad "--help failed"
if python3 "$SCRIPT" bogus_command >/dev/null 2>&1; then
  bad "invalid subcommand was accepted"
else
  ok "invalid subcommand rejected"
fi

# EG1/EG3/EG4 — contract / 能力分格 / error playbook / flow tests
step "EG contract & 能力分格 & flow tests"
if python3 "$ROOT/tests/test_errand_cli.py"; then ok "tests pass"; else bad "tests failed"; fi

# EG2 — negative redline: HTTP /api/v1 transport & legacy header/env residue must be
# gone from the DISTRIBUTED script（MCP 面 consent 走 tool argument，不走 header）.
step "EG2 negative redline (no /api/v1 / legacy header / legacy env)"
redline() {
  local pat="$1" label="$2"
  if grep -nE "$pat" "$SCRIPT" >/dev/null; then
    bad "found residue: $label"
    grep -nE "$pat" "$SCRIPT" | sed 's/^/      /'
  else
    ok "no $label"
  fi
}
redline '/api/v1' '/api/v1 path'
redline '"X-Consent-Grant-Id"|"X-User-Token"|"X-Admin-Secret"' 'legacy auth headers (consent rides as tool argument now)'
redline 'ERRAND_API_KEY|ERRAND_BASE_URL' 'legacy ERRAND_* env (now API_KEY / GATEWAY_MCP_URL)'
redline 'write_env_var|persisted_to_env' '.env writeback (shared cache is the single store)'

if grep -rnE '/api/v1' "$ROOT/skills/errand/scripts/" >/dev/null; then
  bad "scripts/ still contains /api/v1 references"
else
  ok "scripts/ clean of /api/v1"
fi

# EG2b — positive: MCP surface + 能力分格 present
step "EG2b new-surface presence"
grep -q 'tools/call' "$SCRIPT" && ok "speaks JSON-RPC tools/call" || bad "missing tools/call"
grep -q 'GATEWAY_MCP_URL' "$SCRIPT" && ok "reads GATEWAY_MCP_URL" || bad "missing GATEWAY_MCP_URL"
grep -q 'consent_grant_id' "$SCRIPT" && ok "passes consent_grant_id argument" || bad "missing consent_grant_id"
grep -q 'CLAWDOT_HOME' "$SCRIPT" && ok "supports CLAWDOT_HOME redirect" || bad "missing CLAWDOT_HOME"
grep -q 'errand-credentials.json' "$SCRIPT" && ok "能力分格：writes errand-credentials.json" || bad "missing 能力分格 cred file"
grep -q 'errand_quote' "$SCRIPT" && ok "maps to errand_* MCP tools" || bad "missing errand_* tool names"

printf '\n'
if [ "$FAILED" -eq 0 ]; then
  printf '✅ verify-errand.sh: ALL GATES GREEN (真下单/短信绑定为人核项)\n'
else
  printf '❌ verify-errand.sh: FAILURES ABOVE\n'
fi
exit "$FAILED"
