# 命令总览（clawdot.py）

调用形式：`python3 scripts/clawdot.py <command> [--phone <11位>] [参数...]`

子命令 1:1 对应网关 MCP tool 名；`recommend` 是客户端复合命令。全局参数 `--phone`
仅多用户场景需要（单用户绑定后自动命中缓存唯一用户）。

| command | 说明 | 必填参数 |
|---|---|---|
| `request_user_bind` | 绑定第 1 步：发短信验证码 / 签发 H5 授权链接 | `--phone` |
| `verify_user_bind` | 绑定第 2 步：短信验码 / H5 轮询结果，成功写共享缓存 | `--phone`（+ sms: `--bind-id --code`；h5: `--auth-type h5 --request-id`）|
| `get_user_auth_status` | 查询用户授权状态（验活，不触发重绑） | 无 |
| `search_addresses` | 列出已存地址；带 `--keyword` 搜索新地址 | 无 |
| `select_address` | 候选/已存地址落成收货地址 | `--sug-ref` 或 `--address-id`；`--contact-name --contact-phone` |
| `search_shops` | 搜索/浏览附近店铺 | 无（首搜需坐标或 `--city`）|
| `recommend` | 复合：搜店 + 并行拉 top N 菜单 | 无 |
| `get_shop_menu` | 菜单钻取（概览/分类/单品/搜菜） | `--shop-id` |
| `get_item_options` | 批量查商品完整规格（含选中标记） | `--shop-id --items` |
| `preview_order` | 预览订单 → `preview_id` + `confirmation_token` | `--shop-id --address-id --items` |
| `create_order` | 提交订单 → `order_id` + `payment_link` | `--preview-id --confirmation-token` |
| `get_order_status` | 查询订单状态 | `--order-id` |

## 各命令可选参数

### request_user_bind / verify_user_bind
```
--auth-type sms|h5     授权方式（默认 sms）
--bind-id <id>         (sms verify) request_user_bind 返回的 bind_id
--code <6位>           (sms verify) 用户回复的短信验证码
--request-id <id>      (h5 verify) request_user_bind 返回的 request_id
```

### search_addresses
```
--keyword <str>        POI 关键词；缺省只列已存地址
--lat/--lng <float>    用户坐标（GCJ-02）
--city <str>           城市名（中文/拼音/缩写）；传了覆盖历史坐标
```

### select_address
```
--sug-ref <str>        search_addresses 返回的 suggestions[].sug_ref
--address-id <addr_…>  已存地址 id（与 --sug-ref 二选一）
--contact-name <str>   收件人姓名（必填）
--contact-phone <str>  收件人手机号（必填）
--address-detail <str> 门牌/楼层/室号（POI 候选必填）
--tag <str>            标签（≤6 字，如 家/公司），仅 --sug-ref 模式生效
```

### search_shops / recommend
```
--keyword <str>        店名/品类/商品名；缺省浏览附近店
--lat/--lng <float>    坐标（缺省用地址缓存或 DEFAULT_LAT/LNG）
--city <str>           城市名
--top-n <int>          (仅 recommend) 拉菜单店铺数，默认 3 最多 5
```

### get_shop_menu
```
--shop-id <shop_…>     必填（须先 search_shops/recommend 过这家店）
--category <str|int>   分类名/序号 → 该分类全部商品
--item-id <item_…>     单商品详情（sku_options/ingredient_options）
--keyword <str>        按菜名跨分类模糊搜
```

### get_item_options / preview_order / create_order / get_order_status
```
--items <JSON>         [{"item_id":"item_x","quantity":1,"sku_id":"sku_y",
                         "ingredient_option_ids":["opt_z"],"remark":"少冰"}]
--note <str>           (preview_order) 整单备注
--preview-id / --confirmation-token   (create_order) 均来自 preview_order 返回
--order-id <str>       (get_order_status)
```

## 输出契约

- 成功：业务 JSON → stdout，exit 0
- 失败：中文一句 + `RECOVERY[CODE]: <下一步指引>` → stderr，exit 1
- 详细错误码 → errors.md；复杂参数对象 → params.md
