# 参数语义

## 收发两端（quote 的 from / to）

每一端二选一给地址：

1. **地址簿 id**：`--from-id <id>` / `--to-id <id>`，id 来自 `list_addresses` 或 `save_address`
   的返回（`plat_` 前缀）。坐标现成、最省事。
2. **POI 坐标**：`--from-text/-lat/-lng`（收件端 `--to-*`），来自 `search_addresses` 候选里用户
   **选中**的那条的 `name`+`address` 与 `lat`/`lng`。坐标系 **GCJ-02**。

> 纯地址文本（无坐标、无 id）不支持——会返回 `RECOVERY[COORDS_REQUIRED]`。先搜再选。

联系人：`--from-name/--from-phone`、`--to-name/--to-phone`；缺省联系人为"发件人/收件人"、
缺省电话为用户手机号（`--phone`）。收件人是别人时应显式带 `--to-name --to-phone`。

## 地址搜索与存址

- `search_addresses --keyword "西湖文化广场" [--city "杭州"]` → `{candidates:[{name,address,lat,lng,adcode}]}`；
  同名可能多处，**逐行列给用户挑、绝不自动取第一个**。
- `save_address --address "<名+址>" --lat --lng [--contact-name --detail "1栋502" --tag 家]`
  → 存名称+地址+坐标（**不存电话**），返回 `address_id`（`plat_`）供 quote 复用。

## 物品与增值项

- 品类只用于**客户端默认重量/备注**（见 GUIDE Step 3 表），`--goods-name` 永远写用户原话。
- `--goods-price`（分）：货值；`--insured` 保价按此货值口径计费。
- `--person-direct`：专人直送（骑手不拼单、一次只送本单，费用更高）。
- `--scheduled-at`：预约送达毫秒时间戳；不传=即时单。

## 两步下单交接（stateless）

- `quote` 返回 `{quote_id, quotes:[{company_code, company_name, fee, distance, coupon_fee}], expires_in_seconds}`。
- 选定运力（无偏好取 `fee` 最小），带其 `company_code` 与 `quote_id` 调 `create`。
- `create` 返回 `{order_id, status:pending_payment, quote_fee, cashier_url}`。
- 客户端**不缓存业务状态**：`quote_id`、`order_id` 由 stdout 显式在命令间传递。

## 鉴权与凭证

- `API_KEY`（agent 身份）唯一必需注入；`consent_grant_id`（cg_，=一个用户）由短信绑定产生，
  写【能力分格】共享缓存 `~/.clawdot/errand-credentials.json`（按 API_KEY 指纹+手机号键控）。
- 跑腿与外卖 cg **分能力、不互通**、各存各的文件；同号两条线各绑一次、天然不互踢。
- 单用户业务命令不带 `--phone`（缓存唯一用户自动命中）；多用户带 `--phone` 指定。
