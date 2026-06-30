# DECISIONS — open-gateway-clawdot-skill（takeout 迁移）

> 本仓库唯一权威决策源。实现严格遵守「已对齐」区；与之冲突先停下问，不自行其是。
> 背景：把 `clawdot-skills/skills/takeout` 从旧 **clawdot-gateway**（user_token 体系）
> 迁到 **open-gateway**（consent_grant / public v1 体系），功能等价、接口不同。

## 已对齐

### D1. 范围：只迁 `takeout`
- 只迁主 skill `takeout`。`takeout-superagent` **不迁**——它整体建立在 agent 静默绑定
  （trustedBind）之上，而 open-gateway 删除了该能力（见 D2），无法原样迁移。

### D2. 鉴权模型：api_key(env·agent) + cg(env·user，绑定后回写)，去掉 agent 静默绑定
- **KB 坐实**（[agent-cap-provider §2/§4]、[鉴权域链路 §2]、[user_token 续期篇]）：
  - `api_key`(clw_) = **agent 身份**，1 部署 1 个 → **env 静态、开局唯一需注入项**。
  - `cg_`(consent) = **用户授权 = 1 用户**（= 旧 user_token）：90 天有效、无刷新接口、到期/轮换靠重绑；
    一次性明文(show-once)、落库存 SHA256，SMS verify 每次换新 cg。**一个 api_key : N 个 cg**。
  - open-gateway public v1 **没有 `trustedBind`**（`rg trusted` 零命中）；admin secret 只用于创建 agent。
- **统一一条线**（不再割裂为"personal vs 用户绑定"两模式）：
  1. 注入 `API_KEY`（唯一必需注入项）。
  2. 用户走一次 SMS/H5 绑定（`request_code`/`verify_code`，**绑定步骤需 `--phone`**）→ 网关铸 cg →
     **skill 把 cg 回写进 `.env` 的 `CONSENT_GRANT_ID`**（+ 按手机号缓存 file/可选 Redis）。
  3. 业务调用读 env `CONSENT_GRANT_ID` → `X-Consent-Grant-Id`，**单用户无需再带 --phone**。
  4. cg 到期/轮换 → `CONSENT_GRANT_EXPIRED` → 重绑（SMS verify 出新 cg）→ **覆盖回写** env + 缓存
     （KB：SMS 每次换新、记得更新本地缓存——`verify_code` 用 upsert 正好对上）。
- **解析优先级（不带 --phone）**：env `CONSENT_GRANT_ID`（含回写的）→ 缓存中唯一已绑用户 →
  多个则要求带 `--phone` → 否则引导绑定。
- `--phone` + per-phone 缓存 = **一个安装服务多用户**（gateway `max_bindings>1`）的进阶叠加面；
  env 里的 cg = 默认/最近绑定用户。`CONSENT_GRANT_ID` env 保留为可手动预注入的高级覆盖项
  （无状态部署可跳过绑定直接喂长效 cg）。
- **回写安全**：cg 是 bearer 等价凭证 → 写后 `.env` chmod 0600、不回显；`.env` 已 gitignore；
  `.env` 不可写则回退 per-phone 缓存（`persisted_to_env=false` 告知）。
- **去掉**：`ADMIN_SECRET`、`USER_TOKEN`(env)、trustedBind、agent 静默绑定。
- 鉴权头：`Authorization: Bearer <API_KEY>` + `X-Consent-Grant-Id: <cg>`（用户态调用）。

### D3. 仓库结构：镜像源 `skills/takeout/...`
- 保留 `skills/<name>/{skill.yaml,scripts/,platforms/,GUIDE.md,evals/}` + 根 `build.py`/
  `install.sh`/`README`/`INSTALL`/`.github`，与源仓库一致。

### D4. 接口映射（旧→新，强制变更）
| 能力 | 旧 | 新 open-gateway |
| --- | --- | --- |
| 鉴权 | `X-User-Token` | `X-Consent-Grant-Id` |
| 绑定 | `/user/bind/{trusted,request,verify}` | `/auth/bind/{request,verify}`（无 trusted），verify 返回 `cg_` |
| 搜店 | `GET /shops/search` | `POST /shops/search`，**返回 `cart_id`（每店一个，须贯穿）** |
| 菜单 | `GET /shops/{id}` + `/items/{id}` | `POST /shops/menu`（shop_id+cart_id），商品详情内联（无独立 item 接口） |
| 地址 | `/addresses/{search,select}` | 同名；字段改：`saved→saved_addresses`、suggestion 出参 `token`、select 入参 `suggestion_token`、`detail→address_detail` |
| 下单 | `POST /orders/preview`（一步）→`/orders`（session_id） | `POST /orders/preview`→`preview_id`+`confirmation_token`→`POST /orders/create` |
| 查单 | `GET /orders/{id}` | 同名 |

