#!/usr/bin/env bash
# Verification gate for the CLI×MCP transport migration.
# Mirrors DECISIONS.md 第二轮 §验收标准 MG1-MG5. Green here is necessary (not
# sufficient: MG6 live end-to-end + MH1 SMS bind are run with real credentials
# and evidence attached to the PR). Run: bash verify.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$ROOT/skills/takeout/scripts/clawdot.py"
FAILED=0

step() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '  ✅ %s\n' "$1"; }
bad()  { printf '  ❌ %s\n' "$1"; FAILED=1; }

# MG1a — compiles
step "MG1a py_compile"
if python3 -m py_compile "$SCRIPT"; then ok "compiles"; else bad "py_compile failed"; fi

# MG1b — argparse smoke（子命令制）
step "MG1b argparse smoke"
python3 "$SCRIPT" --help >/dev/null 2>&1 && ok "--help exits 0" || bad "--help failed"
if python3 "$SCRIPT" bogus_command >/dev/null 2>&1; then
  bad "invalid subcommand was accepted"
else
  ok "invalid subcommand rejected"
fi

# MG1/MG3/MG4/MG5 — transport contract / error playbook / cred store / flow tests
step "MG1+MG3+MG4+MG5 contract & flow tests"
if python3 "$ROOT/tests/test_clawdot_cli.py"; then ok "tests pass"; else bad "tests failed"; fi

# MG2 — negative redline: HTTP /api/v1 transport & legacy auth residue must be
# gone from the DISTRIBUTED script. Operational forms only — docstrings may
# mention the migration.
step "MG2 negative redline (no /api/v1 / legacy auth residue)"
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
redline '"X-User-Token"|"X-Admin-Secret"|"X-Consent-Grant-Id"' 'legacy auth headers (consent rides as tool argument now)'
redline 'os\.environ\.get\("(ADMIN_SECRET|USER_TOKEN|REDIS_URL)"' 'ADMIN_SECRET/USER_TOKEN/REDIS_URL env'
redline 'trusted' 'trustedBind residue'
redline 'write_env_var|persisted_to_env' '.env writeback (M4: shared cache is the single store)'

# 分发目录整体（scripts/ 下不允许再有任何 /api/v1 脚本）
if grep -rnE '/api/v1' "$ROOT/skills/takeout/scripts/" >/dev/null; then
  bad "scripts/ still contains /api/v1 references"
else
  ok "scripts/ clean of /api/v1"
fi

# MG2b — positive: the MCP surface must be present
step "MG2b new-surface presence"
grep -q 'tools/call' "$SCRIPT" && ok "speaks JSON-RPC tools/call" || bad "missing tools/call"
grep -q 'GATEWAY_MCP_URL' "$SCRIPT" && ok "reads GATEWAY_MCP_URL" || bad "missing GATEWAY_MCP_URL"
grep -q 'consent_grant_id' "$SCRIPT" && ok "passes consent_grant_id argument" || bad "missing consent_grant_id"
grep -q 'CLAWDOT_HOME' "$SCRIPT" && ok "supports CLAWDOT_HOME redirect" || bad "missing CLAWDOT_HOME"

printf '\n'
if [ "$FAILED" -eq 0 ]; then
  printf '✅ verify.sh: ALL GATES GREEN (MG1-MG5; MG6/MH1 run live with credentials)\n'
else
  printf '❌ verify.sh: FAILURES ABOVE\n'
fi
exit "$FAILED"
