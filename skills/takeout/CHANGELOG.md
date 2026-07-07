# Changelog

## [1.1.0] - 2026-07-08 — 对齐 API 文档 v1.8（菜单增量字段）

对照官方《API接口说明文档 v1.8》做增量对齐——只补**落在既有下单主链路（menu→preview→order）**、
不补会让既有流程报错或缺信息的三处。接口契约面（auth 头 / path / items 模型 / 下单交接）v1.8 未改，
仍与 [1.0.0] 一致。详见仓库根 `DECISIONS.md` D10 / G8。

### Added

- **店铺级必选组 `required_groups`（文档 v1.6）**：`menu` 概览透出 `required_groups[]`
  （`name` / `min_select` / `candidates`）+ 提示。麻辣烫「必选好汤」等店，整单须从每组候选选够才能下单；
  漏选时新错误码 `MISSING_REQUIRED_SELECTION` → 定向 `RECOVERY[MISSING_REQUIRED_SELECTION]`
  （与**商品内部**加料必选组 `MUST_PICK_REQUIRED` 明确区分，playbook 顺序保证不串味）。
- **单品起购份数 `min_purchase`（文档 v1.7）**：`menu --item-id` 商品详情透出（>1 时）+ 提示；
  新错误码 `BELOW_MIN_PURCHASE` → 定向 `RECOVERY[BELOW_MIN_PURCHASE]`（与整单「未达起送价」`BELOW_MIN_ORDER` 区分）。
- **库存余量 `available_quantity`（文档 v1.8）**：商品详情透出（0=售罄 / 正整数=余量 / null 省略）。

### 未纳入（新增可选能力，保持与旧 skill 功能等价、不主动引入）

- `get_item_options`（菜单已内联全量 sku/加料、且本 skill 缓存全量菜单，冗余）、`external_user_id`
  （本 skill 一 cg 一手机号，无需联登标识）、`quote_cart` + `blocking_code`（不接 quote，preview 自带算价）。

## [1.0.0] - 2026-06-30 — 迁移到 open-gateway

把 skill 从旧 **clawdot-gateway**（user_token 体系）整体迁到 **open-gateway**（consent_grant / public v1
体系），**功能等价、接口不同**。详见仓库根 `DECISIONS.md`。

### Changed（接口面）

- **鉴权**：`X-User-Token` → `X-Consent-Grant-Id`。`Authorization: Bearer <API_KEY>` 不变。
- **绑定**：`/api/v1/user/bind/{request,verify}` → `/api/v1/auth/bind/{request,verify}`；verify 返回
  `consent_grant_id`（cg_）而非 user_token。`request_code`/`verify_code` 两步流程与出参语义保持兼容。
- **搜店**：`GET /shops/search` → `POST /shops/search`，响应新增 **`cart_id`**（每店一个），skill 按 shop_id
  缓存，menu/preview 内部取回贯穿（不对外暴露 cart_id）。
- **菜单**：`GET /shops/{id}` + `/items/{id}` → `POST /shops/menu`（shop_id + cart_id）。商品详情内联，
  不再有独立单品接口；item 详情含 `sku_options[]`（带 public `sku_id`）与 `ingredient_options[]`（带 public `option_id`）。
- **地址**：`/addresses/{search,select}` 同名；字段改名 `saved→saved_addresses`、suggestion 出参 `token`、
  select 入参 `suggestion_token`、`detail→address_detail`。`--address-tag` 经 select 后顺带 update 设置。
- **下单**：`POST /orders/preview`（一步）+ `POST /orders`（session_id）→ `POST /orders/preview`
  （返回 `preview_id` + `confirmation_token`）+ `POST /orders/create`（消费这两者）。
  - **CLI 变更**：`order --session-id` → `order --preview-id <prv_> --confirmation-token <cf_>`。
  - 付款链接由 create 出参 `payment_action.action_url` 透出为 `payment_link`。
- **金额单位统一为分**。

### Removed

- **agent / trustedBind 模式**：open-gateway 移除了 admin 静默绑定，无等价物。去掉 `ADMIN_SECRET`、
  `USER_TOKEN` 两个 env 与 trustedBind 路径。多用户场景改为「每用户走一次 SMS/H5 授权拿各自 cg」。
- 旧 preview 的 item 模糊匹配 / `needs_clarification` / `required_categories` 内联块：新网关用 public id
  直传，失效 id 走 `RECOVERY[REFERENCE_STALE]` 重搜恢复，不再在 skill 侧按中文名模糊匹配。

