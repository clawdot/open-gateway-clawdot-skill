# 错误与 RECOVERY 码

失败时脚本打到 **stderr**：一行中文错误 + 一行 `RECOVERY[CODE]: <下一步>`，退出码非零。
按 `RECOVERY[CODE]` 选下一步；**不脑补**未出现的错误。

> 本表与网关 errand 面实际会抛的业务码一一对应。`tests/test_errand_cli.py` 的
> `test_error_playbook_covers_every_gateway_code` 会逐码断言**路由到正确的 RECOVERY 码**
> （不是"有标签即过"）——但它遍历的是**手写清单** `CODE_EXPECTED_RECOVERY`，
> 上游文档新增错误码时**不会自动变红**，需要跟文档时手工补进那张表。
> 表里没有的码 = 脚本原样透出网关文案，此时只跟用户说"暂时不可用"，**禁止编造原因**。

## 凭据 / 授权

| CODE | 含义 | 下一步 |
|---|---|---|
| `API_KEY_MISSING` | 没配 API_KEY（服务没配好，非用户问题） | 把注册链接发用户拿 key，写进 `.env` 的 `API_KEY=` |
| `API_KEY_INVALID` | API_KEY 无效（401/403） | 检查/更换 `.env` 里的 API_KEY（clw_） |
| `USER_NOT_BOUND_NEEDS_SMS` | 该手机号还没完成**跑腿**绑定 | `request_user_bind --phone` → 用户回码 → `verify_user_bind --phone --bind-id --code` |
| `CONSENT_INVALID` | 授权凭证无效/缺失（含 WRONG_CAP：拿了外卖 cg 调跑腿） | 带 `--phone` 且该号已绑**跑腿**；未绑先走绑定。跑腿与外卖授权独立 |
| `CONSENT_EXPIRED` | 用户授权已过期 | 引导用户重新短信绑定后重试 |
| `CAP_NOT_BOUND` | 该 agent 未开通跑腿能力（平台侧配置） | 联系 ClawDot 为该 API_KEY 开通 `errand` 能力；非用户可自助 |
| `BINDING_LIMIT_REACHED` | agent 已达可绑用户数上限（平台侧配额） | 解绑某已绑用户或提升 max_bindings 配额 |
| `MULTIPLE_USERS_NEED_PHONE` | 缓存里多个已绑用户，不知道给谁下单 | 业务命令带 `--phone` 指明是哪位 |

## 绑定过程

| CODE | 含义 | 下一步 |
|---|---|---|
| `SMS_COOLDOWN` | 验证码刚发过，冷却中（约 60 秒） | **别重发**。让用户等一下，或直接问他上一条短信里的码 |
| `SMS_CODE_INVALID` | 验证码错误或过期 | 让用户核对最新短信重报；过期就 `request_user_bind` 重发。**禁止编码重试** |

## 下单要素缺失

| CODE | 含义 | 下一步 |
|---|---|---|
| `ADDRESS_REQUIRED` | 收发某端既没地址簿 id 也没坐标 | 用 `--from-id/--to-id`，或先 `search_addresses` 让用户选再回填坐标 |
| `COORDS_REQUIRED` | 纯地址文本无坐标 | 先 `search_addresses` 选中候选拿坐标，再回填 quote |
| `ADDRESS_TEXT_REQUIRED` | 给了坐标但没给地址文本 | 传坐标时必须同时传 `--from-text/--to-text`（用候选的 name+address） |
| `ADDRESS_NOT_FOUND` | 该地址不在这个用户名下 | address_id 必须来自本手机号的 `list_addresses`；重新列一次取新 id |
| `CONTACT_REQUIRED` | 某端缺联系人/电话 | 走地址簿 id 通常会自动带出；没带出说明那条地址没存电话 → 问一次姓名+电话传 `--to-name/--to-phone` |
| `GOODS_REQUIRED` | 没说送什么 | 问一句"送什么？"，原话填 `--goods-name`，不必追问品类 |
| `ADDRESS_INCOMPLETE` | 存址时地址/坐标不全 | `save_address` 三样必给：`--address --lat --lng` |
| `KEYWORD_REQUIRED` | 搜地址没给关键词 | `search_addresses` 要么给 `--keyword`，要么给 `--lat --lng`（按位置搜） |
| `ADDRESS_COORDS_PAIRED` | 改址只改文本没改坐标 | `--address --lat --lng` **三个一起传**；先 search 拿新坐标 |
| `ADDRESS_DUPLICATE` | 改完与已有另一条完全相同 | 判重口径=地址+门牌+联系人；换个门牌/联系人，或直接用已有那条 |
| `ADDRESS_UPDATE_EMPTY` | 改址没给任何要改的字段 | 至少给一个 `--contact-name/--contact-phone/--detail/--tag` 或 `--address+--lat+--lng` |
| `ADDRESS_FIELD_TOO_LONG` | 地址/联系人/门牌/标签/电话超长 | 精简后重试 |

