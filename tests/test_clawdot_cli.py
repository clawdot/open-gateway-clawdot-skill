#!/usr/bin/env python3
"""Contract + flow tests for the MCP-transport CLI (clawdot.py).

Runs standalone (no pytest dependency) so verify.sh can gate on it:
    python3 tests/test_clawdot_cli.py

Strategy: monkeypatch ``clawdot.urlopen`` to capture the actual outbound
``Request`` (url / headers / JSON-RPC body) and feed canned MCP responses.
This validates the M-round contract end to end (DECISIONS 第二轮)：
- MG1: every call is one POST $GATEWAY_MCP_URL with body
  {jsonrpc, method: tools/call, params: {name, arguments}}, Bearer auth,
  consent_grant_id as an ARGUMENT on user-state tools and absent on bind tools;
- MG3: the {"error": {code, message}} envelope (isError=False) routes to the
  directed RECOVERY playbook, ordering red lines intact;
- MG4: shared credential store keyed (sha256(API_KEY)[:12], phone), 0700/0600,
  resolution priority env → unique → --phone → guided bind, no silent re-bind;
- MG5: cart_id threading search→menu/preview, preview_id+confirmation_token
  handoff to create_order."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "takeout" / "scripts"))

import clawdot  # noqa: E402

MCP_URL = "https://gw.example/mcp/v1"


def make_config(**overrides) -> "clawdot.Config":
    base = dict(
        mcp_url=MCP_URL,
        api_key="KEY",
        consent_grant_id="",
        setup_url="https://setup.example",
        default_lat=None,
        default_lng=None,
        timeout_ms=30000,
        clawdot_home=Path(tempfile.mkdtemp()) / "clawdot-home",
    )
    base.update(overrides)
    return clawdot.Config(**base)


_CFG = make_config()


# ── Fake transport ───────────────────────────────────────────────────────────

_CALLS: list[dict] = []
_NEXT: dict = {"payload": {}}


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload, ensure_ascii=False).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen(req, timeout=None):
    headers_ci = {k.lower(): v for k, v in req.headers.items()}
    _CALLS.append({
        "url": req.full_url,
        "method": req.get_method(),
        "headers": headers_ci,
        "body": json.loads(req.data) if req.data else None,
    })
    return _FakeResp(_NEXT["payload"])


clawdot.urlopen = _fake_urlopen


def set_tool_response(payload: object) -> None:
    """Feed a canned SUCCESS tool result (payload rides in content[0].text)."""
    _NEXT["payload"] = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                   "isError": False},
    }


def set_raw_response(payload: dict) -> None:
    """Feed a raw JSON-RPC response (for rpc-error / isError cases)."""
    _NEXT["payload"] = payload


def last_call() -> dict:
    return _CALLS[-1]


# ── Harness ──────────────────────────────────────────────────────────────────

_RESULTS: list = []
_FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        _RESULTS.append(name)
    else:
        _FAILS.append(f"{name}: {detail}")


def fresh_cache() -> "clawdot.Cache":
    tmp = Path(tempfile.mkdtemp())
    clawdot.CACHE_DIR = tmp
    clawdot.CACHE_FILE = tmp / "cache.json"
    return clawdot.Cache()


def fresh_creds(api_key: str = "KEY") -> "clawdot.CredStore":
    return clawdot.CredStore(api_key, Path(tempfile.mkdtemp()) / "home")


def parse(argv: list[str]):
    return clawdot.build_parser().parse_args(argv)


def run_dying(fn, *args) -> str:
    """Run a function expected to die(); return its stderr text."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            fn(*args)
    except SystemExit:
        pass
    return buf.getvalue()


