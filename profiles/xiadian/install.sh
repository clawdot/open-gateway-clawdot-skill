#!/usr/bin/env bash
# 把 profiles/xiadian 包物化成一个 OpenClaw profile。
# 用法：
#   API_KEY=clw_xxx DEEPSEEK_API_KEY=sk-xxx bash install.sh [--profile og-xiadian] [--port 18901] [--with-direct-mcp]
# 幂等：重复跑会重建该 profile（只动自己的 profile，不碰默认 ~/.openclaw）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="og-xiadian"
PORT="18901"
WITH_DIRECT_MCP="0"
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --with-direct-mcp) WITH_DIRECT_MCP="1"; shift ;;
    *) echo "未知参数 $1" >&2; exit 1 ;;
  esac
done

ROOT="/private/tmp/openclaw-$PROFILE"
STATE="$HOME/.openclaw-$PROFILE"
TOKEN="local-$PROFILE-ui"
GATEWAY_MCP_URL="${GATEWAY_MCP_URL:-https://eleme-gateway.hicaspian.com/mcp/v1}"

# .env 同目录可放 API_KEY / DEEPSEEK_API_KEY（env 变量优先）
if [ -f "$HERE/.env" ]; then set -a; . "$HERE/.env"; set +a; fi
: "${API_KEY:?需要 API_KEY（clw_…），从 env 或 profiles/xiadian/.env 提供}"

echo "==> 1/6 初始化纯净 profile: $PROFILE (port $PORT)"
rm -rf "$ROOT" "$STATE"
mkdir -p "$ROOT/workspace/skills"
openclaw --profile "$PROFILE" onboard \
  --mode local --workspace "$ROOT/workspace" \
  --gateway-port "$PORT" --gateway-auth token --gateway-token "$TOKEN" \
  --auth-choice skip --non-interactive --accept-risk \
  --skip-daemon --skip-ui --skip-skills --skip-search --skip-channels --skip-health \
  --json >/dev/null

echo "==> 2/6 写入 SOUL.md（虾点人格）"
cp "$HERE/SOUL.md" "$ROOT/workspace/SOUL.md"

echo "==> 3/6 配置模型"
if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  PROFILE="$PROFILE" DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" python3 - <<'PY'
import json, os
from pathlib import Path
key = os.environ["DEEPSEEK_API_KEY"].strip()
profile = os.environ["PROFILE"]
root = Path.home() / f".openclaw-{profile}"
model_ref = "deepseek/deepseek-chat"
provider = {"deepseek": {"baseUrl": "https://api.deepseek.com", "api": "openai-completions",
    "models": [{"id": "deepseek-chat", "name": "DeepSeek Chat", "api": "openai-completions",
                "reasoning": False, "input": ["text"],
                "cost": {"input": 0.28, "output": 0.42, "cacheRead": 0.028, "cacheWrite": 0},
                "contextWindow": 131072, "maxTokens": 8192,
                "compat": {"supportsUsageInStreaming": True}}],
    "apiKey": key}}
cfg_path = root / "openclaw.json"
cfg = json.loads(cfg_path.read_text())
d = cfg.setdefault("agents", {}).setdefault("defaults", {})
d["models"] = {model_ref: {"alias": "DeepSeek"}}
d["model"] = {"primary": model_ref}
cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
agent_dir = root / "agents/main/agent"
agent_dir.mkdir(parents=True, exist_ok=True)
mp = agent_dir / "models.json"
m = json.loads(mp.read_text()) if mp.exists() else {"providers": {}}
m.setdefault("providers", {}).update(provider)
mp.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n")
ap = agent_dir / "auth-profiles.json"
a = json.loads(ap.read_text()) if ap.exists() else {"version": 1, "profiles": {}}
a.setdefault("version", 1)
a.setdefault("profiles", {})["deepseek:default"] = {"type": "api_key", "provider": "deepseek", "key": key}
a.setdefault("lastGood", {})["deepseek"] = "deepseek:default"
a.setdefault("usageStats", {}).setdefault("deepseek:default", {"errorCount": 0})
ap.write_text(json.dumps(a, ensure_ascii=False, indent=2) + "\n")
print("   deepseek/deepseek-chat 已配置")
PY
else
  echo "   跳过（无 DEEPSEEK_API_KEY；请自行配置模型）"
fi

echo "==> 4/6 安装 takeout skill"
SKILL_DIR="$ROOT/workspace/skills/clawdot-takeout"
mkdir -p "$SKILL_DIR"
if [ -f "$HERE/skills/clawdot-takeout/skill.yaml" ]; then
  echo "   使用包内内置 skill（自包含，离线可装）"
  cp -R "$HERE/skills/clawdot-takeout/." "$SKILL_DIR/"
else
  echo "   包内无内置 skill，从 GitHub release latest 拉取"
  ASSET_URL="$(curl -fsSL https://api.github.com/repos/clawdot/open-gateway-clawdot-skill/releases/latest \
    | python3 -c 'import json,sys;print([a["browser_download_url"] for a in json.load(sys.stdin)["assets"] if "openclaw" in a["name"]][0])')"
  curl -fsSL "$ASSET_URL" | tar xz -C "$SKILL_DIR"
fi
{
  echo "GATEWAY_MCP_URL=$GATEWAY_MCP_URL"
  echo "API_KEY=$API_KEY"
  [ -n "${CLAWDOT_HOME:-}" ] && echo "CLAWDOT_HOME=$CLAWDOT_HOME"
} > "$SKILL_DIR/.env"
chmod 600 "$SKILL_DIR/.env"

echo "==> 5/6 MCP 直连（可选）"
if [ "$WITH_DIRECT_MCP" = "1" ]; then
  python3 - "$HERE/mcp.json" <<'PY' | while read -r name json; do
import json, sys
cfg = json.load(open(sys.argv[1]))
for name, srv in cfg.get("servers", {}).items():
    srv.pop("enabled", None)
    print(name, json.dumps(srv, ensure_ascii=False))
PY
    openclaw --profile "$PROFILE" mcp set "$name" "$json" || echo "   ⚠️ mcp set $name 失败（schema 可能不符，见 openclaw mcp --help）"
  done
else
  echo "   跳过（外卖走 skill 内 CLI；直连实验加 --with-direct-mcp）"
fi

echo "==> 6/6 校验"
openclaw --profile "$PROFILE" skills list 2>&1 | grep -i takeout || { echo "❌ skill 未注册"; exit 1; }

cat <<EOF

✅ 完成。启动：
   openclaw --profile $PROFILE gateway run
   UI: http://127.0.0.1:$PORT/   token: $TOKEN
EOF
