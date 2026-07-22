# PLAN — CLI×MCP 传输迁移（任务清单，每条挂 spec§ / M 决策号）

> HOW 层。WHAT 见 docs/spec-cli-mcp-transport.md，WHY 见 DECISIONS.md M1–M10。

## 关键事实（实现依据，已核实）

- MCP tools 与 HTTP /api/v1 共用同一 `PublicGatewayService` → **出参字段同源，零裁剪适配**（M5 结构性坐实）。
- tool 入参名与旧 GatewayClient 方法参数几乎一一同名（keyword/lat/lng/city/offset/shop_id/cart_id/...），
  差异仅：consent 从 `X-Consent-Grant-Id` header → `consent_grant_id` argument；
  `select_address` 直接收 `tag`（旧"select 后 update 设标签"绕路可删）。
- 错误面：业务错 = content 内 `{"error":{code,message}}`、isError=False（网关 `_translate_gateway_error`）；
  鉴权失败同样走信封（`AUTH_REQUIRED`）；isError=True 仅非网关异常。
- 端点：`/mcp/v1`（app.mount("/mcp") + streamable_http_path="/v1"），stateless+json_response。

## Tasks

- T1 `scripts/clawdot.py` 传输层（spec§3 / M6）：`MCPClient._call(tool, arguments)` 单 POST JSON-RPC；
  四层响应解析（HTTP错→RPC error→isError→业务信封→成功）；GatewayError 语义保留。
- T2 凭据层（spec§4 / M4）：`CredStore`（$CLAWDOT_HOME|~/.clawdot/credentials.json，
  sha256(API_KEY)[:12] → phone → {consent_grant_id, expires_at}，0700/0600）；
  resolve 优先级 env(只读)→唯一→--phone→引导绑定；**删 .env 回写、删 RedisCache**（cg 单一持久化源）。
- T3 子命令化（spec§5 / M7）：argparse subparsers，1:1 tool 名 12 个文档化命令 + `recommend` 复合 +
  `call <tool> --json` 通用后门（M7 附注）；操作缓存（search/menu/cart/addr，~/.cache/clawdot-takeout）
  与裁剪器/坐标解析原样保留；RECOVERY 文案中的命令名同步改。
- T4 错误 playbook（spec§7 / MG3）：正则表 + 顺位红线原样迁移；AUTH_* 分支保留。
- T5 测试 `tests/test_clawdot_cli.py`（MG1/MG3/MG4/MG5）+ 删旧测试；verify.sh 改 MG gates（含 MG2 grep）。
- T6 文档（spec§8）：skill.yaml（entry_point/actions/env：GATEWAY_MCP_URL、去 REDIS_URL、CLAWDOT_HOME）、
  platforms×3、GUIDE.md、references/{commands,params,errors}.md 新建、README/INSTALL、evals.json。
- T7 gates 本地全绿（MG1–MG5）→ 线上端到端（MG6，凭据从 docs/测试配置 取）→ commit 分批提交。

## Self-review trace

spec§2→T1/T6 · §3→T1 · §4→T2 · §5→T3 · §6→T3(缓存保留) · §7→T4 · §8→T6 · §9→T5/T7 · §10→T7(PR 人工合)