### Added

- `CONSENT_GRANT_ID` env（取代 `USER_TOKEN`）。**唯一必需注入的是 `API_KEY`**；cg 由用户走一次
  SMS/H5 绑定产生，`verify_code` 成功后**自动回写 `CONSENT_GRANT_ID` 到 `.env`**（upsert 保留其它键、
  chmod 0600、不回显），之后单用户业务调用无需 `--phone`。到期/轮换重绑覆盖回写。
- 不带 `--phone` 的 consent 解析优先级：env `CONSENT_GRANT_ID` → 缓存中唯一已绑用户 → 多个则要求 `--phone`。
  `--phone` + per-phone 缓存仍支持「一个 api_key 服务多用户」。
- 错误 playbook 适配新网关错误码：`PUBLIC_REFERENCE_INVALID`→`REFERENCE_STALE`、`SHOP_CART_MISS`、
  `CONSENT_GRANT_{INVALID,EXPIRED,WRONG_CAP}`、`ELEME_USER_NOT_FOUND`、`CAP_NOT_BOUND` 等，保留
  起送/打烊/售罄/超范围/必选项等业务语义。
- 仓库根 `DECISIONS.md`（决策账本）+ `verify.sh`（编译 / argparse / 接口契约 / 流程贯穿 / 负向红线硬 gate）
  + `tests/test_takeout_gateway.py`（monkeypatch 出站请求的契约与流程测试，无 pytest 依赖）。

### 保留不变（功能等价）

- 9 个 action 名不变；成功 JSON→stdout、失败「中文 + `RECOVERY[CODE]`」→stderr 的约定不变；
  文件缓存 + 可选 Redis（极简裸 socket）保留（缓存内容从 user_token 改为 consent_grant）；无第三方依赖。

---

> 以下为迁移前 clawdot-gateway 时期的历史记录。

## Unreleased（clawdot-gateway 历史）

### Changed

- `menu --shop-id`、`menu --category`、`menu --shop-keyword` 改为请求 gateway 轻量菜单（`specs=none`），避免浏览菜单时拉全店商品规格。
- `menu --item-id` 改为调用 gateway 单品详情接口，只在用户具体查看商品时拉该商品的规格、属性、加料和默认配料。
- 菜单列表/搜索输出增加 `details_deferred` 提示，避免 agent 把轻量菜单里的空规格误判成商品无规格。
- `order` 不再向 gateway 发送付款渠道；`--channel` 保留为兼容旧调用的废弃参数并被忽略，避免 skill 误拿到只能在微信内打开的付款桥页面。

## [0.5.0] - 2026-06-10

### Added

- **H5 链接授权绑定**：基于 gateway `POST /api/v1/user/bind/request` 新增的 `auth_type` 参数，用户绑定现在支持两种方式，由用户选择（不选默认短信）：
  - 短信验证码（默认，原流程不变）：`request_code` → 用户回 6 位码 → `verify_code --bind-id --code`
  - H5 链接授权（新）：`request_code --auth-type h5` 返回 `{request_id, h5_url, expires_in:300}` → 把 `h5_url` 原样发给用户点开授权 → 用户确认完成后 `verify_code --auth-type h5 --request-id <id>` 轮询结果；`bound:false` 时按 `status`（pending/expired）给出 `RECOVERY[H5_BIND_PENDING]` / `RECOVERY[H5_BIND_EXPIRED]` 指引，禁止高频轮询
  - 新增 CLI 参数：`--auth-type sms|h5`（默认 `sms`）、`--request-id`
  - 两种方式验证成功后 user_token 缓存路径完全一致（file/Redis，按手机号分桶）
- **ERROR_PLAYBOOK 补 `USER_NOT_BOUND_NEEDS_SMS` 条目**：修复该 RECOVERY 行此前从未实际输出的问题（die_with_hint 按 code 查表查不到就静默省略）；现在未绑定报错会带完整双流程指引
- **新装引导流程**：
  - `API_KEY` 缺失 → `RECOVERY[API_KEY_MISSING]`：引导用户去 `CLAWDOT_SETUP_URL`（新增可选 env，默认 ClawDot developer 登录页）拿 key，agent 写入 `.env` 后继续
  - 有 `API_KEY` 没 `USER_TOKEN`/`ADMIN_SECRET` → 不管带不带 `--phone`，业务调用都触发用户绑定流程；**手机号和绑定方式（H5/短信）合成一句问**（"先告诉我手机号，顺便选一下用 H5 还是验证码方式绑定哦～"），用户不选默认短信