## 预约时间（v3.0：只收清单档位）

> 一律的解法：**重调 `list_schedule_slots` 让用户重挑一档**，绝不自己算时间戳。

| CODE | 含义 | 下一步 |
|---|---|---|
| `SCHEDULED_AT_NOT_ON_GRID` | 不在可选档位上 | 从清单取 `value` 原样回填，别自己算 |
| `SCHEDULED_AT_PAST` | 时间已过去 | **复用历史单最常撞**——旧单时间早过期，别照抄，重新问用户 |
| `SCHEDULED_AT_TOO_SOON` | 太近 | 最早 45 分钟后；错误消息给了当前最早可约时间。要马上送就别传该参数 |
| `SCHEDULED_AT_TOO_FAR` | 太远 | 最晚只能约到明天 23:45（北京时间） |
| `SCHEDULED_AT_INVALID` | 不是 13 位毫秒时间戳 | 传成秒/小数/负数都会命中；直接用清单的 `value` |

## 物品品类与禁运

| CODE | 含义 | 下一步 |
|---|---|---|
| `GOODS_PROHIBITED` | 物品名或备注命中禁运 | 毒品/枪爆/危化品/违法交易物。**如实告知用户不能寄，禁止改词重试绕过**；正常商品被误判（如「大麻花」）才换更准确名称重询 |
| `GOODS_CATEGORY_INVALID` | 品类码不在清单内 | 重拉 `list_goods_categories` 再选 |
| `REMARK_TOO_LONG` | 骑手留言超 200 字 | 精简后重试 |

## 历史单筛选

| CODE | 含义 | 下一步 |
|---|---|---|
| `ERRAND_STATUS_INVALID` | 状态筛选值不支持 | 取订单状态原值、逗号分隔；错误消息里列了全部可选值 |
| `ERRAND_TIME_RANGE_INVALID` | 时间格式不对 | 写 `2026-08-01` 或 `2026-08-01 10:30:00`（北京时间） |

## 地址服务

| CODE | 含义 | 下一步 |
|---|---|---|
| `ADDRESS_SEARCH_FAILED` | 地址搜索失败 | 换更具体关键词（带商圈/路名）重搜，或加 `--city` 缩范围 |
| `ADDRESS_LOCATE_FAILED` | 地址定不了位 | 让用户给更精确地址（门牌/楼号/校区名）重新搜选 |
| `ERRAND_LOCATE_UNAVAILABLE` | 定位服务未开通（平台侧） | 非用户问题。改让用户从 `list_addresses` 已存地址里选 |

## 报价 / 下单

