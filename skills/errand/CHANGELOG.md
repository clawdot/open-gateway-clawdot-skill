# Changelog

## [1.1.0] - 2026-07-24 — 对标美团跑腿补交互差距：地址簿带电话、错误码全覆盖、不说做不到的话

对标 `meituan-paotui` skill 逐步比交互流程后，补掉三类差距。

### 地址簿带电话/门牌 → 少问两轮（配合网关同期改动）
- `save_address` 新增 `--contact-phone`：存一次，之后拿这个地址下单**不必再报手机号**。
  电话落库即密文，`list_addresses` 只回脱敏 `contact_phone_masked`（`138****5678`）。
- **修死数据 bug**：`_endpoint` 此前无条件把 `contact_phone` 填成下单人主号、
  `contact_name` 填成字面串"收件人"，导致网关"入参优先"永远命中入参——地址簿存的电话
  成了死数据，**"给妈妈寄东西"骑手拿到的是下单人自己的号**。现在地址簿形态留空交给
  网关兜底，坐标形态维持原兜底（零回归）。
- 门牌（`--detail`）存了下单会自动拼进地址发骑手，但**不回显**；地址簿也不回明文电话。
- GUIDE：门牌与收件人信息**合成一句问**，不再拆两轮；新增地址列表展示模板（①②③ 每条
  独立成块 + 脱敏电话）。

### 错误码全覆盖
- playbook 由 12 条补到 30 条，覆盖网关 errand 面**全部**业务码
  （NO_RIDER / CANCEL_NOT_ALLOWED / TIP_NOT_ALLOWED / FEE_CHANGED / SMS_COOLDOWN /
  PAYMENT_AMOUNT_MISMATCH / CASHIER_UNAVAILABLE / 各类地址与报价失败…）。
  此前未认领的码会把网关英文原文抛给用户。
- 新增 `test_error_playbook_covers_every_gateway_code` 锁死全覆盖，且未知码仍走兜底
  （防过宽正则把陌生错误误判成已知情形）。

### 不说做不到的话
- 报价里**只有价格和距离、没有时效**。删掉"要快就报几家让他挑"这类做不到的承诺，
  改为如实说明 + 给真正可用的手段（`--person-direct` 专人直送 / 下单后加小费催单）；
  明令禁止编造"大概 30 分钟到"。
- 全局输出红线新增**手机号一律脱敏**（三个 platform 入口文件同步）。

## [1.0.0] - 2026-07-22 — 跑腿技能首次并入 open-gateway-clawdot-skill（MCP 传输）

`errand`（跑腿）技能作为**第二个技能**并入本仓库，与 `takeout`（外卖）同源共用一套构建/
安装/发布骨架（`build.py` / `install.sh` / manifest / CI），但**能力分格、互不串扰**。

### 传输与鉴权
- 脚本 `scripts/errand.py` 走 **MCP 客户端**：每个子命令 = 一次 JSON-RPC `tools/call` POST 到
  `GATEWAY_MCP_URL`（默认 `https://paotui.hicaspian.com/mcp/v1`，stateless、纯标准库无依赖）。
  由独立 HTTP 客户端（`/api/v1/errand/*` + `X-Consent-Grant-Id` header）迁移而来，
  功能等价、底层改用网关 MCP 面（13 个 `errand_*` 工具）。
- 鉴权：`Authorization: Bearer <api_key>` 认 agent；用户态 cg 作 `consent_grant_id` **工具参数**
  （绑定类工具不带）。`API_KEY` 是唯一必需注入项。

### 能力分格（跑腿 ≠ 外卖，治"串"）
- 跑腿 consent 与外卖**分能力发放、互不通用**（cap 不互通）。凭证缓存**按能力分格到独立文件**：
  跑腿 cg 写 `~/.clawdot/errand-credentials.json`、外卖 cg 写 `~/.clawdot/credentials.json`，
  各写各的；同一手机号在两条能力下各持一个 cg，文件级隔离故天然不互踢。
- 绑定仅**短信验证码**模式（跑腿上游无终端账号 OAuth，无 H5）。

### 命令
- 13 个子命令 1:1 对应网关 `errand_*` 工具：`request_user_bind` / `verify_user_bind` /
  `list_addresses` / `search_addresses` / `save_address` / `list_orders` / `quote` / `create` /
  `get_order` / `get_rider` / `pre_cancel` / `cancel` / `add_tip`。
- 下单两步交接为 **stateless**：`quote` 返回 `quote_id` + `quotes[]`，`create --quote-id --company-code`
  核销下单（quote_id/order_id 由 stdout 显式传递，客户端不缓存业务状态）。
- `quote` 支持预约送达（`--scheduled-at`）、专人直送（`--person-direct`）、保价（`--insured`）。
- 成功 → stdout JSON；失败 → stderr 中文 + `RECOVERY[CODE]`（exit 1）。付款/小费均由用户点开
  收银台链接自行完成，助手不经手支付。
