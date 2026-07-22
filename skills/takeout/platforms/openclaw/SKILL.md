---
name: clawdot-takeout
description: 通过 ClawDot 外卖网关帮用户点外卖。当用户提到想吃什么、想喝什么、饿了、点外卖、叫外卖、午饭/晚饭吃什么、来杯咖啡、下午茶、夜宵等任何与饮食需求相关的表达时必须触发。即使用户只是随口提到食物或饮品名称（如"好想吃火锅"、"来杯奶茶"、"有点渴"），也要触发此技能来协助点餐。
metadata:
  openclaw:
    requires:
      bins: [python3]
      env: []
      env_optional: [GATEWAY_MCP_URL, API_KEY, CONSENT_GRANT_ID, CLAWDOT_HOME, CLAWDOT_SETUP_URL, DEFAULT_LAT, DEFAULT_LNG]
---

{{GUIDE}}

## 调用方式

所有操作通过 `takeout` tool 的 `command` 参数调用（值 = 网关 MCP tool 名；多用户模式带 `phone` 参数）：

| command | 用途 | 关键参数 |
|--------|------|----------|
| search_addresses | 列出已存地址；带 keyword 搜索新地址 | keyword?, city?, lat?, lng? |
| select_address | 保存地址（sug_ref 候选 或 address_id 已存） | sug_ref?, address_id?, contact_name, contact_phone, address_detail?, tag? |
| search_shops | 搜索附近店铺 | keyword?, lat?, lng?, city? |
| recommend | **搜店+取菜单一步到位（复合命令）** | keyword?, lat?, lng?, top_n?（默认3，最多5）|
| get_shop_menu | 查看菜单（概览→分类→商品；keyword 跨分类搜菜） | shop_id, category?, item_id?, keyword? |
| get_item_options | 批量查商品完整规格/加料（含选中标记） | shop_id, items (JSON array) |
| preview_order | 预览订单，返回 preview_id + confirmation_token | shop_id, address_id, items (JSON array), note? |
| create_order | 确认下单 | preview_id, confirmation_token |
| get_order_status | 查询订单 | order_id |
| get_user_auth_status | 查询用户授权状态（验活凭证） | phone? |
| request_user_bind | 用户绑定第 1 步：默认发短信验证码；`auth_type=h5` 返回授权链接 | phone, auth_type?（sms/h5，默认 sms） |
| verify_user_bind | 用户绑定第 2 步：短信验码 / H5 轮询授权结果，成功后写共享缓存 | phone + bind_id + code（sms）；phone + auth_type=h5 + request_id（h5） |
| revoke_user_bind | 解绑：撤销服务端授权并清本机缓存凭证（地址/订单史保留，重绑同号可恢复） | phone?（多用户必带） |

### 鉴权：只需注入 API_KEY，cg 绑定后写入共享缓存

| 情形 | 业务调用方式 | 凭证来源 |
|------|------------|---------|
| 单用户（默认） | 不传 `phone` | 共享缓存（~/.clawdot/credentials.json，按 API_KEY+手机号键控）中唯一已绑用户；也可用 `CONSENT_GRANT_ID` 环境变量预注入（只读） |
| 多用户 | 传 `phone`（11 位） | 各用户各自绑定，按手机号存共享缓存 |

唯一必需注入的是 `API_KEY`（agent 身份）。一个 api_key 可服务多个用户，**一个 cg = 一个用户**（90 天有效，到期/轮换重绑）。绑定步骤需 `phone`；绑定成功写入共享缓存后，单用户业务调用不用再带 `phone`。`API_KEY` 没配时返回 `RECOVERY[API_KEY_MISSING]`。**open-gateway 无 admin 静默绑定**——每个用户都要本人授权一次。

### 地址管理

- `search_addresses`（无参数）→ 列出已保存地址（saved）+ 历史候选；为空时报 `[需要地址]`
- `search_addresses` 带 `keyword [+ city]` → 关键词搜索（POI 必须坐标或城市，二选一）
- `select_address` 带 `sug_ref` + `contact_name` + `contact_phone` [+ `address_detail`] [+ `tag`] → 保存地址
  - POI suggestion 必须传 `address_detail`，否则 400 DETAIL_REQUIRED；返回平台 `address_id`（addr_）供 preview_order 用

### 菜单钻取与下单 item 模型

1. `get_shop_menu` + `shop_id` → 分类概览（含 item_id/价格；店铺有必选组时带 `required_groups`）
2. `get_shop_menu` + `shop_id` + `category` → 分类下所有商品
3. `get_shop_menu` + `shop_id` + `item_id` → 商品详情，含 `sku_options[]`（带 `sku_id`）、`ingredient_options[]`（带 `option_id` + `selected_by_default`）；起购/库存受限时带 `min_purchase`（>1）、`available_quantity`（0=售罄）
4. `get_shop_menu` + `shop_id` + `keyword` → 跨分类按菜名模糊搜

下单 `items` 每项形如 `{"item_id":"item_x","quantity":1,"sku_id":"sku_y","ingredient_option_ids":["opt_z"],"remark":"少冰"}`：`sku_id` 取自该商品 `sku_options[].sku_id`（不传用默认），`ingredient_option_ids` 取自 `ingredient_options[].option_id`。所有 id 来自**当前店 get_shop_menu 输出**，禁止跨店复用或把中文菜名当 item_id。

**店铺必选组**：概览带 `required_groups[]` 时（麻辣烫「必选好汤」等），整单必须从每组 `candidates` 按 `min_select` 选够，当普通商品加进 `items[]`；漏选 preview_order 报 `MISSING_REQUIRED_SELECTION`。`min_purchase` 是单品起购份数（quantity 要够，否则 `BELOW_MIN_PURCHASE`，与整单起送价不同）。

### 下单两步交接

- `preview_order` 返回 `preview_id` + `confirmation_token` + 价格明细（金额单位为分）
- `create_order` 带 `preview_id` + `confirmation_token` 提交，返回 `order_id` 和 `payment_link`
