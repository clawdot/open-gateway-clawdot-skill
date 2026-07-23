# Changelog

## [2.2.0] - 2026-07-23 — 大清单形态：套餐类规格一次给全、不省略、不编造

### Added

- **「大清单」展示形态**（选项总数 > 15 或组数 ≥ 4 时启用，如麻辣烫「55选11」套餐＝6 组 45 项）：
  结论在前、全量在后——上半部分每组一行只写默认项（3 秒读完就能下单），下半部分每组一行
  列全该组选项并标 `（N选1）`，**一条消息里给全，不分轮、不省略**。

### Fixed

- ❌ **禁止用「……」「等」「更多选项」省略选项**：用户没有界面可以点开，省掉的部分对他等于不存在。
- 🚨 **禁止编造/改写选项名**：选项名逐字照抄菜单返回，`（N选1）` 的 N 必须等于实际条数。
  （实测发现模型在"必须列全"的压力下会凭空造出菜单里没有的选项——用户一旦选中就下不了单，
  比省略更危险。）克重/份数括号可以省掉让清单好扫，但菜名本身不能动。

## [2.1.2] - 2026-07-22 — GUIDE 优化：规格清单格式 + 并行规则修正 + 工具面补全

话术层更新，无代码改动。

### Changed

- **规格/选项展示统一为清单形态**（Step 4.5 新增「规格清单格式」模板 + 单品/多商品两个示例）：
  规格行带各自价格用 `|` 分隔，每个属性组（温度/甜度/辣度/加料…）各占一行、组内用 `/` 分隔、
  默认项标`（推荐）`排首位、加价项标 `+¥X`；禁止散文式罗列与展示内部 id。**所有出现规格的地方
  统一此格式**：单品确认、多商品（麻辣烫等）、店铺必选组 `required_groups`、
  `MUST_PICK_REQUIRED` / `MISSING_REQUIRED_SELECTION` 的补选提示。
- **搜店坐标依赖规则改为条件式**（原先自相矛盾：Step 1 让并行、性能规则禁止并行）：
  本会话未取过地址 → 先单发 `search_addresses` 再搜店；已有地址/已选地址 → 允许并行，
  少等一轮。规则依据是搜店坐标取自本地地址缓存、缓存空时会落默认坐标。

### Added

- **多商品规格一次取全**：Step 4.5 补 `get_item_options`（此前 GUIDE 从未提及），
  麻辣烫等多商品场景不再逐个商品查菜单。
- **订单状态查询**：决策树补「订单到哪了」→ `get_order_status`（此前无处置说明），
  并补解绑意图 → `revoke_user_bind` 的入口指引。
- **`BELOW_MIN_ORDER`（整单未达起送价）处置**：告知差额 + 给低价单品选项，用户点头才加，
  禁止自行凑单。
- **防编造网关地址**：写 `.env` 时 `GATEWAY_MCP_URL` 必须原样抄 stderr，禁止自造 URL。

## [2.1.1] - 2026-07-22 — 输出规范：不展示内部推理

### Fixed

- **总原则新增「绝不输出思考独白与元叙述」**：要求助手只输出结论——回复第一句直接
  面向用户，时段判断、模板选择等内部推理过程不展示，同一问题整条回复只出现一次。
  提升各模型下的回复质量与一致性。话术层更新，无代码改动。

## [2.1.0] - 2026-07-22 — 绑定生命周期收尾：解绑命令 + env 遮蔽警告

### Added

- **`revoke_user_bind` 一等子命令**（此前仅 `call` 后门可达）：服务端撤销 consent +
  清除本机共享缓存条目一步完成（`CredStore` 新增 `delete`）。多用户带 `--phone`；
  不带时按「env cg → 缓存唯一用户」解析；缓存多用户则要求 `--phone`。服务端已失效
  （CONSENT_*/AUTH_REQUIRED）视为目的已达成，照样清本地并回 `server_state:
  "already_invalid"`。解绑不删数据：地址/订单史保留，重绑同号恢复。
- **env 遮蔽警告**：`verify_user_bind` 成功时若检测到 `CONSENT_GRANT_ID` 环境变量
  （通常来自 `.env`）与新绑 cg 不同，stdout JSON 附 `warning` 提示删除 `.env` 残留行
  （env 值优先级最高，会遮蔽新凭证）；`revoke_user_bind` 撤销 env 来源凭证时同样提示。

## [2.0.0] - 2026-07-22 — 传输层迁移：HTTP /api/v1 → CLI×MCP

脚本从 `takeout.py`（HTTP `/api/v1` 客户端）重构为 `clawdot.py`（**MCP 客户端**）：
每个子命令 = 一次 JSON-RPC `tools/call` POST 到 `GATEWAY_MCP_URL`（默认
`https://eleme-gateway.hicaspian.com/mcp/v1`，stateless、纯标准库）。业务功能、
裁剪器、错误 playbook、`RECOVERY[CODE]` 机制与 stdout/stderr 契约全部保留。
决策见根 `DECISIONS.md`「第二轮」M1–M10。

### Changed（破坏性）

- **子命令制，1:1 网关 MCP tool 名**：`--action search` → `search_shops`、`menu` →
  `get_shop_menu`、`addresses` → `search_addresses` + `select_address`（拆分）、
  `preview`/`order`/`order_status` → `preview_order`/`create_order`/`get_order_status`、
  `request_code`/`verify_code` → `request_user_bind`/`verify_user_bind`；`recommend`
  保留为复合命令。参数名对齐 tool 入参：`--shop-keyword`/`--address-keyword` →
  `--keyword`、`--select-token` → `--sug-ref`、`--address-tag` → `--tag`。
- **凭据持久化改共享缓存**（不再回写 `.env`）：cg 写
  `$CLAWDOT_HOME/credentials.json`（默认 `~/.clawdot/`），按 `sha256(API_KEY)[:12]`
  + 手机号键控、0700/0600。同实例多 skill 共用同一 consent 不互踢；skill 升级
  重装不丢绑定。`CONSENT_GRANT_ID` env 降级为只读预注入项。
- **环境变量**：`GATEWAY_URL` → `GATEWAY_MCP_URL`；`REDIS_URL` 移除（共享文件缓存
  取代 Redis 共享 cg）；新增可选 `CLAWDOT_HOME`。
- 用户态鉴权从 `X-Consent-Grant-Id` header 改为 `consent_grant_id` tool 参数。

### Added

- 新子命令 `get_item_options`（批量查规格，含选中标记）、`get_user_auth_status`
  （验活 consent，不触发重绑）、`call <tool> --json`（未文档化 tool 的机械通道）。
- `select_address` 直接支持 `--tag`（旧版 select 后需补一次 update）。
- `references/` 三件套：commands.md / params.md / errors.md（LLM 契约文档）。

### Removed

- `takeout.py`、`.env` 回写（`write_env_var`）、裸 socket Redis 客户端、
  order_id URL 拼接（order_id 现走 JSON 参数，无 path 注入面）。

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
