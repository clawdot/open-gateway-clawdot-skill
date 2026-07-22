# 复杂参数对象

## items（get_item_options / preview_order 的 `--items`）

JSON 数组，每个元素（open-gateway CartItem 形态，多余字段会被 CLI 剔除）：

```json
[{
  "item_id": "item_x",              // 必填，来自当前店 get_shop_menu 输出
  "quantity": 1,                    // preview_order 必填 ≥1（get_item_options 不需要）
  "sku_id": "sku_y",                // 可选：规格/杯型，取自该商品 sku_options[].sku_id
  "ingredient_option_ids": ["opt_z"], // 可选：加料/属性，取自 ingredient_options[].option_id
  "remark": "少冰"                   // 可选：单品备注
}]
```

铁律：所有 id 来自**当前店当前 cart 上下文**的 get_shop_menu / get_item_options 输出；
禁止跨店复用、禁止把中文菜名当 item_id。

## 地址相关字段

- `sug_ref`：`search_addresses` 输出 `suggestions[].sug_ref`（一次性，过期报
  SUGGESTION_EXPIRED；CLI 内部转成网关的 suggestion_token）
- `address_id`：已存地址 id（`addr_…`），`saved[].address_id`
- `address_detail`：门牌/楼层/室号；POI 候选（sug_ref 模式）必填，不能传"无"/空格
- `tag`：地址标签，≤6 字（家/公司/学校 会同步到饿了么侧）

## 金额与坐标

- 所有金额字段单位为**分**（展示时换算成元）
- 坐标为 GCJ-02（高德系）；`--city` 传中文/拼音/缩写均可，传了 city 则丢弃坐标

## 凭据与缓存

- `API_KEY`（clw\_）：agent 身份，env 注入，唯一必需项
- `consent_grant_id`（cg\_）：一个用户的授权凭证，90 天有效；重复绑定同一手机号
  会**轮换作废旧值**——所以 CLI 绝不静默重绑，缓存 miss 时引导用户走绑定流程
- 共享缓存：`$CLAWDOT_HOME/credentials.json`（默认 `~/.clawdot/`），结构
  `{sha256(API_KEY)[:12]: {phone: {consent_grant_id, expires_at, updated_at}}}`，
  目录 0700 / 文件 0600
- 操作缓存（搜索/菜单/cart_id/地址，非凭据）：`~/.cache/clawdot-takeout/cache.json`；
  cart_id 按店缓存 25 分钟，过期报 SHOP_CART_MISS 引导重搜