### D5. 接口变更的强制下游影响（非偏好，新接口决定）
- **下单 item 模型变**：旧 `{item_id, specs{}, attrs{}}` → 新 `{item_id, sku_id?, quantity, ingredient_option_ids?, remark?}`。
  agent 直接从菜单出参 `sku_options[].sku_id`(public `sku_`) 与 `ingredient_options[].option_id`(public `opt_`) 取值；
  skill 不再做 specs→sku 解析（网关内部按 cart context 解析）。
- **金额单位 = 分**（旧为元/字符串混杂）。
- **付款链接**：create 出参 `payment_action.action_url`（旧为 `payment_link`）。
- **cart_id 贯穿**：search/recommend 把每店 `cart_id` 按 `shop_id` 缓存（TTL<上游 30min），
  menu/preview 内部按 shop_id 取回；缓存失效 → `RECOVERY[SHOP_CART_MISS]` 引导重搜（不对 agent 暴露 cart_id）。
- **order action 入参**：`--preview-id` + `--confirmation-token`（取代 `--session-id`）。

### D6. 保留不变（功能等价）
- 9 个 action 名不变：addresses/search/recommend/menu/preview/order/order_status/request_code/verify_code。
- 输出约定不变：成功 JSON→stdout；失败「中文翻译 + `RECOVERY[CODE]:` 」→stderr、非零退出。
- 错误 playbook 哲学不变（给 agent 可执行下一步）；错误码适配新网关
  （新增 `CONSENT_*`/`PUBLIC_REFERENCE_INVALID`/`SHOP_CART_MISS`，保留 below-min/closed/sold-out/must-pick 业务语义）。
- 文件缓存 + 可选 Redis（极简裸 socket）保留；缓存内容从 user_token 改为 cg。
- 无第三方依赖（urllib + 标准库）。

### D7. 暂不迁（超出旧 skill 功能面，保持等价）
- update_address（仅在 `--address-tag` 时 select 后顺带调一次设标签，客户端留方法）、coupons、
  payment sign、homepage-url、share-url、unbind、cart/quote（preview_order 已独立完成预览，不另接 quote）、
  shops 浏览翻页 offset 的对外暴露。这些是 open-gateway 新增能力，迁移不主动引入，避免偏离「功能相同」。

### D8. 交付前 review 修正（迁移引入的回归 / 凭证存储硬化）
- order_id 拼 URL path 段恢复 `quote()` 转义（源码本有、迁移初稿漏了的安全回归），加对抗用例（G3）。
- 缓存文件/目录收紧到 0600/0700（缓存现存 consent_grant 等价凭证）。
- 以下为**源仓库原样照搬、非本次迁移引入**的既有项，留作后续、本次不动（避免范围蔓延）：
  install.sh tar 解包未校验成员路径、build.py 打包 scripts 无 denylist、curl|bash 指向 main、
  裸 socket Redis 解析的粘包风险、stderr/recovery 文案含未脱敏手机号。

### D9. 对照官方《API接口说明文档 v1.5》逐接口核对（2026-06-30）
- **接口契约面：与 v1.5 逐字段吻合（且实测）**——auth 头、所有 path、请求体字段、items 模型
  （`item_id/sku_id?/quantity/ingredient_option_ids?/remark?`）、出参字段名（`saved_addresses`/
  `suggestions[].token`/`shop_id`/`cart_id`/`sku_options`/`ingredient_options[].option_id`/`preview_id`/
  `confirmation_token`/`payment_action.action_url`/金额分）全部对齐；v1.5 新增字段（`original_price`、
  items 的 `specs`/`selected_ingredients`、SKU 专属 `ingredient_options`）整段透传自动带上。
- **错误码：文档 §13 外部码 ≠ 当前部署内部码（实测）**——部署实返 `CONSENT_GRANT_*`/`AUTH_INVALID`/
  `SHOP_NOT_FOUND`/`ORDER_FAILED`/`IDEMPOTENCY_CONFLICT` 等（已实测）；文档 §13 列的是另一套外部码
  （`CAPABILITY_FORBIDDEN`/`ADDRESS_REQUIRED`/`SHOP_UNAVAILABLE`/`ITEM_UNAVAILABLE`/`CART_CONTEXT_EXPIRED`/
  `CONFIRMATION_CONFLICT`/`ORDER_CREATE_FAILED`/`PRICE_CHANGED`/`CONFIRMATION_REQUIRED`/`AUTH_EXPIRED`/
  `BINDING_LIMIT_REACHED`）。**处理**：把两套码都并进错误 playbook（实测码 + 文档码），并捕获错误
  `next_action`（文档 §12.3 的稳定路由信号；当前部署不返回→前瞻兼容、不影响实测）消解 `AUTH_REQUIRED`
  在「文档=用户未授权」vs「部署=api_key 缺失」的语义歧义。verify G7 覆盖。