- GUIDE.md 新增 h5_url 铁律（原样转发，禁止改写/脱敏/缩短）、request_id 来源铁律

### Compatibility

- 默认行为不变：不传 `--auth-type` 即原 SMS 流程，入参出参完全兼容
- H5 模式要求 gateway 已部署 `auth_type` 支持（bind/request + bind/verify 轮询语义）

## [0.4.0] - 2026-05-10

### Added

- **SMS 模式**：第三方集成（无 `ADMIN_SECRET`）现在可以走短信验证码完成 user 绑定，不再被迫去 portal 网页操作。
  - 新增 action `request_code --phone <11位>`：调 `POST /api/v1/user/bind/request` 发短信，返回 `bind_id` 给 LLM 记住
  - 新增 action `verify_code --phone <11位> --bind-id <id> --code <6位>`：调 `POST /api/v1/user/bind/verify` 验码，验通过后 user_token 自动写进 file/Redis cache，按手机号分桶
  - 新增 CLI 参数：`--code` / `--bind-id`
  - `resolve_token` 增加分支：cache miss 且无 `ADMIN_SECRET` 时 `die_with_hint("USER_NOT_BOUND_NEEDS_SMS")` 引导 LLM 走 SMS 流程
- **错误处理铁律**（SKILL.md / GUIDE.md 顶部）：禁止编造 SSL/网络错误、HTTP 错误码、API key 失效；禁止编 bind_id；禁止用 123456 当占位验证码。配套反面教材表（4 个真实 LLM 幻觉案例）。

### Compatibility

- 旧 `.env` 配 `USER_TOKEN`（personal）或 `ADMIN_SECRET`（agent）继续直接用，无变化
- SMS 模式只在两者都没配时触发，**不影响**已有部署

## [0.3.0] - 2026-05-10

### Added

- **Agent 模式**：CLI 新增 `--phone <11 位手机号>` 参数。脚本内部 `resolve_token(phone)` 走 Redis → 文件缓存 → `trustedBind`（`POST /api/v1/user/bind/trusted`，带 `X-Admin-Secret`），自动完成 agent + 手机号绑定，token 按手机号分桶缓存 1 小时。
  - 不传 `--phone` 退化到原有 personal 模式（`USER_TOKEN` 环境变量），向后兼容
  - 新增 env：`ADMIN_SECRET`（agent 必须）、`REDIS_URL`（可选，跨进程共享 token）
  - `normalize_phone_for_trusted_bind` 自动剥掉 `+86` 前缀
  - 内置极简 `RedisTokenCache`（裸 socket，无 redis-py 依赖）
- **`addresses --city`**：城市参数支持（中文/拼音/缩写）；传了就覆盖历史坐标走 cityId 搜索，解决冷启动 + 跨城场景搜不到 POI 的问题
- **`order --channel`**：按 bot 渠道分发付款链路。`wechat` 走桥页面 URL（拉淘宝闪购小程序原生支付，避开微信封锁）；其他渠道走饿了么 H5 收银台。该参数已在 Unreleased 中废弃并被 skill 忽略
- **`menu --shop-keyword <菜名>`**：跨分类菜品模糊搜（复用 `--shop-keyword` dest，避免增加新参数）
- **结构化错误 playbook**（`ERROR_PLAYBOOK`）：stderr 现在输出"用户向翻译 + `RECOVERY[CODE]: <下一步具体调用>`"两行格式，覆盖 16 类常见错误（缺地址/起送/打烊/售罄/POI 无门牌/凑单未点等），让 LLM 一轮推理选好下一个 tool call
- **preview 模糊菜名自动恢复**：LLM 把中文菜名当 item_id 传时，脚本按名字模糊匹配；唯一命中静默 recovery，多候选则把 `needs_clarification` JSON 块附在 stderr 里，**不需要再 menu 一次**
- **MUST_PICK_REQUIRED 嵌入候选**：preview 触发"必选项未点"时，把 `required_categories`（带 item_id）一并嵌进 stderr，LLM 直接读这个块给用户选项即可
- **suggestions 字段 `token` → `sug_ref` 重命名**：避免被某些 agent 平台的密钥屏蔽器按关键字打码

### Changed

