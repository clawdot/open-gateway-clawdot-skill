---
name: clawdot-errand
description: 通过 ClawDot 跑腿网关帮用户叫同城跑腿——帮取送（A地取件送到B地，支持餐饮、文件、生鲜、蛋糕、鲜花、数码、服饰、快递等品类）。当用户提到"帮我送/寄/取/拿/带"某个东西到某处、叫跑腿、下跑腿单、同城配送、叫闪送/达达/顺丰同城、取快递、寄文件、送钥匙、送花、送蛋糕、叫骑手、帮我跑一趟等需求时触发。注意：本技能只做「点到点送东西」，点外卖/买吃的走外卖技能，代购帮买暂不支持。
metadata:
  requires:
    bins: [python3]
    env: []
    env_optional: [GATEWAY_MCP_URL, API_KEY, CONSENT_GRANT_ID, CLAWDOT_HOME, CLAWDOT_SETUP_URL]
---

{{GUIDE}}

## 调用方式

所有操作通过 `python3 {baseDir}/scripts/errand.py <command> [--phone <手机号>]` 调用（子命令 1:1 对应网关 MCP tool 名，内部映射到 `errand_*` 工具）：

| command | 用途 | 关键参数 |
|--------|------|----------|
| request_user_bind | 绑定第 1 步：发短信验证码（跑腿仅短信模式） | --phone, --external-user-id? |
| verify_user_bind | 绑定第 2 步：核验码，成功后写共享缓存 | --phone --bind-id --code |
| list_addresses | 列该手机号名下地址簿（选收发地址） | --phone? |
| search_addresses | POI 关键词搜地点 → 候选给用户挑（绝不自动取第一个） | --keyword, --city? |
| save_address | 把选中的地址存进地址簿复用 | --address --lat --lng, --contact-name? --contact-phone? --detail? --tag? |
| list_orders | 近几单历史（"还是上次那样"复用收发/物品） | --limit? |
| quote | 询价：多运力报价 + quote_id（可预约/专人直送/保价） | --goods-name + 每端(--from/to-id 或 --from/to-text/lat/lng)；--goods-price? --weight? --remark? --scheduled-at? --person-direct? --insured? --to-name? --to-phone? |
| create | 核销 quote_id + 选定运力下单，返回付款链接 | --quote-id --company-code |
| get_order | 查订单状态/时间线/骑手 | --order-id |
| get_rider | 骑手实时位置（配送中才有） | --order-id |
| pre_cancel | 取消前查违约金/可退金额 | --order-id |
| cancel | 取消订单（已付按 实付−违约金 退） | --order-id, --reason? |
| add_tip | 加小费催单（独立付款链接） | --order-id --tip-fee |

### 鉴权：只需注入 API_KEY，cg 绑定后写入共享缓存

| 情形 | 业务调用方式 | 凭证来源 |
|------|------------|---------|
| 单用户（默认） | 不传 `--phone` | 共享缓存（`~/.clawdot/errand-credentials.json`，按 API_KEY+手机号键控）中唯一已绑用户；也可用 `CONSENT_GRANT_ID` 环境变量预注入长效 cg（只读，优先级最高） |
| 多用户 | 传 `--phone <11 位>` | 各用户各自绑定，按手机号存共享缓存 |

唯一必需注入的是 `API_KEY`（agent 身份）。一个 api_key 可服务多个用户，**一个 cg = 一个用户**（到期/轮换要重绑）。绑定步骤（request_user_bind/verify_user_bind）需 `--phone`；绑定成功写入共享缓存后，单用户业务调用无需再带 `--phone`。`API_KEY` 没配时脚本返回 `RECOVERY[API_KEY_MISSING]`，按指引引导用户去注册页拿 key 写入 `.env`。**open-gateway 无 admin 静默绑定**——每个用户必须本人走一次短信授权。

> **能力分格（跑腿 ≠ 外卖）**：跑腿授权与外卖是两条独立能力，consent 分能力发放、互不通用；同一手机号要用两条线得各绑一次。跑腿 cg 写 `errand-credentials.json`、外卖 cg 写 `credentials.json`，各存各的、互不覆盖。

### 地址

- `list_addresses`（无参数）→ 列该用户已存地址簿（跑腿只能用地址簿里/已选坐标的地址）
- `search_addresses --keyword "西湖文化广场" [--city "杭州"]` → POI 候选 `[{name, address, lat, lng}]`，**逐行列给用户挑，绝不自动取第一个**
- `save_address --address "..." --lat --lng [--contact-name --contact-phone --detail "1栋502" --tag 家]` → 存进地址簿，返回 `address_id`（plat_）供后续 quote 用。**存了电话/门牌，下次拿这个 id 下单就不用再问用户**

### 下单两步交接（stateless，id 靠 stdout 传递）

- `quote` 返回 `quote_id` + `quotes[]`（每项 `{company_code, company_name, fee, distance, coupon_fee}`）+ `expires_in_seconds`
- 从 `quotes` 选定运力（无偏好取 fee 最小），带其 `company_code` 与 `quote_id` 调 `create`
- `create --quote-id <quote_id> --company-code <code>` 返回 `order_id` + `cashier_url`（付款链接）+ `status: pending_payment`
- **金额字段单位均为分**；后续 `get_order`/`cancel`/`add_tip` 都带 `create` 返回的 `order_id`

### 环境变量

| 变量 | 必须？ | 用途 |
|------|--------|------|
| GATEWAY_MCP_URL | 可选 | ClawDot 跑腿网关 MCP 端点（默认 `https://paotui.hicaspian.com/mcp/v1`；接受 origin，自动补 /mcp/v1） |
| API_KEY | ✅ | Gateway API 密钥（clw_，agent 身份；唯一必需注入项，须已开通 errand 能力） |
| CONSENT_GRANT_ID | 可选 | 用户授权凭证（cg_）只读预注入；正常绑定后存共享缓存，无需配置 |
| CLAWDOT_HOME | 可选 | 共享凭证缓存目录（默认 ~/.clawdot） |

### 输出格式

- 成功：JSON 输出到 stdout
- 失败：中文错误 + `RECOVERY[CODE]: <下一步>` 输出到 stderr，非零退出码
- 按 stderr 的 RECOVERY 提示选下一个动作；付款链接（cashier_url）原样发用户、禁止改写脱敏

### 执行铁律

- **默认能服务**：只要 skill 已加载、能调脚本，就当作可下单推进；禁止凭空判定"无法服务"而拒答。仅当脚本实际返回 RECOVERY/非零退出码时才按其指引处理。
- **不谎报成功**：任何成功结论必须以脚本 stdout 实际返回为准；脚本报错即如实告知失败，禁止编造"已下单/已送达"。
- **地址先确认后推进**：POI 候选必须用户亲自选、不自动取第一个；展示给用户的地址只用人类可读文本，禁止暴露内部 id/坐标。
- **手机号一律脱敏**：展示成 `138****5678`。地址簿回的就是脱敏号，原样用别去还原；用户自己刚说的号，复述时也脱敏。唯一例外是付款链接（一个字符都不能改）。
- **花钱先确认**：下单、加小费、取消都要用户明确点头再执行（取消先报违约金）。
