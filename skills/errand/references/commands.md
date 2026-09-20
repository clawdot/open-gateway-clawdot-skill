# 命令总览（errand.py）

调用形式：`python3 scripts/errand.py <command> [--phone <11位>] [参数...]`

子命令 1:1 对应网关 MCP tool 名（内部映射到 `errand_*`，共 **19** 个，与《跑腿MCP接口说明文档
v3.0》一致）。全局参数 `--phone` 仅多用户场景需要（单用户绑定后自动命中缓存唯一用户）。
绑定类命令（request/verify）必带 `--phone`。

| command | 说明 | 必填参数 |
|---|---|---|
| `request_user_bind` | 绑定第 1 步：发短信验证码 | `--phone` |
| `verify_user_bind` | 绑定第 2 步：核验码，成功写共享缓存 | `--phone --bind-id --code` |
| `auth_status` | 查授权是否仍有效（失效不报错，看 `bound`） | 无 |
| `revoke_user_bind` | 解绑；同时作废本地缓存的 cg | 无 |
| `list_addresses` | 列该用户跑腿地址簿（id 恒 `plat_` 前缀） | 无 |
| `search_addresses` | 搜地点 → `candidates` + `saved_matches` | `--keyword` 或 `--lat --lng` |
| `save_address` | 把候选地址（可带门牌/电话）存进地址簿 | `--address --lat --lng` |
| `update_address` | 改地址簿某条（只传要改的字段） | `--address-id` |
| `delete_address` | 删地址簿某条（**不可撤销**） | `--address-id` |
| `list_goods_categories` | 物品品类清单（15 项，前 7 项贵重） | 无 |
| `list_schedule_slots` | 可预约送达档位（一档 15 分钟） | 无 |
| `list_orders` | 历史单 → `{orders, next_offset}` | 无 |
| `quote` | 询价 → `quote_id` + 多运力报价（含时效） | `--goods-name` + 收发两端各一（id 或 text+坐标）|
| `create` | 核销 quote_id 下单 → 付款链接 + 整单信息 | `--quote-id --company-code` |
| `get_order` | 订单详情/时间线/骑手/照片 | `--order-id` |
| `get_rider` | 骑手实时位置 | `--order-id` |
| `pre_cancel` | 取消前查违约金/可退金额 | `--order-id` |
| `cancel` | 取消订单 | `--order-id` |
| `add_tip` | 加小费（独立付款链接） | `--order-id --tip-fee` |

## 各命令可选参数

### request_user_bind
```
--external-user-id <id>   客户侧用户唯一标识（可选），注入收银台联登/支付 open_id
```

### revoke_user_bind
```
--reset-history           连地址簿与历史订单一并清退，**不可逆**（换人用这台设备/这个号才传）；
                          默认不传＝只解绑，重新绑定后地址簿与历史单都还在
```

### search_addresses
```
--keyword <地名>          按地名搜，如 "西湖文化广场"
--city <城市>             缩小范围（同名多处时）
--lat / --lng             用户当前位置坐标；**只给坐标**＝按位置反查地点名（"从我现在的位置"）
                          与 keyword 同时给则用于就近排序；用户说了地名时别让坐标顶替 keyword
```
出参两份：`candidates`（搜到的候选）+ `saved_matches`（用户**已存过且命中本次搜索**的地址，
门牌电话都齐，取其 `id` 当 `--from-id/--to-id` 可直接下单，不必再问）。

### save_address
```
--address <文本>          地址（search_addresses 候选的 name+address）
--lat / --lng             坐标（GCJ-02，取自候选）
--contact-name <名>       联系人（可选）
--contact-phone <号>      联系电话（可选）；存了之后拿这个地址下单**不必再报手机号**。
                          落库即密文，出参只回脱敏 138****5678
--detail <门牌>           门牌/楼层；下单自动拼进地址，**查地址簿时原样返回**
--tag <标签>              如 家/公司
```
判重口径：**地址 + 门牌 + 联系人**三者都相同才算同一条（已存在则返回原地址、不重复建）；
同一地点填不同门牌或不同联系人会各存一条。

### update_address
```
--address-id <plat_X>     要改哪条（必填）
--contact-name / --contact-phone / --detail     改联系人 / 电话 / 门牌
--address + --lat + --lng                       改**位置**：三个必须一起传
                                                （只改文本不改坐标，骑手会按旧坐标去旧地方）
--tag <标签>              新标签；传空串 '' 可**清空**标签
```
没传的字段保持原样；一个字段都不传 → `ADDRESS_UPDATE_EMPTY`。
改成与自己另一条完全相同 → `ADDRESS_DUPLICATE`。

