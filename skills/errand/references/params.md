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

- `search_addresses --keyword "西湖文化广场" [--city "杭州"]`：按地名搜。
- `search_addresses --lat 30.29 --lng 120.096`：**只给坐标**＝按用户当前位置反查地点名
  （"从我现在的位置"），返回从具体到宽泛的几个说法让他确认。
  用户**说了地名**时别让坐标顶替 keyword，否则"送到人民广场"会变成搜他当前位置。
- 出参两份：
  - `candidates[]`：搜到的候选（`name/address/lat/lng/adcode`）。同名可能多处，
    **逐行列给用户挑、绝不自动取第一个**。
  - `saved_matches[]`：用户**已存过且命中本次搜索**的地址，结构同地址簿单条 + `match_reason`
    （keyword/same_place/both）。**门牌电话都齐，取其 `id` 直接下单，不必再问用户**。
- `save_address --address "<名+址>" --lat --lng [--contact-name --contact-phone --detail "1栋502" --tag 家]`
  → 返回 `address_id`（`plat_` 前缀）供 quote 复用。**电话可以存**（落库即密文、出参只回脱敏），
  存一次之后下单不必再报。判重口径＝**地址+门牌+联系人**三者全同才算同一条。
- `update_address --address-id plat_X`：只传要改的字段；改**位置**须 `--address --lat --lng` 齐传。
- `delete_address --address-id plat_X`：**不可撤销**，先跟用户确认是哪一条。

## 物品与增值项

- `--goods-name` 永远写用户原话——**平台不解析名称**。
- `--goods-category-code`：品类码，取自 `list_goods_categories`（15 项，前 7 项 `valuable=true`）。
  **理赔只看品类码、与物品名无关**，每单都该带；不传会退回平台默认品类。
  映射到贵重品类时要跟用户确认品类并问一句保价。
- `--goods-price`（分）：货值；`--insured` 保价按此货值口径计费，**额外收费、须用户点头**。
- `--weight`（克）：总重量，默认 1000。
- `--remark`：给骑手的留言，**最多 200 字**；与物品名一样会做**禁运校验**（命中回 `GOODS_PROHIBITED`）。
- `--person-direct`：专人直送（骑手不拼单、一次只送本单，费用更高）。
- `--scheduled-at`：预约送达时间，**只能填 `list_schedule_slots` 返回的 `value`**
  （一档 15 分钟，最早 45 分钟后、最晚明天 23:45 北京时间）；自己算的会被拒。
  不传＝即时单（**别传 0**）。复用历史单时**不要照抄**旧单的值，那个时间早过了。

## 两步下单交接（stateless）

- `quote` 返回 `{quote_id, quotes:[{company_code, company_name, fee, distance, coupon_fee,
  estimated_minutes, estimated_arrival_time}], expires_in_seconds}`。
  后两个是**预计时长（分钟）与预计送达时刻（HH:MM 北京时间）**，可如实报给用户；
  **距离未知时为 null**，是 null 就别报时效。
- 选定运力（无偏好取 `fee` 最小；用户要快则比 `estimated_minutes`），
  带其 `company_code` 与 `quote_id` 调 `create`。
- `create` 返回**整单信息**：`{order_id, status:pending_payment, quote_fee, cashier_url,
  payment_expire_at, company_name, from, to, goods, goods_category_name, insured, remark, ...}`
  ——可直接摆给用户核对。`payment_expire_at` = 最晚付款时间（自下单起 15 分钟）。
- 客户端**不缓存业务状态**：`quote_id`、`order_id` 由 stdout 显式在命令间传递。

## 鉴权与凭证

- `API_KEY`（agent 身份）唯一必需注入；`consent_grant_id`（cg_，=一个用户）由短信绑定产生，
  写【能力分格】共享缓存 `~/.clawdot/errand-credentials.json`（按 API_KEY 指纹+手机号键控）。
- 跑腿与外卖 cg **分能力、不互通**、各存各的文件；同号两条线各绑一次、天然不互踢。
- 单用户业务命令不带 `--phone`（缓存唯一用户自动命中）；多用户带 `--phone` 指定。
