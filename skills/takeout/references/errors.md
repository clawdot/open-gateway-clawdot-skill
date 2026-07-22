# 错误码 → RECOVERY playbook

CLI 把网关错误（MCP 业务信封 `{"error":{code,message}}`）翻译成
「中文一句 + `RECOVERY[CODE]: 下一步指引`」写 stderr、exit 1。
**stderr 是唯一真相**：按 RECOVERY 指引走，禁止编造错误原因。

| RECOVERY 码 | 含义 | 下一步 |
|---|---|---|
| `API_KEY_MISSING` | 未配置 API_KEY | 引导用户注册拿 key 写入 .env |
| `USER_NOT_BOUND_NEEDS_SMS` | 用户未绑定 | 一句话问齐手机号+方式 → request_user_bind → verify_user_bind |
| `H5_BIND_PENDING` / `H5_BIND_EXPIRED` | H5 授权未完成/链接过期 | 提醒用户点链接 / 重新签发链接 |
| `CONSENT_EXPIRED` | 用户授权过期 | 引导重绑（request_user_bind → verify_user_bind）后重试原命令 |
| `CONSENT_INVALID` | 凭证无效/缺失 | 检查预注入 CONSENT_GRANT_ID 或走绑定流程 |
| `ADDR_MISSING` | 缺用户坐标 | 直接问用户当前位置，search_addresses --keyword |
| `POI_DETAIL_REQUIRED` | POI 候选缺门牌 | 问到门牌后 select_address 带 --address-detail 重试 |
| `CONTACT_REQUIRED` | 缺收件人 | 问齐姓名/手机后重试 select_address |
| `SUGGESTION_EXPIRED` | 地址候选过期 | search_addresses 重拿新 sug_ref |
| `SHOP_CART_MISS` | 缺店铺购物车上下文 | 先 search_shops/recommend 这家店再重试 |
| `REFERENCE_STALE` | shop/item/cart 引用失效 | 重新 search_shops → get_shop_menu 拿新 id 再 preview_order |
| `MISSING_REQUIRED_SELECTION` | **店铺级**必选组未选满 | get_shop_menu 看 required_groups[]，让用户选够加进 items[] |
| `MUST_PICK_REQUIRED` | **商品内部**必选组未选 | 看该商品 ingredient_options，让用户选 option 加进 ingredient_option_ids |
| `BELOW_MIN_PURCHASE` | 单品未达起购份数 | quantity 提到 min_purchase 以上（先跟用户说一声） |
| `BELOW_MIN_ORDER` | 整单未达起送价 | 让用户决定加什么凑单 |
| `SHOP_CLOSED` | 店铺打烊/不可下单 | recommend 推同类其他店 |
| `ITEM_SOLD_OUT` | 商品售罄 | 菜单里找替代款给用户确认 |
| `PRICE_CHANGED` | 价格变化 | 重新 preview_order 拿新价+新令牌，用户确认后 create_order |
| `CONFIRMATION_REQUIRED` | 缺确认令牌 | 先 preview_order 再 create_order |
| `IDEMPOTENCY_CONFLICT` | 确认令牌已被别组参数消费 | 重新 preview_order 拿新令牌 |
| `COUPON_ISSUE` | 优惠券不可用/过期 | 不带该券重新 preview_order |
| `OUT_OF_RANGE` | 超配送范围 | 保留地址换店推荐，禁止同店重试 |
| `ORDER_GENERIC_FAIL` | 下单/预览通用失败 | 核对商品状态与 id 后重 preview_order；多次失败换店 |
| `ELEME_USER_NOT_FOUND` | 手机号无饿了么账号 | 让用户先开通或换号 |
| `CAP_NOT_BOUND` | agent 未开通外卖能力 | 平台侧配置问题，联系 ClawDot |
| `BINDING_LIMIT_REACHED` | 绑定配额满 | 解绑旧用户或提配额 |

## 顺位红线（歧义码的精确路由，勿改顺序）

- `MISSING_REQUIRED_SELECTION`（店铺级必选组）先于 `MUST_PICK_REQUIRED`（商品内部必选）
- `BELOW_MIN_PURCHASE`（单品起购）先于 `BELOW_MIN_ORDER`（整单起送价）
- `CONSENT_GRANT_INVALID` 的 message 含 "expired" 也路由 `CONSENT_INVALID`（未绑≠过期）

## 传输层错误（非业务信封）

| 情形 | CLI 行为 |
|---|---|
| HTTP 401/403 | `API_KEY 无效或缺失`（AUTH_INVALID） |
| JSON-RPC error（如 unknown tool） | 原 code + message 直出 |
| MCP isError=true（非网关业务错） | `TOOL_ERROR` + content 文本直出 |
| 网络不通 | `请求失败：<reason>`（NETWORK） |
