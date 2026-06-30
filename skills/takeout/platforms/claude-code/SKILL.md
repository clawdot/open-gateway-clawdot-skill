---
name: clawdot-takeout
description: 通过 ClawDot 外卖网关帮用户点外卖。当用户提到想吃什么、想喝什么、饿了、点外卖、叫外卖、午饭/晚饭吃什么、来杯咖啡、下午茶、夜宵等任何与饮食需求相关的表达时必须触发。即使用户只是随口提到食物或饮品名称（如"好想吃火锅"、"来杯奶茶"、"有点渴"），也要触发此技能来协助点餐。
metadata:
  requires:
    bins: [python3]
    env: []
    env_optional: [GATEWAY_URL, API_KEY, CONSENT_GRANT_ID, REDIS_URL, CLAWDOT_SETUP_URL, DEFAULT_LAT, DEFAULT_LNG]
---

{{GUIDE}}

## 调用方式

所有操作通过 `python3 {baseDir}/scripts/takeout.py [--phone <手机号>] --action <action>` 调用：

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
| 单用户（默认） | 不传 `--phone` | `.env` 的 `CONSENT_GRANT_ID`——用户走一次 SMS/H5 绑定后由 `verify_code` **自动回写**；也可手动预注入长效 cg |
| 多用户 | 传 `--phone <11 位>` | 各用户各自绑定，按手机号缓存 cg（可选 `REDIS_URL` 跨进程共享） |

唯一必需注入的是 `API_KEY`（agent 身份）。模型：一个 api_key 可服务多个用户，**一个 cg = 一个用户**（90 天有效，到期/轮换要重绑）。绑定步骤（request_code/verify_code）需 `--phone`；绑定成功 cg 回写 env 后，**单用户业务调用不用再带 --phone**。`<user_identity>` 的 `<phone>` 字段就是绑定时 `--phone` 的值。`API_KEY` 没配时脚本返回 `RECOVERY[API_KEY_MISSING]`，按指引引导用户去注册页拿 key 并写入 `.env`。**open-gateway 无 admin 静默绑定**——每个用户都必须本人走一次 SMS/H5 授权。

### 地址管理

- 无参数 → 列出已保存地址（saved）+ 历史候选（suggestions）；为空时报 `[需要地址]`
- `--address-keyword "浦东" [--city "上海"]` → 关键词搜索（POI 必须坐标或城市，二选一）
- `--select-token sug_xxx --contact-name 张三 --contact-phone 138xxx [--address-detail "1栋502"] [--address-tag home]` → 保存地址
  - POI suggestion 必须传 `--address-detail`（门牌/楼层），否则 400 DETAIL_REQUIRED
  - 保存返回平台 `address_id`（addr_…），preview 用它指定配送地址

### 菜单钻取与下单 item 模型

1. `--shop-id shop_xxx` → 分类概览（各分类 + 热门商品 item_id/价格）
2. `--shop-id shop_xxx --category "热饮"` → 分类下所有商品
3. `--shop-id shop_xxx --item-id item_xxx` → 商品详情，含 `sku_options[]`（规格，带 `sku_id`）、`ingredient_options[]`（加料/属性，带 `option_id` + `selected_by_default`）
4. `--shop-id shop_xxx --shop-keyword "苕皮"` → 跨分类按菜名模糊搜

下单 `--items` 为 JSON 数组，每项形如：
```json
{"item_id":"item_xxx","quantity":1,"sku_id":"sku_xxx","ingredient_option_ids":["opt_xxx"],"remark":"少冰"}
```
`sku_id` 从该商品 `sku_options[].sku_id` 取（不传用默认规格）；`ingredient_option_ids` 从 `ingredient_options[].option_id` 取。所有 id 都来自**当前店的 menu 输出**，禁止跨店复用、禁止把中文菜名当 item_id 传。

### 下单两步交接

- `preview` 返回 `preview_id`（prv_）+ `confirmation_token`（cf_）+ 价格明细
- `order --preview-id <prv_> --confirmation-token <cf_>` 提交，返回 `order_id` 和 `payment_link`（付款链接）
- 金额字段单位均为**分**

### 输出格式

- 成功：JSON 输出到 stdout
- 失败：中文错误 + `RECOVERY[CODE]: <下一步>` 输出到 stderr，非零退出码
- 按 stderr 的 RECOVERY 提示选下一个动作；shop_id/item_id 失效报 `REFERENCE_STALE` 时重新 search→menu 拿新 id

### 执行铁律

- **默认能服务**：只要 skill 已加载、能调脚本，就当作可点单推进；禁止凭空判定"无法服务"而拒答。仅当脚本实际返回 RECOVERY/非零退出码时才按其指引处理。
- **不谎报成功**：任何成功结论必须以脚本 stdout 实际返回为准；脚本报错或未确认修改即如实告知失败，禁止编造"已完成"。
- **地址先确认后推进**：用户有多个地址或地址来源不明时，先报候选让用户确认再展示菜单/下单；展示给用户的地址只用人类可读文本，禁止暴露内部标识符。
- **追问要带选项**：需用户补全信息（称呼、口味、规格等）时，主动给 2~3 个具体建议或默认选项，不要只甩问题让用户空想。

### 失败兜底

- 工具失败、返回 null/空、或超出能力时，必须：①一句话说清楚发生了什么（卡在哪、为何拿不到结果），②紧扣用户**原始需求**给出可操作的下一步（换关键词/换店/重试/改地址等），由你主动推进，禁止甩锅让用户自己想办法。
- 同一问题重试时换新说法或新路径，不机械复读上一句；真超出能力也要诚实说明并给最接近的替代方案。

### 面向用户的措辞

- 给用户的回复一律用**用户视角说人话**：讲「你做了什么、我帮你找了什么、下一步是什么」，不要复述内部处理流程。例：「你刚点了 Manner，我找了 4 家店，你还没选送哪～」。
- 用户文本里**禁止出现内部术语**：`saved`、任何字段名、skill 名、"后台任务"、"调 X 工具"、`cart_id`/`preview_id` 等内部 id——这些只用于内部，不外露。