def run_ok(fn, *args) -> dict:
    """Run a command expected to succeed; return parsed stdout JSON."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return json.loads(buf.getvalue())


def rpc_of(call: dict) -> tuple[str, dict]:
    body = call["body"]
    return body["params"]["name"], body["params"]["arguments"]


# ── MG1: per-tool transport contract ─────────────────────────────────────────

def test_transport_contract() -> None:
    gw = clawdot.MCPClient(_CFG)
    CG = "cg_test123"

    # request_user_bind — bind tool: NO consent_grant_id argument
    set_tool_response({"bind_id": "bind_1", "expires_in": 300, "masked_phone": "138****8888"})
    gw.request_bind("13800008888", auth_type="sms")
    c = last_call()
    name, args = rpc_of(c)
    check("bind.url", c["url"] == MCP_URL, c["url"])
    check("bind.method", c["method"] == "POST", c["method"])
    check("bind.jsonrpc", c["body"]["jsonrpc"] == "2.0" and c["body"]["method"] == "tools/call",
          str(c["body"])[:120])
    check("bind.bearer", c["headers"].get("authorization") == "Bearer KEY", str(c["headers"]))
    check("bind.accept", c["headers"].get("accept") == "application/json", str(c["headers"]))
    check("bind.tool", name == "request_user_bind", name)
    check("bind.args", args == {"phone": "13800008888", "auth_type": "sms"}, str(args))
    check("bind.no_consent", "consent_grant_id" not in args, str(args))

    # verify_user_bind (sms)
    set_tool_response({"bound": True, "consent_grant_id": "cg_x", "expires_at": None})
    gw.verify_bind(auth_type="sms", bind_id="bind_1", code="123456")
    name, args = rpc_of(last_call())
    check("verify.tool", name == "verify_user_bind", name)
    check("verify.args", args == {"auth_type": "sms", "bind_id": "bind_1", "code": "123456"},
          str(args))

    # verify_user_bind (h5)
    set_tool_response({"bound": False, "status": "pending"})
    gw.verify_bind(auth_type="h5", request_id="req_9")
    name, args = rpc_of(last_call())
    check("verify_h5.args", args == {"auth_type": "h5", "request_id": "req_9"}, str(args))

    # search_shops — consent as ARGUMENT; None params dropped
    set_tool_response({"shops": []})
    gw.search_shops(CG, keyword="咖啡", lat=30.1, lng=120.2)
    name, args = rpc_of(last_call())
    check("search_shops.tool", name == "search_shops", name)
    check("search_shops.consent", args.get("consent_grant_id") == CG, str(args))
    check("search_shops.args",
          args == {"consent_grant_id": CG, "keyword": "咖啡", "lat": 30.1, "lng": 120.2,
                   "offset": 0},
          str(args))

    # get_shop_menu — cart_id must ride along
    set_tool_response({"shop": {}, "items": [], "categories": []})
    gw.get_shop_menu(CG, shop_id="shop_1", cart_id="cart_1")
    name, args = rpc_of(last_call())
    check("menu.tool", name == "get_shop_menu", name)
    check("menu.args", args == {"consent_grant_id": CG, "shop_id": "shop_1",
                                "cart_id": "cart_1", "offset": 0}, str(args))

    # get_item_options
    set_tool_response({"items": []})
    gw.get_item_options(CG, cart_id="cart_1", items=[{"item_id": "item_1"}])
    name, args = rpc_of(last_call())
    check("item_options.tool", name == "get_item_options", name)
    check("item_options.args", args == {"consent_grant_id": CG, "cart_id": "cart_1",
                                        "items": [{"item_id": "item_1"}]}, str(args))

    # search_addresses
    set_tool_response({"saved_addresses": [], "suggestions": []})
    gw.search_addresses(CG, keyword="西溪", city="杭州")
    name, args = rpc_of(last_call())
    check("addr_search.tool", name == "search_addresses", name)
    check("addr_search.args", args == {"consent_grant_id": CG, "keyword": "西溪",
                                       "city": "杭州"}, str(args))

    # select_address — tag rides directly (no follow-up update_address hop)
    set_tool_response({"address_id": "addr_1"})
    gw.select_address(CG, contact_name="王", contact_phone="13800008888",
                      suggestion_token="tok_1", address_detail="3-2-201", tag="家")
    name, args = rpc_of(last_call())
    check("addr_select.tool", name == "select_address", name)
    check("addr_select.args",
          args == {"consent_grant_id": CG, "contact_name": "王",
                   "contact_phone": "13800008888", "suggestion_token": "tok_1",
                   "address_detail": "3-2-201", "tag": "家"},
          str(args))

    # preview_order
    set_tool_response({"preview_id": "prv_1", "confirmation_token": "cf_1"})
    gw.preview_order(CG, shop_id="shop_1", cart_id="cart_1", address_id="addr_1",
                     items=[{"item_id": "item_1", "quantity": 1}])
    name, args = rpc_of(last_call())
    check("preview.tool", name == "preview_order", name)
    check("preview.args",
          args == {"consent_grant_id": CG, "shop_id": "shop_1", "cart_id": "cart_1",
                   "address_id": "addr_1", "items": [{"item_id": "item_1", "quantity": 1}],
                   "order_remark": ""},
          str(args))

    # create_order — no callback_url unless set (None dropped)
    set_tool_response({"order_id": "ord_1"})
    gw.create_order(CG, preview_id="prv_1", confirmation_token="cf_1")
    name, args = rpc_of(last_call())
    check("create.tool", name == "create_order", name)
    check("create.args", args == {"consent_grant_id": CG, "preview_id": "prv_1",
                                  "confirmation_token": "cf_1"}, str(args))

    # get_order_status — order_id as ARGUMENT (no URL path splicing anymore)
    set_tool_response({"order_id": "ord_1", "status": "pending_payment"})
    gw.get_order_status(CG, "ord_1/../evil")
    name, args = rpc_of(last_call())
    check("status.tool", name == "get_order_status", name)
    check("status.args", args == {"consent_grant_id": CG, "order_id": "ord_1/../evil"},
          str(args))
    check("status.url_untouched", last_call()["url"] == MCP_URL, last_call()["url"])

    # get_user_auth_status
    set_tool_response({"bound": True})
    gw.get_auth_status(CG)
    name, args = rpc_of(last_call())
    check("auth_status.args", args == {"consent_grant_id": CG}, str(args))


def test_url_normalization() -> None:
    n = clawdot.normalize_mcp_url
    check("url.origin", n("https://gw.example") == "https://gw.example/mcp/v1", n("https://gw.example"))
    check("url.mcp_base", n("https://gw.example/mcp") == "https://gw.example/mcp/v1",
          n("https://gw.example/mcp"))
    check("url.full", n("https://gw.example/mcp/v1/") == "https://gw.example/mcp/v1",
          n("https://gw.example/mcp/v1/"))


# ── MG3: error surface mapping ───────────────────────────────────────────────

def test_error_envelope_and_playbook() -> None:
    gw = clawdot.MCPClient(_CFG)

    # 业务错误信封（isError=False）→ GatewayError(code)
    set_tool_response({"error": {"code": "CONSENT_GRANT_EXPIRED", "message": "consent expired"}})
    try:
        gw.search_shops("cg_x", keyword="k")
        check("env.raises", False, "no exception")
    except clawdot.GatewayError as e:
        check("env.code", e.code == "CONSENT_GRANT_EXPIRED", e.code)
        msg = clawdot.friendly_error(e, {"phone": "13800008888"})
        check("env.recovery", "RECOVERY[CONSENT_EXPIRED]" in msg, msg)
        check("env.bind_cmd", "request_user_bind" in msg and "verify_user_bind" in msg, msg)

    # JSON-RPC 层错误
    set_raw_response({"jsonrpc": "2.0", "id": 1,
                      "error": {"code": -32602, "message": "Invalid params"}})
    try:
        gw.search_shops("cg_x")
        check("rpc.raises", False, "no exception")
    except clawdot.GatewayError as e:
        check("rpc.code", e.code == "-32602", e.code)

    # isError=True（非网关业务错）
    set_raw_response({"jsonrpc": "2.0", "id": 1,
                      "result": {"content": [{"type": "text", "text": "boom"}], "isError": True}})
    try:
        gw.search_shops("cg_x")
        check("iserr.raises", False, "no exception")
    except clawdot.GatewayError as e:
        check("iserr.code", e.code == "TOOL_ERROR", e.code)

    # AUTH envelope → api_key 提示（不落 playbook）
    msg = clawdot.friendly_error(clawdot.GatewayError(200, "AUTH_REQUIRED", "api key missing"))
    check("auth.api_key_hint", "API_KEY" in msg, msg)

    # next_action 稳定路由信号 → 绑定恢复
    msg = clawdot.friendly_error(
        clawdot.GatewayError(200, "AUTH_REQUIRED", "用户未授权", next_action="request_user_bind"))
    check("next_action.route", "RECOVERY[USER_NOT_BOUND_NEEDS_SMS]" in msg, msg)


def test_playbook_ordering_red_lines() -> None:
    # MISSING_REQUIRED_SELECTION 必须先于 MUST_PICK_REQUIRED（`必选` 过宽会吞）
    msg = clawdot.friendly_error(
        clawdot.GatewayError(400, "MISSING_REQUIRED_SELECTION", "必选商品组未选满"))
    check("order.required_group", "RECOVERY[MISSING_REQUIRED_SELECTION]" in msg, msg)
    msg = clawdot.friendly_error(clawdot.GatewayError(400, "UPSTREAM", "店铺必须商品未点"))
    check("order.must_pick", "RECOVERY[MUST_PICK_REQUIRED]" in msg, msg)

    # BELOW_MIN_PURCHASE 必须先于 BELOW_MIN_ORDER（起购 ≠ 起送）
    msg = clawdot.friendly_error(clawdot.GatewayError(400, "BELOW_MIN_PURCHASE", "低于起购份数"))
    check("order.min_purchase", "RECOVERY[BELOW_MIN_PURCHASE]" in msg, msg)
    msg = clawdot.friendly_error(clawdot.GatewayError(400, "UPSTREAM", "未达起送价"))
    check("order.min_order", "RECOVERY[BELOW_MIN_ORDER]" in msg, msg)

    # CONSENT_GRANT_INVALID 的 message 含 "expired" 也必须路由 CONSENT_INVALID
    msg = clawdot.friendly_error(
        clawdot.GatewayError(401, "CONSENT_GRANT_INVALID", "invalid or expired"))
    check("order.consent_invalid", "RECOVERY[CONSENT_INVALID]" in msg, msg)


def test_playbook_directed_coverage() -> None:
    """doc §13 外部码 + 部署实测码都要命中定向 RECOVERY（非通用兜底）。"""
    cases = [
        ("CAPABILITY_FORBIDDEN", "RECOVERY[CAP_NOT_BOUND]"),
        ("ADDRESS_REQUIRED", "RECOVERY[ADDR_MISSING]"),
        ("SHOP_UNAVAILABLE", "RECOVERY[SHOP_CLOSED]"),
        ("ITEM_UNAVAILABLE", "RECOVERY[ITEM_SOLD_OUT]"),
        ("CART_CONTEXT_EXPIRED", "RECOVERY[REFERENCE_STALE]"),
        ("CONFIRMATION_CONFLICT", "RECOVERY[IDEMPOTENCY_CONFLICT]"),
        ("ORDER_CREATE_FAILED", "RECOVERY[ORDER_GENERIC_FAIL]"),
        ("PRICE_CHANGED", "RECOVERY[PRICE_CHANGED]"),
        ("CONFIRMATION_REQUIRED", "RECOVERY[CONFIRMATION_REQUIRED]"),
        ("AUTH_EXPIRED", "RECOVERY[CONSENT_EXPIRED]"),
        ("BINDING_LIMIT_REACHED", "RECOVERY[BINDING_LIMIT_REACHED]"),
        ("PUBLIC_REFERENCE_INVALID", "RECOVERY[REFERENCE_STALE]"),
        ("ELEME_USER_NOT_FOUND", "RECOVERY[ELEME_USER_NOT_FOUND]"),
    ]
    for code, expect in cases:
        msg = clawdot.friendly_error(clawdot.GatewayError(400, code, ""))
        check(f"directed.{code}", expect in msg, msg)


# ── MG4: shared credential store ─────────────────────────────────────────────

def test_cred_store() -> None:
    home = Path(tempfile.mkdtemp()) / "home"
    creds = clawdot.CredStore("KEY_A", home)
    creds.set("13800008888", "cg_aaa", "2099-01-01T00:00:00+08:00")

    check("cred.get", creds.get("13800008888") == "cg_aaa", str(creds.get("13800008888")))
    check("cred.all", creds.all() == {"13800008888": "cg_aaa"}, str(creds.all()))

    # (key指纹, phone) 键控：另一把 key 看不到这条
    other = clawdot.CredStore("KEY_B", home)
    check("cred.key_isolated", other.get("13800008888") is None, str(other.get("13800008888")))

    # 同 key 重新加载可见（持久化）
    reload_a = clawdot.CredStore("KEY_A", home)
    check("cred.persisted", reload_a.get("13800008888") == "cg_aaa", "")

    # 权限：目录 0700、文件 0600
    dir_mode = stat.S_IMODE(os.stat(home).st_mode)
    file_mode = stat.S_IMODE(os.stat(home / "credentials.json").st_mode)
    check("cred.dir_mode", dir_mode == 0o700, oct(dir_mode))
    check("cred.file_mode", file_mode == 0o600, oct(file_mode))

    # 过期条目视同不存在
    creds.set("13900009999", "cg_old", "2000-01-01T00:00:00+08:00")
    check("cred.expired", creds.get("13900009999") is None, "")
    check("cred.expired_not_in_all", "13900009999" not in creds.all(), str(creds.all()))

    # 指纹格式 = sha256 前 12 位
    import hashlib
    check("cred.fp", creds.fingerprint == hashlib.sha256(b"KEY_A").hexdigest()[:12],
          creds.fingerprint)


def test_consent_resolution_priority() -> None:
    calls_before = len(_CALLS)

    # env 只读预注入优先
    cfg = make_config(consent_grant_id="cg_env")
    creds = fresh_creds()
    creds.set("13800008888", "cg_cached", None)
    cg = clawdot.resolve_consent_grant(None, creds, cfg)
    check("res.env_wins", cg == "cg_env", cg)

    # 无 env → 缓存唯一
    cfg = make_config()
    cg = clawdot.resolve_consent_grant(None, creds, cfg)
    check("res.unique", cg == "cg_cached", cg)

    # 多个 → 要求 --phone
    creds.set("13900009999", "cg_two", None)
    err = run_dying(clawdot.resolve_consent_grant, None, creds, cfg)
    check("res.multi_die", "--phone" in err, err)

    # --phone 命中
    cg = clawdot.resolve_consent_grant("13900009999", creds, cfg)
    check("res.phone_hit", cg == "cg_two", cg)

    # --phone miss → 引导绑定（绝不静默重绑：无出站请求）
    err = run_dying(clawdot.resolve_consent_grant, "13700007777", creds, cfg)
    check("res.miss_guides_bind", "RECOVERY[USER_NOT_BOUND_NEEDS_SMS]" in err, err)
    check("res.no_silent_rebind", len(_CALLS) == calls_before,
          f"{len(_CALLS) - calls_before} unexpected outbound calls")


# ── MG5: flow threading (cart_id / preview handoff) ──────────────────────────

MENU_PAYLOAD = {
    "shop": {"shop_id": "shop_1", "name": "测试店", "available": True},
    "categories": [{"name": "招牌", "items": [{"item_id": "item_1"}]}],
    "items": [{"item_id": "item_1", "name": "拿铁", "price": 1500, "category_name": "招牌",
               "sku_options": [{"sku_id": "sku_1", "name": "大杯"}]}],
    "total_items": 1,
}


def test_flow_threading() -> None:
    cache = fresh_cache()
    gw = clawdot.MCPClient(_CFG)
    cfg = make_config()

    # search_shops 命令：缓存 cart_id
    set_tool_response({"shops": [{"shop_id": "shop_1", "cart_id": "cart_1", "name": "测试店"}]})
    out = run_ok(clawdot.cmd_search_shops, parse(["search_shops", "--keyword", "咖啡",
                                                  "--lat", "30.1", "--lng", "120.2"]),
                 gw, cache, cfg, "cg_x", None)
    check("flow.search_out", out["shops"][0]["shop_id"] == "shop_1", str(out)[:120])

    # get_shop_menu 命令：cart_id 从缓存取回、arguments 带上
    set_tool_response(MENU_PAYLOAD)
    out = run_ok(clawdot.cmd_get_shop_menu, parse(["get_shop_menu", "--shop-id", "shop_1"]),
                 gw, cache, cfg, "cg_x", None)
    _name, args = rpc_of(last_call())
    check("flow.menu_cart", args.get("cart_id") == "cart_1", str(args))
    check("flow.menu_overview", out["shop_name"] == "测试店", str(out)[:120])

    # 商品详情视图（客户端裁剪）
    out = run_ok(clawdot.cmd_get_shop_menu,
                 parse(["get_shop_menu", "--shop-id", "shop_1", "--item-id", "item_1"]),
                 gw, cache, cfg, "cg_x", None)
    check("flow.item_detail", out["item_id"] == "item_1" and "sku_options" in out, str(out)[:150])

    # preview_order 命令：cart_id 贯穿 + items 白名单
    set_tool_response({"preview_id": "prv_1", "confirmation_token": "cf_1", "total": 1500})
    out = run_ok(clawdot.cmd_preview_order,
                 parse(["preview_order", "--shop-id", "shop_1", "--address-id", "addr_1",
                        "--items", json.dumps([{"item_id": "item_1", "quantity": 2,
                                                "sku_id": "sku_1", "extra_field": "DROP_ME"}])]),
                 gw, cache, cfg, "cg_x", None)
    _name, args = rpc_of(last_call())
    check("flow.preview_cart", args.get("cart_id") == "cart_1", str(args))
    check("flow.items_whitelist",
          args["items"] == [{"item_id": "item_1", "quantity": 2, "sku_id": "sku_1"}],
          str(args["items"]))

    # create_order 命令：preview_id + confirmation_token 交接、payment_link 提升
    set_tool_response({"order_id": "ord_1", "status": "pending_payment",
                       "payment_action": {"action_url": "https://pay.example/x"}})
    out = run_ok(clawdot.cmd_create_order,
                 parse(["create_order", "--preview-id", "prv_1",
                        "--confirmation-token", "cf_1"]),
                 gw, cache, cfg, "cg_x", None)
    _name, args = rpc_of(last_call())
    check("flow.create_handoff",
          args.get("preview_id") == "prv_1" and args.get("confirmation_token") == "cf_1",
          str(args))
    check("flow.payment_link", out.get("payment_link") == "https://pay.example/x", str(out)[:150])

    # cart 缓存 miss → SHOP_CART_MISS 定向恢复
    empty_cache = fresh_cache()
    err = run_dying(clawdot.cmd_get_shop_menu,
                    parse(["get_shop_menu", "--shop-id", "shop_unknown"]),
                    gw, empty_cache, cfg, "cg_x", None)
    check("flow.cart_miss", "RECOVERY[SHOP_CART_MISS]" in err, err)


def test_verify_bind_writes_shared_cache() -> None:
    creds = fresh_creds()
    gw = clawdot.MCPClient(_CFG)
    cfg = make_config()
    set_tool_response({"bound": True, "consent_grant_id": "cg_new",
                       "expires_at": "2099-01-01T00:00:00+08:00", "scopes": ["delivery"]})
    out = run_ok(clawdot.cmd_verify_user_bind,
                 parse(["verify_user_bind", "--phone", "13800008888",
                        "--bind-id", "bind_1", "--code", "654321"]),
                 gw, creds, cfg)
    check("bindflow.cached_flag", out.get("cached") is True, str(out)[:150])
    check("bindflow.store", creds.get("13800008888") == "cg_new", str(creds.all()))
    check("bindflow.no_env_key", "persisted_to_env" not in out, str(out)[:150])


# ── M11/M12: bind lifecycle follow-up（解绑 + env 遮蔽警告）──────────────────

def test_cred_store_delete() -> None:
    creds = fresh_creds()
    creds.set("13800008888", "cg_a", None)
    creds.set("13900009999", "cg_b", None)
    check("del.removes", creds.delete("13800008888") is True, "")
    check("del.gone", creds.get("13800008888") is None, str(creds.all()))
    check("del.other_intact", creds.get("13900009999") == "cg_b", str(creds.all()))
    check("del.unknown_false", creds.delete("13700007777") is False, "")
    reopened = clawdot.CredStore("KEY", creds.path.parent)
    check("del.persisted",
          reopened.get("13800008888") is None and reopened.get("13900009999") == "cg_b",
          str(reopened.all()))


def test_revoke_user_bind() -> None:
    gw = clawdot.MCPClient(_CFG)

    # 单用户不带 --phone：撤销缓存唯一用户 + 清条目
    creds = fresh_creds()
    creds.set("13800008888", "cg_only", None)
    set_tool_response({"revoked": True})
    out = run_ok(clawdot.cmd_revoke_user_bind, parse(["revoke_user_bind"]),
                 gw, creds, make_config())
    name, args_sent = rpc_of(last_call())
    check("rev.tool", name == "revoke_user_bind", name)
    check("rev.cg", args_sent.get("consent_grant_id") == "cg_only", str(args_sent))
    check("rev.cache_deleted",
          out.get("cache_deleted") is True and creds.get("13800008888") is None,
          str(out)[:150])

    # --phone 命中：只清目标用户
    creds = fresh_creds()
    creds.set("13800008888", "cg_a", None)
    creds.set("13900009999", "cg_b", None)
    set_tool_response({"revoked": True})
    run_ok(clawdot.cmd_revoke_user_bind,
           parse(["revoke_user_bind", "--phone", "13900009999"]), gw, creds, make_config())
    check("rev.phone_target",
          creds.get("13900009999") is None and creds.get("13800008888") == "cg_a",
          str(creds.all()))

    # 多用户不带 --phone → 要求 --phone，零出站
    creds.set("13900009999", "cg_b2", None)  # 补回第二个用户（上一步刚被定向解绑）
    calls_before = len(_CALLS)
    err = run_dying(clawdot.cmd_revoke_user_bind, parse(["revoke_user_bind"]),
                    gw, creds, make_config())
    check("rev.multi_die", "--phone" in err, err)
    check("rev.multi_no_outbound", len(_CALLS) == calls_before, "")

    # --phone 缓存 miss → die，零出站
    calls_before = len(_CALLS)
    err = run_dying(clawdot.cmd_revoke_user_bind,
                    parse(["revoke_user_bind", "--phone", "13700007777"]),
                    gw, creds, make_config())
    check("rev.miss_die", "没有缓存的绑定" in err, err)
    check("rev.miss_no_outbound", len(_CALLS) == calls_before, "")

    # env 来源：撤 env cg、缓存不动、带 warning
    creds = fresh_creds()
    creds.set("13800008888", "cg_cached", None)
    set_tool_response({"revoked": True})
    out = run_ok(clawdot.cmd_revoke_user_bind, parse(["revoke_user_bind"]),
                 gw, creds, make_config(consent_grant_id="cg_env"))
    _, args_sent = rpc_of(last_call())
    check("rev.env_cg", args_sent.get("consent_grant_id") == "cg_env", str(args_sent))
    check("rev.env_cache_intact", creds.get("13800008888") == "cg_cached", str(creds.all()))
    check("rev.env_warning", "CONSENT_GRANT_ID" in out.get("warning", ""), str(out)[:200])

    # 服务端已失效（CONSENT_*）→ 目的已达成：仍成功、照样清本地
    creds = fresh_creds()
    creds.set("13800008888", "cg_dead", None)
    set_tool_response({"error": {"code": "CONSENT_GRANT_INVALID", "message": "revoked"}})
    out = run_ok(clawdot.cmd_revoke_user_bind, parse(["revoke_user_bind"]),
                 gw, creds, make_config())
    check("rev.dead_ok",
          out.get("server_state") == "already_invalid" and out.get("cache_deleted") is True,
          str(out)[:150])
    check("rev.dead_cleared", creds.get("13800008888") is None, str(creds.all()))


def test_auth_invalid_carries_setup_url() -> None:
    """API_KEY 无效的引导必须自带注册页 URL——否则禁编造铁律下模型无处引导（线上真实卡死案例）。"""
    msg = clawdot.friendly_error(clawdot.GatewayError(401, "AUTH_INVALID", "unauthorized"))
    check("authurl.tag", "RECOVERY[API_KEY_INVALID]" in msg, msg)
    check("authurl.url", clawdot.DEFAULT_SETUP_URL in msg, msg)


def test_verify_bind_env_shadow_warning() -> None:
    gw = clawdot.MCPClient(_CFG)
    payload = {"bound": True, "consent_grant_id": "cg_new",
               "expires_at": "2099-01-01T00:00:00+08:00", "scopes": ["delivery"]}
    argv = ["verify_user_bind", "--phone", "13800008888", "--bind-id", "b1", "--code", "111111"]

    # env 残留不同值 → stdout JSON 附 warning
    set_tool_response(payload)
    out = run_ok(clawdot.cmd_verify_user_bind, parse(argv), gw, fresh_creds(),
                 make_config(consent_grant_id="cg_old_env"))
    check("shadow.warns", "CONSENT_GRANT_ID" in out.get("warning", ""), str(out)[:200])

    # 无 env → 无 warning（原输出逐字段不变）
    set_tool_response(payload)
    out = run_ok(clawdot.cmd_verify_user_bind, parse(argv), gw, fresh_creds(), make_config())
    check("shadow.absent", "warning" not in out, str(out)[:200])


# ── Run ──────────────────────────────────────────────────────────────────────

def main() -> int:
    tests = [
        test_transport_contract,
        test_url_normalization,
        test_error_envelope_and_playbook,
        test_playbook_ordering_red_lines,
        test_playbook_directed_coverage,
        test_cred_store,
        test_consent_resolution_priority,
        test_flow_threading,
        test_verify_bind_writes_shared_cache,
        test_cred_store_delete,
        test_revoke_user_bind,
        test_auth_invalid_carries_setup_url,
        test_verify_bind_env_shadow_warning,
    ]
    for t in tests:
        t()
    print(f"PASS {len(_RESULTS)} checks", file=sys.stderr)
    if _FAILS:
        print(f"FAIL {len(_FAILS)}:", file=sys.stderr)
        for f in _FAILS:
            print(f"  ✗ {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
