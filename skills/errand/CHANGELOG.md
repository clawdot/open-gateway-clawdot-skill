# Changelog

## [1.2.0] - 2026-09-20 — 对齐《跑腿 MCP 接口说明文档 v3.0》（跨 v2.0+v3.0 两个版本）

skill 此前对齐的是文档 **v1.0**，v2.0（2026-08-15）与 v3.0（2026-09-18）的变更全部未跟。
本次一次补齐：工具数 13 → **19**，与文档逐条对齐。

### 修掉会直接报错 / 说错话的（v3.0「需回归五处」）
- **预约时间**：原先让 agent「把时间换算成毫秒时间戳」——**会被网关拒**
  （`SCHEDULED_AT_NOT_ON_GRID`）。改为必须走 `list_schedule_slots` 取档位、回填其 `value`。
  一并补 5 个 `SCHEDULED_AT_*` 错误码的处理话术。
- **物品品类**：原先是 skill 本地硬编码的 9 类表、且**从不向网关传品类码**，
  而网关侧**理赔只看 `goods_category_code`、不看物品名**——等于每单都落在默认品类。
  改为调 `list_goods_categories`（15 项），按用户原话自动映射；落在 `valuable=true`
  的 7 个贵重品类时确认品类并问保价（保价额外收费，**绝不代用户开启**）。
- **时效反转**：v1.1.0 写着「报价里只有价格距离、没有时效」「**禁止**编造 30 分钟到」。
  v3.0 起报价已带 `estimated_minutes` / `estimated_arrival_time`——改为**如实报**，
  「要快」时可真比时效择快；两字段为 null（距离未知）时才不报。
- **`list_orders` 返回** 由数组改为 `{orders, next_offset}`，补翻页与状态/时间筛选参数。
- **`pre_cancel` 未支付单**不再报 `ERRAND_CANCEL_NOT_ALLOWED`，正常返回 0/0，
  文案改为「没付过钱没款可退，直接取消即可」。

### 补齐 6 个缺失工具
`auth_status`（查授权，失效不报错看 `bound`）、`revoke_user_bind`（解绑，
`--reset-history` 清退地址簿与历史单、不可逆，并同步作废本地缓存 cg）、
`update_address`（改址）、`delete_address`（删址）、
`list_goods_categories`、`list_schedule_slots`。

### 跟上 v2.0 漏掉的
- **`search_addresses` 返回 `saved_matches`**：用户已存过且命中本次搜索的地址，
  门牌电话都齐——直接取其 id 下单，**省掉「问门牌 + 问电话」两轮**。
- **按坐标搜地址**：`--lat --lng` 支持「从我现在的位置」（无地名可搜时反查地点名）。
- **禁运校验**：物品名与 remark 命中禁运回 `GOODS_PROHIBITED`——如实告知不能寄，
  **明令禁止改词重试绕过**。
- `--remark` 明确为「给骑手的留言、上限 200 字」（原先被当成本地"默认备注"用）。
- 地址簿判重口径＝**地址+门牌+联系人**；门牌**会原样回显**
  （v1.1.0 的「门牌不回显」已于 2026-08-06 按用户报的 bug 反转——看不见原值没法核对、只能盲改）。

### 其他新增
`create` 返回整单信息（可直接与预览卡片对账）+ `payment_expire_at`（15 分钟付款期限）；
`get_order` 对**未支付单**回 `cashier_url`——用户说「付款链接找不到了」可原样重发，不必重新下单；
取件/送达照片 `pickup_photos` / `finish_photos`。

### 修掉一个误路由
`PUBLIC_REFERENCE_INVALID` 原映射到 `QUOTE_EXPIRED`（"重新询价"）。v3.0 起该码专指
**callback_url 不是公网地址**，与报价无关——改为独立的 `CALLBACK_URL_INVALID`，
否则用户会收到驴唇不对马嘴的指引。错误 playbook 由 39 码扩到 **53 码**。

### 验证
- `tests/test_errand_cli.py`：新增 `test_v3_tool_mapping`（6 个新工具名 + 新参数逐条钉死，
  含 `tag=""` 清空标签不被当成"没传"丢掉）；码级全覆盖由 39 → 53 码；
  argparse 用例覆盖全部 19 个子命令。
- `verify-errand.sh` 全门禁通过；`build.py errand` 三平台产物正常。

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