| CODE | 含义 | 下一步 |
|---|---|---|
| `QUOTE_EXPIRED` | 报价过期/失效（约 10 分钟） | 用同样收发/物品重新 `quote` 拿新 `quote_id` 再 `create`（网关码 `QUOTE_INVALID_OR_EXPIRED`）|
| `CALLBACK_URL_INVALID` | 回调地址非公网（网关码 `PUBLIC_REFERENCE_INVALID`）| `--callback-url` 须公网 http(s)，localhost/内网会被拒。一般用不到这个参数，去掉重下。**v3.0 起该码不再表示报价失效** |
| `ERRAND_NO_QUOTE` | 这两点之间无运力接单 | 换地址或稍后重试一次；仍不行如实告诉用户叫不到 |
| `COMPANY_NOT_IN_QUOTE` | 选的运力不在本次报价里 | `--company-code` 必须取自**本次** quote 的 `quotes[]`；重新 quote 再选 |
| `ERRAND_FEE_CHANGED` | 配送费刚变动 | 静默重新 quote，跟用户说"我重新算了下价"，再确认 |
| `QUOTE_FEE_INVALID` | 报价金额异常 | **别硬下单**。重新 quote；仍异常告诉用户稍后再试 |
| `ERRAND_CROSS_CITY` | 收发不在同一城市 | 跑腿只做同城；跨城如实告诉用户做不了 |
| `ERRAND_CITY_NOT_OPEN` | 该城市暂未开通跑腿 | 告诉用户这个城市暂时叫不到跑腿 |
| `ERRAND_SHOP_NOT_CONFIGURED` | 该城市平台侧未配置 | 非用户问题。同上，如实说这个城市用不了 |

## 订单 / 售后

| CODE | 含义 | 下一步 |
|---|---|---|
| `ERRAND_ORDER_NOT_FOUND` | 查不到这一单 | `--order-id` 必须是 create 返回的（err_ 开头）；不确定先 `list_orders` |
| `ERRAND_NO_RIDER` | 这单还没有骑手 | 未付款/刚下单还没派单时没有骑手位置。说"骑手还没接单"，过会儿再查 |
| `ERRAND_CANCEL_NOT_ALLOWED` | 当前状态不能取消 | 仅**已完成/已取消/配送失败**会报此码。先 `get_order` 看状态再如实回答。<br>⚠️ **未支付的单不会报这个码**——`pre_cancel` 正常返回 `cancel_fee:0 / refund_amount:0`（没付过钱没款可退），直接 `cancel` 即可 |
| `ERRAND_TIP_NOT_ALLOWED` | 当前状态加不了小费 | 骑手已接单/单子终态就加不了。先 `get_order` 看状态再解释 |
| `ERRAND_TIP_INVALID` | 小费金额不对 | `--tip-fee` 单位是**分**且 > 0（¥2 传 200） |

## 支付 / 上游

| CODE | 含义 | 下一步 |
|---|---|---|
| `CASHIER_UNAVAILABLE` | 支付服务暂时不可用（平台侧） | **别重复下单**（可能已产生待付单），让用户稍后再试 |
| `PAYMENT_AMOUNT_MISMATCH` | 支付金额与订单不符，已拦截 | 资金安全拦截。**别绕过、别重试**，让用户联系客服核对 |
| `ERRAND_TEMPORARILY_UNAVAILABLE` | 跑腿服务暂时不可用（上游波动） | 稍后重试一次；连续失败告诉用户过会儿再叫 |
| `ERRAND_PROVIDER_CONFIG_ERROR` | 跑腿服务没配好（平台侧） | 非用户问题。**别重试**，告诉用户暂时用不了 |
| `ERRAND_UPSTREAM_ERROR` | 上游返回异常 | 重试一次；仍失败说"这趟暂时叫不到跑腿"，**禁止编造具体原因** |

> WRONG_CAP 专门提示：跑腿 cg 与外卖 cg **分能力发放、互不通用**，各存各的缓存文件
> （`errand-credentials.json` vs `credentials.json`）。若报 `CONSENT_INVALID` 且用户"外卖能用、
> 跑腿不行"，多半是该号**只绑了外卖没绑跑腿**——走一次跑腿绑定即可，不要拿外卖 cg 硬调。
