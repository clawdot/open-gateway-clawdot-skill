# OpenClaw 纯净环境测试流程（接 open-gateway-clawdot-skill）

> 改写自 open-gateway 仓库 `docs/测试配置/OpenClaw纯净环境测试流程.md`。
> 差异：部署的是**本仓库迁移后的 `takeout` skill**（构建名 `clawdot-takeout`，**open-gateway / consent_grant 体系**），
> 而非旧 `clawdot-food-takeout`（clawdot-gateway / USER_TOKEN 体系）。鉴权改为「只注入 `API_KEY`，cg 绑定后自动回写」。

## 与原流程的关键差异（先看这个）

| 项 | 原流程（旧 skill） | 本流程（我们的 skill） |
|---|---|---|
| skill 来源 | `clawdot-food-takeout-skill.zip`（VH 包） | 本仓库 `python3 build.py takeout` → `dist/takeout-openclaw/` |
| skill 名 | `clawdot-food-takeout` | `clawdot-takeout` |
| 网关 | clawdot-gateway | **open-gateway**（public v1） |
| 鉴权注入 | `API_KEY` + `USER_TOKEN` | **只 `API_KEY`**；`CONSENT_GRANT_ID` 开局留空，绑定后自动回写 |
| 配置引导话术 | 让用户给 API_KEY + USER_TOKEN | 让用户给 API_KEY；再 SMS/H5 绑定拿 cg |
| `.env` | 禁止写（走配置引导） | **本流程要写真 `.env`**（真实联调，见下） |

> ⚠️ 现有 `clawdot-food-takeout-agent` 的 `AGENT.md`/`config.json` 硬绑旧 skill 名与 USER_TOKEN 话术，
> **不能直接驱动本 skill**。本流程默认用 OpenClaw 自带 `main` agent 调用我们的 skill；要专用 agent 需另行重调（不在本流程范围）。

## 前置输入（必须先有）

```bash
# open-gateway 部署对外地址（以实际部署为准，本机/远端都行；末尾不要带 /api/v1）
export OG_GATEWAY_URL="<填 open-gateway 部署地址>"
# agent 的 API Key（clw_…，不要写进任何提交物）
export CLAWDOT_API_KEY="<填 clw_ 开头的 key>"
# 完整 UI 对话还需要一个模型 key（只做只读冒烟可不填）
export DEEPSEEK_API_KEY="<可选：DeepSeek key>"
```

---

## Step A. 直连只读冒烟（最快看效果，强烈建议先做）

不经 OpenClaw/agent/模型，直接用 skill 脚本验证「我们的 skill ↔ 真实 open-gateway」是否打通。**只读、不下单、不花钱。**

```bash
cd <本仓库根>
python3 build.py takeout >/dev/null            # 产出 dist/takeout-openclaw/
SKILL=dist/takeout-openclaw

# 写真 .env（仅 API_KEY；cg 绑定后自动回写；权限交给脚本收 0600）
cat > "$SKILL/.env" <<EOF
GATEWAY_URL=$OG_GATEWAY_URL
API_KEY=$CLAWDOT_API_KEY
EOF

# 1) 未绑定时：任意业务 action 应返回 RECOVERY[USER_NOT_BOUND_NEEDS_SMS]（预期，不是失败）
python3 "$SKILL/scripts/takeout.py" --action search --shop-keyword 瑞幸 --lat 31.23 --lng 121.47

# 2) 绑定（需真人）：发短信验证码 → 用户回 6 位码 → 验证（cg 自动回写 .env）
python3 "$SKILL/scripts/takeout.py" --action request_code --phone <11位手机号>
#   用户回码后：
python3 "$SKILL/scripts/takeout.py" --action verify_code --phone <11位手机号> --bind-id <上一步的bind_id> --code <用户的6位码>
#   → 成功后 .env 自动多出 CONSENT_GRANT_ID=cg_…，persisted_to_env=true

# 3) 绑定后（无需 --phone）：只读链路应返回真实数据
python3 "$SKILL/scripts/takeout.py" --action search   --shop-keyword 瑞幸 --lat 31.23 --lng 121.47
python3 "$SKILL/scripts/takeout.py" --action menu      --shop-id <上一步返回的 shop_id>
python3 "$SKILL/scripts/takeout.py" --action preview   --shop-id <shop_id> --address-id <addr_id> --items '[{"item_id":"<item_>","quantity":1}]'
#   preview 是试算（返回 preview_id+confirmation_token+价格），**不要**接着 order（那才是真实下单/花钱）
```

判读：`search/menu/preview` 返回带 `shop_id/cart_id/item_id/sku_options/preview_id` 的 JSON = 迁移在真实网关上跑通。任一步报错看 stderr 的 `RECOVERY[...]` 按提示走。

> 安全：`order`（真实下单+付款）属 money path，**未经你明确同意不跑**。

---

## Step 1-8. 完整 OpenClaw 环境（可选，要 DeepSeek key + 浏览器）

沿用原流程，仅把 skill 来源与 env 换成我们的。变量：

```bash
PROFILE=og-takeout-clean
ROOT=/private/tmp/openclaw-$PROFILE
STATE="$HOME/.openclaw-$PROFILE"
GATEWAY_PORT=18899
GATEWAY_TOKEN=local-og-takeout-ui
```

1. 清理：`rm -rf "$ROOT" "$STATE"; mkdir -p "$ROOT/workspace/skills"`
2. 初始化纯净 profile（同原 doc 第 3 步，`--profile "$PROFILE"`、`--skip-skills` 等不变）。
3. 配 DeepSeek（同原 doc 第 4 步的 Python 脚本，把 `profile` 改成 `og-takeout-clean`；需 `DEEPSEEK_API_KEY`）。
4. **部署我们的 skill**（解构建产物到 workspace，并写真 `.env`）：

```bash
cp -R dist/takeout-openclaw "$ROOT/workspace/skills/clawdot-takeout"
cat > "$ROOT/workspace/skills/clawdot-takeout/.env" <<EOF
GATEWAY_URL=$OG_GATEWAY_URL
API_KEY=$CLAWDOT_API_KEY
EOF
openclaw --profile "$PROFILE" skills list   # 应看到 clawdot-takeout
```

5. 启动：`openclaw --profile "$PROFILE" gateway run --force`（另开终端 `dashboard --no-open` 拿 UI URL）。

## Step 9. 首次对话预期（consent 流程，已改）

用户说"我想点外卖"且 `.env` 已有 `API_KEY`、但还没 `CONSENT_GRANT_ID` 时，应引导**绑定**（不是要 USER_TOKEN）：

```text
先告诉我手机号，顺便选一下用 H5 还是验证码方式绑定哦～
```

- 短信：用户给手机号 → 收到 6 位码回复 → 绑定成功，cg 自动写入 skill `.env` → 之后正常点餐。
- 若连 `API_KEY` 都没写，则先返回 `RECOVERY[API_KEY_MISSING]` 引导去注册页拿 key。

## Step 10. 安全红线（本流程）

- `API_KEY` / `CONSENT_GRANT_ID` / 模型 key **不写进文档、不提交 Git**（`.env` 已 gitignore）。
- **未经明确同意不跑真实 `order`**（下单+付款）。只读链路（search/menu/preview 试算）随便跑。
- 不改 skill 包内文件（`.env` 是运行期配置，不算包内容）。
