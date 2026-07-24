# 虾点 agent profile（open-gateway 版）

声明式 agent 包：把「人格 + 技能 + 环境 + 子代理 + MCP」打成一个可物化的模板，
`install.sh` 一条命令落成 OpenClaw profile。**格式逐项对应 OpenClaw 原生约定**，不发明新机制。

## 目录

| 文件 | 是什么 | 物化到哪 |
|---|---|---|
| `SOUL.md` | 虾点人格 + 能力叙事（consent 授权版，无 trustedBind/user_token） | workspace 根 `SOUL.md`（OpenClaw 原生读取） |
| `skills/` | 占位；实际由 install.sh 从 GitHub Release 拉 `clawdot-takeout` 最新包 | `workspace/skills/clawdot-takeout/` |
| `env.example` | `API_KEY`（网关）+ `DEEPSEEK_API_KEY`（模型）等 | skill `.env` + 模型 provider 配置 |
| `agents/xiadian.json` | 子代理定义模板（id/workspace/persona/skills） | 单代理=main agent；多代理经 `openclaw agents add` |
| `mcp.json` | MCP server 声明（open-gateway 直连，**默认关**） | `openclaw mcp set`（仅 `--with-direct-mcp`） |
| `install.sh` | 物化器：onboard → SOUL → 模型 → skill → mcp → 校验 | — |

## 用法

```bash
API_KEY=clw_xxx DEEPSEEK_API_KEY=sk-xxx bash profiles/xiadian/install.sh
openclaw --profile og-xiadian gateway run
```

## 设计要点

- **外卖不走 MCP 直连、走 skill 内 CLI**（CLI 每个子命令本身就是一次 MCP tools/call）。
  原因：CLI 承担结果裁剪（省上下文）、购物车缓存（cart_id 隐藏态）、错误 playbook
  （RECOVERY 引导）、共享凭证解析（按手机号取 consent）。把 20 个原始 tool 直挂给模型
  会双重暴露且丢掉这四层。`mcp.json` 保留声明供直连实验对比。
- **SOUL.md 只含人格层 + 外卖能力叙事**。平台自有工具（昵称/换号/定时/天气）的行为
  规范在文件尾部「平台扩展位」标记处追加，人格层不动——同一份 SOUL 可被 Hermes 复用。
- **绑定叙事 = consent 一次性授权**（90 天），没有静默绑定；SOUL 里明确"不预判绑定
  状态、只在 skill 返回引导时才带用户授权"。
