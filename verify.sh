#!/usr/bin/env bash
# Verification gate for the open-gateway takeout skill migration.
# Mirrors DECISIONS.md §验收标准 G1-G5. Green here is necessary (not sufficient:
# end-to-end real ordering is H1, human-verified — needs a live API_KEY + real
# consent). Run: bash verify.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$ROOT/skills/takeout/scripts/takeout.py"
FAILED=0

step() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf '  ✅ %s\n' "$1"; }
bad()  { printf '  ❌ %s\n' "$1"; FAILED=1; }

# G1 — compiles
step "G1 py_compile"
if python3 -m py_compile "$SCRIPT"; then ok "compiles"; else bad "py_compile failed"; fi

# G2 — argparse smoke
step "G2 argparse smoke"
python3 "$SCRIPT" --help >/dev/null 2>&1 && ok "--help exits 0" || bad "--help failed"
if python3 "$SCRIPT" --action bogus >/dev/null 2>&1; then
  bad "invalid --action was accepted"
else
  ok "invalid --action rejected"
fi

# G3 + G5 — contract & flow tests
step "G3+G5 contract & flow tests"
if python3 "$ROOT/tests/test_takeout_gateway.py"; then ok "tests pass"; else bad "tests failed"; fi

# G4 — operational negative redline (old gateway residue must be gone).
# Grep for *operational* forms only — the module docstring intentionally names
# the old symbols to explain the migration, which is fine.
step "G4 negative redline (no operational legacy residue)"
redline() {
  local pat="$1" label="$2"
  if grep -nE "$pat" "$SCRIPT" >/dev/null; then
    bad "found legacy residue: $label"
    grep -nE "$pat" "$SCRIPT" | sed 's/^/      /'
  else
    ok "no $label"
  fi
}
redline '"X-User-Token"|X-User-Token"\]|X-Admin-Secret' 'X-User-Token/X-Admin-Secret header'
redline 'os\.environ\.get\("(ADMIN_SECRET|USER_TOKEN)"' 'ADMIN_SECRET/USER_TOKEN env'
redline '/api/v1/user/bind/' 'legacy /user/bind/ path'
redline 'def trusted_bind|\.trusted_bind\(' 'trusted_bind method'
redline 'session_id|session-id' 'session_id order handoff'
# raw phone as an emitted JSON field (own line, comma-terminated) — NOT the request
# body {"phone": phone, "auth_type": ...} which legitimately must send it to the gateway.
redline '"phone": phone,$' 'raw phone in output() (must emit phone_masked)'

# Positive: the new auth surface must be present
step "G4b new-surface presence"
grep -q 'X-Consent-Grant-Id' "$SCRIPT" && ok "uses X-Consent-Grant-Id" || bad "missing X-Consent-Grant-Id"
grep -q 'CONSENT_GRANT_ID' "$SCRIPT" && ok "reads CONSENT_GRANT_ID env" || bad "missing CONSENT_GRANT_ID"
grep -q '/api/v1/orders/create' "$SCRIPT" && ok "uses /orders/create" || bad "missing /orders/create"

printf '\n'
if [ "$FAILED" -eq 0 ]; then
  printf '✅ verify.sh: ALL GATES GREEN\n'
else
  printf '❌ verify.sh: FAILURES ABOVE\n'
fi
exit "$FAILED"
