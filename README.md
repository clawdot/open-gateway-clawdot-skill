# open-gateway-clawdot-skill

基于 **open-gateway** 对外 **MCP** 面（consent_grant 体系）的 ClawDot AI Agent 技能集合：CLI 每个子命令 = 一次 JSON-RPC `tools/call`，skill 分发物不含任何内部 HTTP API 路径。

> 从旧 [`clawdot-skills`](https://github.com/clawdot/clawdot-skills)（clawdot-gateway / user_token 体系）迁移而来，
> **功能等价、底层接口改用 open-gateway**。迁移决策与验收见根目录 [`DECISIONS.md`](DECISIONS.md)。

## 技能列表

| 技能 | 说明 | Auth 模型 | 平台 |
|------|------|-----------|------|
| [takeout](skills/takeout/) | 外卖点餐（搜店、选菜、下单、查单） | API_KEY + 用户绑定（共享凭证缓存） | Claude Code, OpenClaw, Codex |

## 与旧网关的关键差异

| 维度 | 旧 clawdot-gateway | 新 open-gateway |
|------|--------------------|------------------|
| 传输 | HTTP `/api/v1/*` | **MCP** `tools/call`（`/mcp/v1`，JSON-RPC，stateless） |
| 用户态鉴权 | `X-User-Token` header | `consent_grant_id`（cg_）作为 tool **参数** |
| 鉴权模式 | personal / agent(trustedBind) / 用户绑定 | personal / 用户绑定（**agent 静默绑定已移除**） |
| 绑定接口 | `/api/v1/user/bind/*` | MCP tool `request_user_bind`/`verify_user_bind`，verify 返回 consent_grant |
| 选店→选菜 | 各自独立 | 搜店返回 `cart_id`，贯穿 menu/preview（skill 内部按 shop_id 缓存） |
| 下单 item | `{item_id, specs, attrs}` | `{item_id, sku_id?, quantity, ingredient_option_ids?, remark?}` |
| 下单交接 | preview→`session_id`→order | preview→`preview_id`+`confirmation_token`→create |
| 金额单位 | 元/字符串混杂 | 分 |

> Agent 调用面：子命令 1:1 用网关 MCP tool 名（search_shops / get_shop_menu / preview_order / create_order …），stdout JSON 为成功信号、`RECOVERY[CODE]` 机制不变。

## 目录结构

```
skills/
└── takeout/
    ├── skill.yaml              # 技能元数据（名称、版本、依赖、能力声明）
    ├── GUIDE.md                # 交互指南（平台无关，技能的"灵魂"）
    ├── CHANGELOG.md
    ├── .env.example
    ├── scripts/clawdot.py      # 共享 CLI（MCP 客户端 + 12 个子命令）
    ├── references/             # 命令/参数/错误码契约文档
    ├── evals/                  # 评测用例
    └── platforms/              # 平台适配层（claude-code / openclaw / codex）
DECISIONS.md                    # 迁移决策账本（唯一权威决策源）
verify.sh                       # 验收硬 gate（编译/argparse/接口契约/流程/负向红线）
tests/test_clawdot_cli.py       # 契约 + 流程测试（无 pytest 依赖）
build.py / install.sh           # 构建与安装
```

## 配置

skill 根目录放 `.env`（参考 `skills/takeout/.env.example`）：

```bash
GATEWAY_MCP_URL=https://eleme-gateway.hicaspian.com/mcp/v1
API_KEY=<你的 clw_ 密钥>          # 唯一必需注入项（agent 身份）
# CONSENT_GRANT_ID 只读预注入项（无状态部署跳过绑定才需要）；正常流程留空：
# 用户走一次 SMS/H5 绑定后凭证存共享缓存（~/.clawdot/credentials.json），
# 单用户业务调用无需 --phone。多用户则各自绑定、业务调用带 --phone 指定。
# CONSENT_GRANT_ID=
```

> 模型：一个 `api_key` 服务多个用户，一个 `consent_grant`（cg_）= 一个用户（90 天有效）。
> 只需注入 `API_KEY`；cg 由绑定产生并写入共享凭证缓存（`~/.clawdot/`，`CLAWDOT_HOME` 可重定向）。

## 安装

```bash
# Claude Code
curl -fsSL https://raw.githubusercontent.com/clawdot/open-gateway-clawdot-skill/main/install.sh | bash -s -- takeout claude-code
# Codex（项目根目录执行）
curl -fsSL https://raw.githubusercontent.com/clawdot/open-gateway-clawdot-skill/main/install.sh | bash -s -- takeout codex
# OpenClaw
curl -fsSL https://raw.githubusercontent.com/clawdot/open-gateway-clawdot-skill/main/install.sh | bash -s -- takeout openclaw
```

安装脚本自动从最新 Release 下载、校验 sha256、解压到正确位置。指定版本加 `v1.0.0` 参数。
AI Agent 安装参考 [INSTALL.md](INSTALL.md)。

## 构建 / 验证 / 发布

```bash
python3 build.py                  # 构建所有平台变体到 dist/
python3 build.py takeout          # 构建指定技能
python3 build.py --list           # 查看可用技能
bash verify.sh                    # 跑验收硬 gate（编译 + 接口契约 + 流程 + 负向红线）
python3 build.py --release 1.0.0  # 打包 release（tar.gz + manifest.json）
```

推送 `v*` 标签触发 GitHub Actions 自动构建并挂到 Release。

## Auth 模型

| 情形 | 说明 | 配置 |
|------|------|----------|
| **单用户（默认）** | 注入 `API_KEY` → 用户绑一次 SMS/H5 → cg 存共享缓存 → 业务调用无需 `--phone` | `API_KEY`（`GATEWAY_MCP_URL` 默认公网端点） |
| **多用户** | 一个 api_key 服务多个用户，各自绑定，业务调用带 `--phone` 指定（按手机号存共享缓存） | `API_KEY` |
| **预注入（高级）** | 无状态部署直接喂一个长效 cg，跳过绑定 | `+ CONSENT_GRANT_ID` |
