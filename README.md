# open-gateway-clawdot-skill

基于 **open-gateway**（ClawDot 对外网关 public v1，consent_grant 体系）的 ClawDot AI Agent 技能集合。

> 从旧 [`clawdot-skills`](https://github.com/clawdot/clawdot-skills)（clawdot-gateway / user_token 体系）迁移而来，
> **功能等价、底层接口改用 open-gateway**。迁移决策与验收见根目录 [`DECISIONS.md`](DECISIONS.md)。

## 技能列表

| 技能 | 说明 | Auth 模型 | 平台 |
|------|------|-----------|------|
| [takeout](skills/takeout/) | 外卖点餐（搜店、选菜、下单、查单） | personal / 用户绑定 | Claude Code, OpenClaw, Codex |

## 与旧网关的关键差异

| 维度 | 旧 clawdot-gateway | 新 open-gateway |
|------|--------------------|------------------|
| 用户态鉴权 | `X-User-Token` | `X-Consent-Grant-Id`（cg_） |
| 鉴权模式 | personal / agent(trustedBind) / 用户绑定 | personal / 用户绑定（**agent 静默绑定已移除**） |
| 绑定接口 | `/api/v1/user/bind/*` | `/api/v1/auth/bind/*`，verify 返回 consent_grant |
| 选店→选菜 | 各自独立 | 搜店返回 `cart_id`，贯穿 menu/preview（skill 内部按 shop_id 缓存） |
| 下单 item | `{item_id, specs, attrs}` | `{item_id, sku_id?, quantity, ingredient_option_ids?, remark?}` |
| 下单交接 | preview→`session_id`→order | preview→`preview_id`+`confirmation_token`→create |
| 金额单位 | 元/字符串混杂 | 分 |

> Agent 调用面（action 名、stdout JSON 为成功信号、`RECOVERY[CODE]` 机制）保持兼容。

## 目录结构

```
skills/
└── takeout/
    ├── skill.yaml              # 技能元数据（名称、版本、依赖、能力声明）
    ├── GUIDE.md                # 交互指南（平台无关，技能的"灵魂"）
    ├── CHANGELOG.md
    ├── .env.example
    ├── scripts/takeout.py      # 执行脚本（open-gateway 客户端 + 9 个 action）
    ├── evals/                  # 评测用例
    └── platforms/              # 平台适配层（claude-code / openclaw / codex）
DECISIONS.md                    # 迁移决策账本（唯一权威决策源）
verify.sh                       # 验收硬 gate（编译/argparse/接口契约/流程/负向红线）
tests/test_takeout_gateway.py   # 契约 + 流程测试（无 pytest 依赖）
build.py / install.sh           # 构建与安装
```

## 配置

skill 根目录放 `.env`（参考 `skills/takeout/.env.example`）：

```bash
GATEWAY_URL=https://clawdot.hicaspian.com/gateway
API_KEY=<你的 clw_ 密钥>          # 唯一必需注入项（agent 身份）
# CONSENT_GRANT_ID 开局留空：用户走一次 SMS/H5 绑定后，脚本自动把 cg 回写到这行；
# 之后单用户业务调用无需 --phone。多用户则各自绑定、业务调用带 --phone 指定。
CONSENT_GRANT_ID=
```

> 模型：一个 `api_key` 服务多个用户，一个 `consent_grant`（cg_）= 一个用户（= 旧 user_token，90 天有效）。
> 只需注入 `API_KEY`；cg 由绑定产生并自动回写 `.env`。

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
| **单用户（默认）** | 注入 `API_KEY` → 用户绑一次 SMS/H5 → cg 自动回写 `.env` → 业务调用无需 `--phone` | `GATEWAY_URL` + `API_KEY`（cg 绑定后自动填） |
| **多用户** | 一个 api_key 服务多个用户，各自绑定，业务调用带 `--phone` 指定（按手机号缓存 cg） | `GATEWAY_URL` + `API_KEY` |
| **预注入（高级）** | 无状态部署直接喂一个长效 cg，跳过绑定 | `+ CONSENT_GRANT_ID` |
