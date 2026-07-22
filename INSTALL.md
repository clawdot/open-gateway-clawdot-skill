# open-gateway-clawdot-skill — Agent 安装指引

> 本文件面向 AI Agent。如果你是 Agent，直接按下方步骤执行即可完成技能安装。
> 人类用户请参考 README.md 中的安装说明。

## 安装步骤

### 1. 确定你的平台

| 平台 | 安装位置 | 技能文件 |
|------|----------|----------|
| Claude Code | `~/.claude/skills/clawdot-<skill>/` | SKILL.md |
| Codex | 项目根目录 | AGENTS.md |
| OpenClaw | `~/.openclaw/skills/clawdot-<skill>/` | SKILL.md |

### 2. 执行安装

以 `takeout` 技能为例：

**Claude Code:**

```bash
curl -fsSL https://raw.githubusercontent.com/clawdot/open-gateway-clawdot-skill/main/install.sh | bash -s -- takeout claude-code
```

**Codex（在项目根目录执行）:**

```bash
curl -fsSL https://raw.githubusercontent.com/clawdot/open-gateway-clawdot-skill/main/install.sh | bash -s -- takeout codex
```

**OpenClaw:**

```bash
curl -fsSL https://raw.githubusercontent.com/clawdot/open-gateway-clawdot-skill/main/install.sh | bash -s -- takeout openclaw
```

安装脚本会自动从最新 Release 下载对应平台的技能包，校验 sha256 后解压到正确位置。

### 3. 配置环境变量

安装完成后需要配置以下环境变量（也可放在 `<安装目录>/.env`，脚本自动加载）：

| 变量 | 说明 | 必需 |
|------|------|------|
| `GATEWAY_MCP_URL` | ClawDot open-gateway MCP 端点（默认公网地址；接受 origin，自动补 /mcp/v1） | 可选 |
| `API_KEY` | open-gateway API 密钥（clw_，agent 身份） | **唯一必需注入项**（缺失时返回 RECOVERY[API_KEY_MISSING] 引导去注册页获取） |
| `CONSENT_GRANT_ID` | 用户授权凭证（cg_，=一个用户）的只读预注入项；正常流程绑定后存共享缓存，无需配置 | 可选 |
| `CLAWDOT_SETUP_URL` | API_KEY 缺失时的注册/登录引导页（默认 ClawDot developer 登录页） | 可选 |
| `CLAWDOT_HOME` | 共享凭证缓存目录（默认 ~/.clawdot） | 可选 |
| `DEFAULT_LAT` / `DEFAULT_LNG` | 默认配送坐标（冷启动兜底） | 推荐 |

> **鉴权一条线**：只需注入 `API_KEY`。首跑业务命令会返回 `RECOVERY[USER_NOT_BOUND_NEEDS_SMS]`，
> 按指引引导用户走短信验证码（默认）或 H5 链接授权（`request_user_bind` → `verify_user_bind`，需 `--phone`）；
> verify 成功后 cg **写入共享凭证缓存**（`~/.clawdot/credentials.json`），之后单用户业务调用无需 `--phone`。
> 多用户（一个 api_key 服务多人）则各自绑定、业务调用带 `--phone`。open-gateway 无 admin 静默绑定，每个用户须本人授权一次。

### 4. 验证安装

```bash
python3 <安装目录>/scripts/clawdot.py search_addresses
```

已绑定（共享缓存有凭证，或预注入了 `CONSENT_GRANT_ID`）返回 JSON 地址列表即成功；首次未绑定会返回
`RECOVERY[USER_NOT_BOUND_NEEDS_SMS]` 引导绑定——这是预期行为，不是失败。

## 可用技能

| 技能 | 平台 | 说明 |
|------|------|------|
| `takeout` | claude-code, codex, openclaw | 外卖点餐 |

## 安装指定版本

```bash
curl -fsSL https://raw.githubusercontent.com/clawdot/open-gateway-clawdot-skill/main/install.sh | bash -s -- takeout claude-code v1.0.0
```

## 手动安装（不使用安装脚本）

```bash
# 1. 获取 manifest 找到最新包名
MANIFEST=$(curl -fsSL https://github.com/clawdot/open-gateway-clawdot-skill/releases/latest/download/manifest.json)
ASSET=$(echo "$MANIFEST" | python3 -c "import json,sys; print(json.load(sys.stdin)['skills']['takeout']['claude-code']['asset'])")

# 2. 下载并解压（以 Claude Code 为例）
mkdir -p ~/.claude/skills/clawdot-takeout
curl -fsSL "https://github.com/clawdot/open-gateway-clawdot-skill/releases/latest/download/${ASSET}" | tar xz -C ~/.claude/skills/clawdot-takeout

# 3. 配置环境变量后验证
python3 ~/.claude/skills/clawdot-takeout/scripts/clawdot.py search_addresses
```