- `auth_model: personal` → `auth_model: personal_or_agent`，`USER_TOKEN` 改为可选
- `GatewayClient` 不再在构造时锁定 `user_token`，改为每次请求按需注入 `X-User-Token` / `X-Admin-Secret` header
- `addresses` 缓存键由全局 `addr:user` 改为 `addr:{phone or 'user'}`（personal 模式键不变；agent 模式按手机号分桶）
- `addresses select` 不再 `cache.delete + 强制重拉` 而是把新地址插到缓存头部，省掉 ~25s 的二次 round-trip
- `addresses` saved 列表加上 `last_used_at`、`use_count`、`detail`、`contact_*`、`tag` 字段，支持"上次送过 XX"对话路径
- `version` 0.2.0 → 0.3.0
- 三平台 SKILL.md 同步更新调用示例、环境变量列表
- GUIDE.md 增加 Step 0（token 解析）、🏙️ city 铁律、Step 4.5（饮品规格确认）、Step 6 channel 路由、preview 内置错误回收说明

### Backwards-compatible

- 旧用户的 `.env`（`GATEWAY_URL/API_KEY/USER_TOKEN/DEFAULT_LAT/LNG`）不动直接用：personal 模式不传 `--phone` 时行为与 0.2.0 一致
- `--shop-keyword` / `--keyword` / `--address-keyword` / `--search-keyword` 旧别名继续可用
- `DEFAULT_LAT/LNG` 仍是 personal 模式的冷启动兜底；agent 模式忽略

## [0.2.0] - 2026-04-14

### Added

- `recommend` action：搜店 + 并行抓 top N 家菜单一步到位（默认 3 家、最多 5 家），省一次推理回合
- `build_menu_overview(compact=True)` 模式：跳过 ¥0 噪音分类、按销量取 top 5，专为 `recommend` 用
- `--top-n` CLI 参数（recommend 用）
- `INSTALL.md` 新增 `DEFAULT_LAT/LNG` 环境变量说明（避免账户无已存地址时首次调用 422）

### Changed

- **GUIDE.md 全量重写**：从骨架版升级为完整 playbook
  - 决策流 Step 1-6 + 后续消息决策树（"还有吗/别的看看"按上下文判断指代）
  - 推荐两段式气泡输出模板（信息块 + 决策块）+ 模板规则
  - 菜单单气泡分组模板（招牌/搭着吃）
  - Preview 朋友口吻一段话模板
  - 导购铁律：画像锚点 + 稳定维度轴 + 长对话不衰减
  - 并行 & 性能规则（addresses + recommend 并行、单轮 ≤8、menu 全程一次）
  - Checkpoint 显式列表（必停 vs 可默做）
  - 兜底场景表、时段感、语气规则（书面 → 口语对照）
  - 翻车实录 + Good/Bad case 对照
- 平台 SKILL.md（claude-code/codex/openclaw）action 表加 `recommend` 行，地址段落改为 token 流

### Fixed

- `addresses` 默认列表 + 关键词搜索分支：网关现在强制要求 lat/lng，加缓存 → DEFAULT 兜底
- `preview` 地址 hydrate 路径：同样的 lat/lng 兜底，避免 422
- `preview` 处理 saved 地址 lat/lng 为空的情况（eleme history hydrate 不带坐标），fallback 到 args/缓存/DEFAULT
- 删除死代码 `GatewayClient.list_addresses()`（端点 `/api/v1/addresses` 已不存在）

### Breaking

- `addresses --select-source poi/eleme_history` + `--poi-data` + `--eleme-address-id` 全部移除
  - 原因：网关 `/addresses/select` 已改为 token-based shape；旧路径本来就是 broken 状态
  - 替代：`addresses --select-token sug_xxx --contact-name X --contact-phone Y [--address-detail "..."] [--address-tag home]`
  - `--select-token` 来自上一次 `addresses --address-keyword` 返回的 suggestions[].token
  - 当 suggestion.requires_detail=true（POI 类）时，`--address-detail`（门牌/楼层）必填，否则后端 500

## [0.1.0] - 2026-04-10

### Added

- 初版 takeout 技能
- 6 actions：addresses, search, menu, preview, order, order_status
- 基于 urllib 的 GatewayClient（无第三方依赖）
- 文件缓存（addresses 30min / search 5min / menu 10min）
- Personal auth 模型（GATEWAY_URL + API_KEY + USER_TOKEN）
- 三平台支持：claude-code, openclaw, codex
