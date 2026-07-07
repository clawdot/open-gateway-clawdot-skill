---
name: clawdot-takeout
description: 通过 ClawDot 外卖网关帮用户点外卖。当用户提到想吃什么、想喝什么、饿了、点外卖、叫外卖、午饭/晚饭吃什么、来杯咖啡、下午茶、夜宵等任何与饮食需求相关的表达时必须触发。即使用户只是随口提到食物或饮品名称（如"好想吃火锅"、"来杯奶茶"、"有点渴"），也要触发此技能来协助点餐。
---

{{GUIDE}}

## 调用方式

所有操作通过 `python3 scripts/takeout.py [--phone <手机号>] --action <action>` 调用：

| action | 用途 | 关键参数 |
|--------|------|----------|
| addresses | 查询/搜索/新建地址 | --address-keyword?, --city?, --select-token?, --contact-name?, --contact-phone?, --address-detail?, --address-tag? |
| search | 搜索附近店铺 | --shop-keyword?, --lat?, --lng? |
| recommend | **搜店+取菜单一步到位** | --shop-keyword?, --lat?, --lng?, --top-n?（默认3，最多5）|
| menu | 查看菜单（概览→分类→商品；--shop-keyword 跨分类搜菜） | --shop-id, --category?, --item-id?, --shop-keyword? |
| preview | 预览订单，返回 preview_id + confirmation_token | --shop-id, --address-id, --items (JSON array), --note? |
| order | 确认下单 | --preview-id, --confirmation-token |
| order_status | 查询订单 | --order-id |
| request_code | 用户绑定第 1 步：默认发短信验证码；`--auth-type h5` 返回授权链接 | --phone, --auth-type?（sms/h5，默认 sms） |
| verify_code | 用户绑定第 2 步：短信验码 / H5 轮询授权结果，成功后缓存 consent_grant | --phone --bind-id --code（sms）；--phone --auth-type h5 --request-id（h5） |

### 鉴权：只需注入 API_KEY，cg 绑定后自动回写 .env

| 情形 | 业务调用方式 | 凭证来源 |
|------|------------|---------|
| 单用户（默认） | 不传 `--phone` | `.env` 的 `CONSENT_GRANT_ID`——用户走一次 SMS/H5 绑定后由 `verify_code` 自动回写；也可手动预注入长效 cg |
| 多用户 | 传 `--phone <11 位>` | 各用户各自绑定，按手机号缓存 cg（可选 `REDIS_URL`） |

唯一必需注入的是 `API_KEY`（agent 身份）。一个 api_key 可服务多个用户，**一个 cg = 一个用户**（90 天有效，到期/轮换重绑）。绑定步骤需 `--phone`；绑定成功 cg 回写 env 后，单用户业务调用不用再带 `--phone`。`API_KEY` 没配时返回 `RECOVERY[API_KEY_MISSING]`。**open-gateway 无 admin 静默绑定**——每个用户都要本人授权一次。

### 地址管理

- 无参数 → 列出已保存地址（saved）+ 历史候选；为空时报 `[需要地址]`
- 带 `--address-keyword [--city ...]` → 关键词搜索（POI 必须坐标或城市，二选一）
- `--select-token sug_xxx --contact-name 张三 --contact-phone 138xxx [--address-detail "1栋502"] [--address-tag home]` → 保存地址
  - POI suggestion 必须传 `--address-detail`，否则 400 DETAIL_REQUIRED；返回平台 `address_id`（addr_）供 preview 用

### 菜单钻取与下单 item 模型

1. `--shop-id shop_xxx` → 分类概览（含 item_id/价格；店铺有必选组时带 `required_groups`）
2. `--shop-id shop_xxx --category "热饮"` → 分类下所有商品
3. `--shop-id shop_xxx --item-id item_xxx` → 商品详情，含 `sku_options[]`（带 `sku_id`）、`ingredient_options[]`（带 `option_id` + `selected_by_default`）；起购/库存受限时带 `min_purchase`（>1）、`available_quantity`（0=售罄）
4. `--shop-id shop_xxx --shop-keyword "苕皮"` → 跨分类按菜名模糊搜

下单 `--items` 每项形如 `{"item_id":"item_x","quantity":1,"sku_id":"sku_y","ingredient_option_ids":["opt_z"],"remark":"少冰"}`：`sku_id` 取自该商品 `sku_options[].sku_id`（不传用默认），`ingredient_option_ids` 取自 `ingredient_options[].option_id`。所有 id 来自当前店 menu 输出，禁止跨店复用或把中文菜名当 item_id。

**店铺必选组**：概览带 `required_groups[]` 时（麻辣烫「必选好汤」等），整单必须从每组 `candidates` 按 `min_select` 选够，当普通商品加进 `--items`；漏选 preview 报 `MISSING_REQUIRED_SELECTION`。`min_purchase` 是单品起购份数（quantity 要够，否则 `BELOW_MIN_PURCHASE`，与整单起送价不同）。

### 下单两步交接

- `preview` 返回 `preview_id` + `confirmation_token` + 价格明细（金额单位为分）
- `order --preview-id <prv_> --confirmation-token <cf_>` 提交，返回 `order_id` 和 `payment_link`

### 环境变量

| 变量 | 必须？ | 用途 |
|------|--------|------|
| GATEWAY_URL | ✅ | ClawDot open-gateway API 地址 |
| API_KEY | ✅ | Gateway API 密钥（clw_，agent 身份；唯一必需注入项） |
| CONSENT_GRANT_ID | 留空 | 用户授权凭证（cg_）；用户绑定后由 verify_code 自动回写，也可手动预注入 |
| REDIS_URL | 可选 | 跨进程共享按手机号缓存的 consent_grant（多用户） |
| DEFAULT_LAT/LNG | 可选 | 冷启动兜底坐标 |

### 输出格式

- 成功：JSON 输出到 stdout
- 失败：中文错误 + `RECOVERY[CODE]: <下一步>` 输出到 stderr，非零退出码