### list_orders
```
--limit <n>               条数，默认 5、上限 20（按下单时间倒序）
--offset <n>              翻页起点：把上次返回的 next_offset **原样**传回
--status <s1,s2>          按状态筛，逗号分隔多传，如 dispatching,delivering；传错值会报错
--created-after <t>       只看该时间之后的单，写 2026-08-01 或 '2026-08-01 10:30:00'
--created-before <t>      只看该时间之前的单；只写日期时含当天全天
```
> 时间**按北京时间**理解，不必自己换算时区。出参为 `{orders, next_offset}`，
> `next_offset` 为 null 表示没有更多了。

### quote
```
每端二选一：
  --from-id <id> / --to-id <id>            地址簿地址 id（plat_ 前缀）
  --from-text/-lat/-lng、--to-text/-lat/-lng  搜索选中的名称+坐标（GCJ-02）
--from-name / --from-phone                发件联系人/电话（可省，见下）
--to-name / --to-phone                    收件联系人/电话（可省，见下）
    走 --from-id/--to-id 时：地址簿存了联系人/电话就**自动带上**，不必传、也不必问用户；
    传了则以传的为准。坐标形态（--*-text/-lat/-lng）没有地址簿可兜底，两者必给，
    否则 RECOVERY[CONTACT_REQUIRED]。
--goods-name <名>                         物品名（写用户原话；平台**不解析**名称）
--goods-category-code <码>                物品品类码，取自 list_goods_categories。
                                          **理赔只看品类、不看物品名**；不传会退回平台默认品类
--goods-price <分>                        货值（分）；保价按此口径
--weight <克>                             总重量，默认 1000
--remark <留言>                           给骑手的留言，**最多 200 字**（随单送达，查单时原样返回）
--scheduled-at <value>                    预约送达时间：**只能填 list_schedule_slots 给出的 value**，
                                          自己算的会被拒；不传=即时单（**别传 0**）
--person-direct                           专人直送（不拼单，费用更高）
--insured                                 保价（额外收费，**须用户点头**，绝不代开）
```
出参每家运力带 `estimated_minutes`（预计时长/分钟）与 `estimated_arrival_time`（预计送达 HH:MM，
北京时间）——**距离未知时为 null**，是 null 就别报时效。
物品名与 remark 都会做**禁运校验**，命中回 `GOODS_PROHIBITED`。

### create
```
--quote-id <id>          quote 返回的 quote_id（单次核销令牌，报价约 10 分钟有效）
--company-code <码>      选定运力（须在本次 quote 的 quotes[].company_code 内）
--callback-url <url>     状态回调地址（可选，一般不填）；须公网 http(s)，localhost/内网会被拒
```
出参是**整单信息**（可直接摆给用户核对）：除 `order_id / cashier_url / quote_fee` 外，
还有 `payment_expire_at`（最晚付款时间，自下单起 15 分钟）、`company_name`、`from`/`to`
（含脱敏电话）、`goods`、`goods_category_name`、`person_direct`、`insured`、`remark` 等。

### cancel / add_tip
```
--reason <文本>          取消原因（cancel，可选）
--tip-fee <分>           小费金额（add_tip，须 > 0，如 200 = ¥2）
```

## 输出契约

- 成功 → JSON 打到 **stdout**，exit 0。
- 失败 → 中文错误 + `RECOVERY[CODE]: <下一步>` 打到 **stderr**，exit 1。
  （网关侧业务失败时 HTTP 恒 200、`isError` 恒 false，真正的判据是 `content` 里有没有
  `error` 字段——脚本已按此判定并翻成 RECOVERY，调用方照 stderr 处理即可。）
- 金额字段单位均为**分**。付款链接（`cashier_url`）原样发用户，禁止改写/脱敏。
- 地址簿与订单出参只回 `contact_phone_masked`（`138****5678`）**不回明文电话**；
  下单时服务端按 address_id 自己取库里的明文，agent 全程碰不到明文。
  **门牌（detail）会原样返回**——调用方要能把原值念给用户核对才改得对。
- 未支付订单的 `get_order` 会带回 `cashier_url` 与 `payment_expire_at`（已支付/已关闭为 null），
  用户丢了付款链接可据此原样重发，不必重新下单。
