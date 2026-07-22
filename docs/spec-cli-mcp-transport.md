# SPEC — takeout skill 传输层迁移：HTTP /api/v1 → CLI×MCP

> WHY 见 `DECISIONS.md`「第二轮」M1–M10，本文只写 WHAT，不重抄决策理由。
> 能力 delta 一句话：**skill 的全部网关调用改经共享 CLI 走 MCP `tools/call`；分发物零 `/api/v1`、零 admin 凭据。**
> 验收总命令：`bash verify.sh`（MG1–MG6）；有凭据环境冒烟：
> `python3 skills/takeout/scripts/clawdot.py search_shops --keyword 咖啡 --lat 30.265 --lng 120.075`

## 1. Scope / Non-goals

**In scope**（实现 M5/M7/M8）：
- 新共享 CLI `clawdot.py`（替代 takeout.py 的 HTTP 客户端层 + action 分发层）
- SKILL.md / GUIDE.md / references 三件套改写（调用方式、子命令名、错误 playbook）
- verify.sh 按 MG1–MG6 更新

**Non-goals**（转发指针）：
- 服务端任何改动 → M5 红线，缺口停下重盘
- 跑腿/开发者 skill 迁移 → M8，将来同 CLI 加 paotui tools
- 对外 CLI 产品承诺 → M2，客户继续直接挂 MCP
- 混淆/客户端保密 → M1，非目标
- coupons/sign/unbind 等新能力的文档化 → 第一轮 D7 口径不变（CLI 机械可调，文档不引导）

## 2. 文件布局（实现 M2/M6）

```
skills/takeout/
  scripts/clawdot.py          ← 新：共享 CLI（纯 stdlib，单文件）
  scripts/takeout.py          ← 删除（PR 即切换点，回退 = revert PR，M9②）
  references/commands.md      ← 新：子命令总览 + 每命令参数（美团样板）
  references/params.md        ← 新：复杂参数对象（items 模型、地址对象）
  references/errors.md        ← 新：错误码 → RECOVERY playbook
  SKILL.md / GUIDE.md         ← 改写调用方式与流程
```

## 3. 传输契约（实现 M6）

- 端点：env `GATEWAY_MCP_URL`，默认 `https://eleme-gateway.hicaspian.com/mcp/v1`
  （旧 `GATEWAY_URL` 不再读取，INSTALL.md 同步改）。
- 每个子命令 = 一次 `POST $GATEWAY_MCP_URL`，无 initialize、无 session（stateless 已坐实）：

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "search_shops",
            "arguments": {"consent_grant_id": "cg_xxx", "keyword": "咖啡",
                          "latitude": 30.265, "longitude": 120.075}}}
```

- Headers：`Authorization: Bearer <API_KEY>`、`Content-Type: application/json`、
  `Accept: application/json`。
- 响应解析（四层，MG3）：

| 层 | 信号 | CLI 行为 |
| --- | --- | --- |
| HTTP 401/403 | 状态码 | stderr `RECOVERY[API_KEY_MISSING/INVALID]`，exit 1 |
| JSON-RPC error | 顶层 `error` | stderr 中文 + 原 code，exit 1（如 unknown tool） |
| 业务错误信封 | `result.content[0].text` 解出 `{"error":{code,message}}`（isError=False，网关契约） | 按 §7 playbook → stderr 中文 + `RECOVERY[CODE]`，exit 1 |
| 成功 | `result.content[0].text` 为业务 JSON | stdout 原样 JSON，exit 0 |

## 4. 鉴权与凭据（实现 M3/M4）

- `API_KEY`（env，唯一必需注入项）→ Bearer。无 ADMIN_SECRET/USER_TOKEN 任何残留（MG2）。
- consent 解析链（M4）：`CONSENT_GRANT_ID` env（只读预注入）→ 共享缓存该 key 指纹下唯一 phone
  → 多个要求 `--phone` → 引导绑定（`RECOVERY[USER_NOT_BOUND_NEEDS_SMS]` 语义保留）。
- 绑定流：`request_user_bind`(sms/h5) → `verify_user_bind` → 出参 cg 写共享缓存；
  **不回写 .env**。疑失效先 `get_user_auth_status` 验活，绝不静默重绑。
- 共享缓存 `~/.clawdot/credentials.json`（`CLAWDOT_HOME` 可重定向；目录 0700/文件 0600）：

```json
{"a1b2c3d4e5f6": {"13812345678": {"consent_grant_id": "cg_xxx",
                                   "updated_at": "2026-07-22T10:00:00Z"}}}
```

外层键 = `sha256(API_KEY)` 十六进制前 12 位。

## 5. 子命令契约（实现 M7）

- 基础面 1:1 MCP tool 名，通用分发器覆盖全部 delivery tools；**文档化子集**（references/commands.md
  只写下列流程命令，其余标"未文档化，agent 勿用"）：
  `request_user_bind` `verify_user_bind` `get_user_auth_status` `search_addresses` `select_address`
  `search_shops` `get_shop_menu` `get_item_options` `preview_order` `create_order` `get_order_status`
- 复合子命令：`recommend`（显式标注 composite，包装 search_shops + 排序呈现，语义与旧版一致）。
- 入参映射：`--kebab-flag` → tool argument 同名 snake_case；复杂对象（items、地址）收 `--json '<obj>'`
  或独立 flags（沿用旧版 argparse 习惯，详表落 references/params.md）。
- IO 契约不变（锚 SKILL.md 反幻觉铁律）：stdout=成功 JSON；stderr=中文 + `RECOVERY[CODE]`；exit 1。

## 6. 客户端隐式状态（继承第一轮 D5，实现 M7/MG5）

- cart_id：`search_shops`/`recommend` 出参按 shop_id 缓存（TTL < 上游 30min），`get_shop_menu`/
  `preview_order` 隐式取回，不对 agent 暴露；失效 → `RECOVERY[SHOP_CART_MISS]` 引导重搜。
- `preview_id` + `confirmation_token`：preview → create 贯穿，替代无此概念的裸调用。

## 7. 错误 playbook（实现 MG3）

第一轮 G7/G8 的全部定向映射原样迁移（外部码 + 实测码双套），信号源从 HTTP body 换成 MCP 业务信封：
- 顺位红线不变：`MISSING_REQUIRED_SELECTION` 先于 `MUST_PICK_REQUIRED`；`BELOW_MIN_PURCHASE` 先于
  `BELOW_MIN_ORDER`。
- 新增传输层码：`SHOP_CART_MISS`（本地缓存失效）、`API_KEY_MISSING/INVALID`（HTTP 401 层）。
- `CONSENT_GRANT_EXPIRED/REVOKED` → 引导重绑（M4：明确提示后重绑，不静默）。

## 8. SKILL.md / 文档改写范围（实现 M7/M8）

- 调用方式：`--action X` → 子命令；action 名机械替换（`request_code→request_user_bind` 等 8 个）。
- 行为铁律（stdout/stderr 唯一真相、禁编造、H5 链接原样）**一字不动**。
- 鉴权叙述：删 Personal/Agent 模式段，只留"API_KEY + 用户自授权绑定"一条线。

## 9. 验收（→ DECISIONS M 轮 MG1–MG6 / MH1–MH2；verify.sh 为硬 gate）

## 10. Rollout / 回退（实现 M8/M9）

1. 本分支完成实现 + MG 全绿 → 独立环境端到端（MG6 真链路到 pending_payment）。
2. 提 PR（GitHub），**不自动合并**；贴 MG6 输出 + MH1/MH2 证据。
3. 用户人工审核、手动合并 = 切换点；回退 = revert PR（旧版在 main 历史完整可恢复）。
