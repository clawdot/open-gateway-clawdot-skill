# 命令总览（errand.py）

调用形式：`python3 scripts/errand.py <command> [--phone <11位>] [参数...]`

子命令 1:1 对应网关 MCP tool 名（内部映射到 `errand_*`）。全局参数 `--phone` 仅多用户
场景需要（单用户绑定后自动命中缓存唯一用户）。绑定类命令（request/verify）必带 `--phone`。

| command | 说明 | 必填参数 |
|---|---|---|
| `request_user_bind` | 绑定第 1 步：发短信验证码 | `--phone` |
| `verify_user_bind` | 绑定第 2 步：核验码，成功写共享缓存 | `--phone --bind-id --code` |
| `list_addresses` | 列该用户已存地址簿 | 无 |
| `search_addresses` | POI 关键词搜地点 → 候选 | `--keyword` |
| `save_address` | 把候选地址（可带门牌/电话）存进地址簿 | `--address --lat --lng` |
| `list_orders` | 近几单历史（"还是上次那样"） | 无 |
| `quote` | 询价 → `quote_id` + 多运力报价 | `--goods-name` + 收发两端各一（id 或 text+坐标）|
| `create` | 核销 quote_id 下单 → 付款链接 | `--quote-id --company-code` |
| `get_order` | 查订单状态/时间线/骑手 | `--order-id` |
| `get_rider` | 骑手实时位置 | `--order-id` |
| `pre_cancel` | 取消前查违约金/可退金额 | `--order-id` |
| `cancel` | 取消订单 | `--order-id` |
| `add_tip` | 加小费（独立付款链接） | `--order-id --tip-fee` |

## 各命令可选参数

### request_user_bind
```
--external-user-id <id>   客户侧用户唯一标识（可选），注入收银台联登/支付 open_id
```

### quote
```
每端二选一：
  --from-id <id> / --to-id <id>            地址簿地址 id（list_addresses / save_address 返回）
  --from-text/-lat/-lng、--to-text/-lat/-lng  POI 搜索选中的名称+坐标（GCJ-02）
--from-name / --from-phone                发件联系人/电话（可省，见下）
--to-name / --to-phone                    收件联系人/电话（可省，见下）
    走 --from-id/--to-id 时：地址簿存了联系人/电话就**自动带上**，不必传、也不必问用户；
    传了则以传的为准。坐标形态（--*-text/-lat/-lng）没有地址簿可兜底，两者必给，
    否则 RECOVERY[CONTACT_REQUIRED]。
--goods-name <名>                         物品名（写用户原话）
--goods-price <分>                        货值（分），贵重物品可带
--weight <克>                             总重量，默认 1000
--remark <备注>                           给骑手的备注
--scheduled-at <毫秒时间戳>               预约送达时间；不传=即时单
--person-direct                           专人直送（不拼单，费用更高）
--insured                                 保价（按货值口径）
```

### save_address
```
--address <文本>          地址（search_addresses 候选的 name+address）
--lat / --lng             坐标（GCJ-02，取自候选）
--contact-name <名>       联系人（可选）
--contact-phone <号>      联系电话（可选）；存了之后拿这个地址下单**不必再报手机号**。
                          落库即密文，list_addresses 只回脱敏 138****5678
--detail <门牌>           门牌/楼层；存了下单会自动拼进地址发骑手，但**不回显**
--tag <标签>              如 家/公司
```

### create
```
--quote-id <id>          quote 返回的 quote_id（单次核销令牌，报价约 10 分钟有效）
--company-code <码>      选定运力（须在本次 quote 的 quotes[].company_code 内）
--callback-url <url>     状态回调地址（可选，一般不填）
```

### cancel / add_tip
```
--reason <文本>          取消原因（cancel，可选）
--tip-fee <分>           小费金额（add_tip，须 > 0，如 200 = ¥2）
```

## 输出契约

- 成功 → JSON 打到 **stdout**，exit 0。
- 失败 → 中文错误 + `RECOVERY[CODE]: <下一步>` 打到 **stderr**，exit 1。
- 金额字段单位均为**分**。付款链接（`cashier_url`）原样发用户，禁止改写/脱敏。
- 地址簿出参只回 `contact_phone_masked`（`138****5678`）**不回明文、不回门牌**——
  下单时服务端按 address_id 自己取库里的明文与门牌，agent 全程碰不到。
  展示给用户时原样用这个脱敏串。