- **delete_address（§7.4）/ get_user_auth_status / get_homepage_url / sign / coupons**：文档有、本 skill
  未接 action（D7 同口径，保持与旧 skill 功能等价，不主动引入）。客户端按需可补。

## 验收标准（可证伪）

### 机器可判定（写入 verify.sh 硬 gate，绿才算 done）
- **G1 编译**：`python3 -m py_compile skills/takeout/scripts/takeout.py` 通过。
- **G2 CLI 冒烟**：`takeout.py --action <每个> --help`/缺参不崩在 argparse；`--action xxx` 非法值被拒。
- **G3 接口契约单测**（monkeypatch HTTP 层，断言出站请求）：每个 GatewayClient 方法产出
  正确的 `method + path + headers(Bearer + X-Consent-Grant-Id) + body 字段`，逐方法覆盖：
  - bind/request、bind/verify（sms+h5）
  - shops/search、shops/menu
  - addresses/search、addresses/select
  - orders/preview、orders/create、orders/{id}（含 order_id 含 `/`、`..` 时被转义成单 path 段的对抗用例）
  断言中 path 全部以 `/api/v1/` 开头；用户态调用必带 `X-Consent-Grant-Id`；绑定调用**不**带。
- **G4 负向红线**：grep 断言新脚本无**操作性**旧符号残留（区别于 docstring 里解释迁移差异的文字）：
  不设 `headers["X-User-Token"]`/`X-Admin-Secret`、不读 env `ADMIN_SECRET`/`USER_TOKEN`、
  不调 `/api/v1/user/bind/`、无 `def trusted_bind`、无 `session_id`（order 入参）。
- **G5 流程贯穿单测**：模拟 search→menu→preview→order，断言 `cart_id` 从 search 缓存被 menu/preview 取回、
  `preview_id`+`confirmation_token` 从 preview 传到 create。
- **G6 绑定回写 + 解析优先级单测**（D2 核心）：`verify_code` 成功后把 `CONSENT_GRANT_ID=<cg>` upsert 进 `.env`
  （保留其它键、`persisted_to_env=true`）、下一次读 env 即得该 cg；`resolve_consent_grant(None)` 优先级
  env → 缓存唯一 → 多个 die 要求 --phone。
- **G7 文档 v1.5 §13 错误码全覆盖单测**（D9）：每个文档外部错误码（含 `CAPABILITY_FORBIDDEN`/`ADDRESS_REQUIRED`/
  `SHOP_UNAVAILABLE`/`ITEM_UNAVAILABLE`/`CART_CONTEXT_EXPIRED`/`CONFIRMATION_CONFLICT`/`ORDER_CREATE_FAILED`/
  `PRICE_CHANGED`/`CONFIRMATION_REQUIRED`/`AUTH_EXPIRED`/`BINDING_LIMIT_REACHED`）映射到定向 RECOVERY（非通用兜底）；
  `AUTH_REQUIRED` 带 `next_action=request_user_bind` 走绑定恢复、否则走 api_key 提示。

### 人核（无法自动：需真实 API_KEY + 线上 open-gateway + 真实用户授权 cg，成本=真实下单花钱+真人验证码）
- H1：端到端真跑一单（search→menu→preview→order→order_status）拿到真实付款链接。
  - 为何不自动：下单是 money path，需真实 consent 授权（短信/H5 真人操作）+ 真实支付，
    无法在 CI 安全自动化。证据形式：贴一次真实 run 的 compact 输出（脱敏）。
  - fallback：G3/G5 用 monkeypatch 钉死「请求构造正确」（手段断言）；端到端「真能下单」（目的断言）留 H1 人核。

## 负向红线（碰现有行为/接口）
- 本仓库是**新建**目标仓库（源 README 占位），无既有运行行为可破坏 → 纯新增，无字节级红线。
- 但对**外部契约**守一条：agent 调用面（action 名、stdout JSON 为成功信号、RECOVERY 机制）
  与旧 skill 保持兼容语义；item 模型/order 入参的变更已在 D5 记录为「接口强制变更」，须在 GUIDE/SKILL 同步说明，不得静默改。
