#!/usr/bin/env python3
"""Contract + flow tests for the open-gateway takeout script.

Runs standalone (no pytest dependency) so verify.sh can gate on it:
    python3 tests/test_takeout_gateway.py

Strategy: monkeypatch ``takeout.urlopen`` to capture the actual outbound
``Request`` (url / method / headers / body) and feed canned responses. This
validates request construction end to end — the real thing the migration must
get right (path under /api/v1, Bearer + X-Consent-Grant-Id auth, body fields,
cart_id threading, preview_id+confirmation_token order handoff)."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "takeout" / "scripts"))

import takeout  # noqa: E402


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
    base = takeout_cfg().gateway_url
    full = req.full_url
    path = full[len(base):] if full.startswith(base) else full
    # urllib capitalizes header keys (X-Consent-Grant-Id -> X-consent-grant-id);
    # expose a case-insensitive view for assertions.
    headers_ci = {k.lower(): v for k, v in req.headers.items()}
    _CALLS.append({
        "method": req.get_method(),
        "path": path,
        "headers": headers_ci,
        "body": json.loads(req.data) if req.data else None,
    })
    return _FakeResp(_NEXT["payload"])


_CFG = None


def takeout_cfg():
    return _CFG


def set_response(payload: dict) -> None:
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


def fresh_cache() -> "takeout.Cache":
    tmp = Path(tempfile.mkdtemp())
    takeout.CACHE_DIR = tmp
    takeout.CACHE_FILE = tmp / "cache.json"
    return takeout.Cache()


def parse(argv: list[str]):
    return takeout.build_parser().parse_args(argv)


# ── G3: per-method contract tests ────────────────────────────────────────────

def test_client_contract() -> None:
    gw = takeout.GatewayClient(_CFG)
    CG = "cg_test123"

    # bind/request (sms) — Bearer only, NO consent header
    set_response({"bind_id": "bind_1", "expires_in": 300, "masked_phone": "138****8888"})
    gw.request_bind("13800008888", auth_type="sms")
    c = last_call()
    check("bind_request.method", c["method"] == "POST", c["method"])
    check("bind_request.path", c["path"] == "/api/v1/auth/bind/request", c["path"])
    check("bind_request.bearer", c["headers"].get("authorization") == "Bearer KEY", str(c["headers"]))
    check("bind_request.no_consent", "x-consent-grant-id" not in c["headers"], str(c["headers"]))
    check("bind_request.body", c["body"] == {"phone": "13800008888", "auth_type": "sms"}, str(c["body"]))

    # bind/verify (sms)
    set_response({"bound": True, "consent_grant_id": "cg_x", "expires_at": None})
    gw.verify_bind(auth_type="sms", bind_id="bind_1", code="123456")
    c = last_call()
    check("bind_verify_sms.path", c["path"] == "/api/v1/auth/bind/verify", c["path"])
    check("bind_verify_sms.body",
          c["body"] == {"auth_type": "sms", "bind_id": "bind_1", "code": "123456"}, str(c["body"]))

    # bind/verify (h5)
    set_response({"bound": False, "status": "pending"})
    gw.verify_bind(auth_type="h5", request_id="h5b_1")
    c = last_call()
    check("bind_verify_h5.body",
          c["body"] == {"auth_type": "h5", "request_id": "h5b_1"}, str(c["body"]))

    # shops/search — consent header required
    set_response({"shops": []})
    gw.search_shops(CG, keyword="luckin", lat=31.2, lng=121.4)
    c = last_call()
    check("search.path", c["path"] == "/api/v1/shops/search", c["path"])
    check("search.consent", c["headers"].get("x-consent-grant-id") == CG, str(c["headers"]))
    check("search.body",
          c["body"].get("keyword") == "luckin" and c["body"].get("lat") == 31.2
          and c["body"].get("lng") == 121.4 and c["body"].get("offset") == 0, str(c["body"]))

    # shops/menu — needs shop_id + cart_id
    set_response({"shop": {}, "categories": [], "items": []})
    gw.get_shop_menu(CG, shop_id="shop_1", cart_id="cart_1")
    c = last_call()
    check("menu.path", c["path"] == "/api/v1/shops/menu", c["path"])
    check("menu.body",
          c["body"].get("shop_id") == "shop_1" and c["body"].get("cart_id") == "cart_1", str(c["body"]))

    # addresses/search
    set_response({"saved_addresses": [], "suggestions": []})
    gw.search_addresses(CG, keyword="浦东", city="上海")
    c = last_call()
    check("addr_search.path", c["path"] == "/api/v1/addresses/search", c["path"])
    check("addr_search.body",
          c["body"] == {"keyword": "浦东", "city": "上海"}, str(c["body"]))

    # addresses/select — suggestion_token + contact fields
    set_response({"address_id": "addr_1"})
    gw.select_address(CG, {"suggestion_token": "sug_x", "contact_name": "张三",
                           "contact_phone": "13800008888", "address_detail": "1栋502"})
    c = last_call()
    check("addr_select.path", c["path"] == "/api/v1/addresses/select", c["path"])
    check("addr_select.body.token", c["body"].get("suggestion_token") == "sug_x", str(c["body"]))

    # orders/preview
    set_response({"preview_id": "prv_1", "confirmation_token": "cf_1"})
    gw.preview_order(CG, {"shop_id": "shop_1", "cart_id": "cart_1", "address_id": "addr_1",
                          "items": [{"item_id": "item_1", "quantity": 1}]})
    c = last_call()
    check("preview.path", c["path"] == "/api/v1/orders/preview", c["path"])

    # orders/create — preview_id + confirmation_token
    set_response({"order_id": "ord_1", "status": "pending_payment"})
    gw.create_order(CG, preview_id="prv_1", confirmation_token="cf_1")
    c = last_call()
    check("create.path", c["path"] == "/api/v1/orders/create", c["path"])
    check("create.body",
          c["body"] == {"preview_id": "prv_1", "confirmation_token": "cf_1"}, str(c["body"]))

    # orders/{id} — GET, no body, id in path
    set_response({"order_id": "ord_1", "status": "delivering"})
    gw.get_order_status(CG, "ord_1")
    c = last_call()
    check("status.method", c["method"] == "GET", c["method"])
    check("status.path", c["path"] == "/api/v1/orders/ord_1", c["path"])
    check("status.no_body", c["body"] is None, str(c["body"]))

    # hostile order_id must be escaped into a single path segment (no path reshape)
    set_response({})
    gw.get_order_status(CG, "../auth/bind/request")
    c = last_call()
    check("status.path_escaped",
          c["path"].startswith("/api/v1/orders/") and "/auth/bind/request" not in c["path"], c["path"])

    # every business call path is under /api/v1
    check("all_paths_v1", all(c["path"].startswith("/api/v1/") for c in _CALLS),
          str([c["path"] for c in _CALLS]))


# ── G5: search → menu → preview → order flow threading ───────────────────────

def test_flow_threading() -> None:
    cache = fresh_cache()
    gw = takeout.GatewayClient(_CFG)
    captured: list = []
    orig_output = takeout.output
    takeout.output = lambda data: captured.append(data)
    try:
        # 1. search mints cart_1 for shop_1; skill must cache it per shop_id
        set_response({"shops": [{"shop_id": "shop_1", "cart_id": "cart_1",
                                 "name": "Luckin", "delivery_fee_text": "￥0",
                                 "matched_items": [{"name": "生椰拿铁"}]}]})
        takeout.action_search(parse(["--action", "search", "--shop-keyword", "luckin",
                                     "--lat", "31.2", "--lng", "121.4"]),
                              gw, cache, _CFG, "cg_flow", None)
        check("flow.cart_cached", cache.get("cart:shop_1") == "cart_1",
              str(cache.get("cart:shop_1")))

        # 2. menu --shop-id shop_1 must resolve cart_1 from cache into the request
        set_response({"shop": {"shop_id": "shop_1", "cart_id": "cart_1", "name": "Luckin"},
                      "categories": [{"name": "拿铁", "items": [{"item_id": "item_1", "name": "生椰拿铁"}]}],
                      "items": [{"item_id": "item_1", "name": "生椰拿铁", "price": 1800,
                                 "category_name": "拿铁",
                                 "sku_options": [{"sku_id": "sku_1", "name": "大杯", "price": 1800}],
                                 "ingredient_options": [{"option_id": "opt_1", "group_name": "糖度", "name": "少糖"}]}]})
        takeout.action_menu(parse(["--action", "menu", "--shop-id", "shop_1"]),
                            gw, cache, _CFG, "cg_flow", None)
        check("flow.menu_uses_cart", last_call()["body"].get("cart_id") == "cart_1",
              str(last_call()["body"]))

        # 3. preview threads cart_1 + items (new {item_id, sku_id, quantity} shape)
        set_response({"preview_id": "prv_9", "confirmation_token": "cf_9",
                      "price": {"payable_price": 1800}})
        takeout.action_preview(parse(["--action", "preview", "--shop-id", "shop_1",
                                      "--address-id", "addr_1", "--items",
                                      '[{"item_id":"item_1","quantity":2,"sku_id":"sku_1","ingredient_option_ids":["opt_1"]}]']),
                               gw, cache, _CFG, "cg_flow", None)
        pbody = last_call()["body"]
        check("flow.preview_cart", pbody.get("cart_id") == "cart_1", str(pbody))
        check("flow.preview_addr", pbody.get("address_id") == "addr_1", str(pbody))
        item0 = (pbody.get("items") or [{}])[0]
        check("flow.preview_item",
              item0.get("item_id") == "item_1" and item0.get("sku_id") == "sku_1"
              and item0.get("quantity") == 2 and item0.get("ingredient_option_ids") == ["opt_1"],
              str(item0))

        # 4. order consumes preview_id + confirmation_token; payment_action.action_url → payment_link
        set_response({"order_id": "ord_9", "status": "pending_payment",
                      "payment_action": {"action_url": "https://pay.example/x"}})
        takeout.action_order(parse(["--action", "order", "--preview-id", "prv_9",
                                    "--confirmation-token", "cf_9"]),
                             gw, cache, _CFG, "cg_flow", None)
        obody = last_call()["body"]
        check("flow.order_handoff",
              obody == {"preview_id": "prv_9", "confirmation_token": "cf_9"}, str(obody))
        check("flow.payment_link",
              captured[-1].get("payment_link") == "https://pay.example/x", str(captured[-1]))
    finally:
        takeout.output = orig_output


# ── G5b: cart-miss recovery ──────────────────────────────────────────────────

def test_cart_miss_recovery() -> None:
    cache = fresh_cache()
    gw = takeout.GatewayClient(_CFG)
    try:
        takeout.action_menu(parse(["--action", "menu", "--shop-id", "shop_unknown"]),
                            gw, cache, _CFG, "cg_x", None)
        check("cart_miss.exits", False, "expected SystemExit")
    except SystemExit:
        check("cart_miss.exits", True)


def _cfg(consent_grant_id: str = "", env_path: Path | None = None) -> "takeout.Config":
    return takeout.Config(
        gateway_url="http://test.local", api_key="KEY", consent_grant_id=consent_grant_id,
        setup_url="http://setup", default_lat=None, default_lng=None,
        redis_url=None, timeout_ms=5000,
        env_path=env_path or (Path(tempfile.mkdtemp()) / ".env"),
    )


# ── consent resolution priority: env → single cached → multiple needs --phone ──

def test_resolve_priority() -> None:
    # explicit env CONSENT_GRANT_ID wins (incl. the cg written back after a bind)
    check("resolve.env_wins",
          takeout.resolve_consent_grant(None, fresh_cache(), None, _cfg("cg_env")) == "cg_env")

    # env empty → fall back to the single bound user in cache
    c1 = fresh_cache()
    c1.set("cg:13800000001", {"consent_grant_id": "cg_one", "expires_at": None}, 3600)
    check("resolve.single_cache",
          takeout.resolve_consent_grant(None, c1, None, _cfg("")) == "cg_one")

    # env empty + multiple bound → must specify --phone (die)
    c2 = fresh_cache()
    c2.set("cg:13800000001", {"consent_grant_id": "cg_a", "expires_at": None}, 3600)
    c2.set("cg:13800000002", {"consent_grant_id": "cg_b", "expires_at": None}, 3600)
    try:
        takeout.resolve_consent_grant(None, c2, None, _cfg(""))
        check("resolve.multi_dies", False, "expected SystemExit")
    except SystemExit:
        check("resolve.multi_dies", True)


# ── bind write-back: verify_code persists cg to .env (api_key-only onboarding) ──

def test_env_writeback() -> None:
    d = Path(tempfile.mkdtemp())
    env_path = d / ".env"
    env_path.write_text("API_KEY=K\nGATEWAY_URL=http://t\n")
    cfg = _cfg("", env_path)
    captured: list = []
    orig_output = takeout.output
    takeout.output = lambda data: captured.append(data)
    try:
        set_response({"bound": True, "consent_grant_id": "cg_wb",
                      "expires_at": None, "scopes": ["delivery"]})
        takeout.action_verify_code(
            parse(["--action", "verify_code", "--phone", "13800001111",
                   "--bind-id", "bind_1", "--code", "123456"]),
            takeout.GatewayClient(cfg), fresh_cache(), cfg)
    finally:
        takeout.output = orig_output
    txt = env_path.read_text()
    check("writeback.env_has_cg", "CONSENT_GRANT_ID=cg_wb" in txt, txt)
    check("writeback.api_key_preserved", "API_KEY=K" in txt, txt)
    check("writeback.persisted_flag",
          bool(captured) and captured[-1].get("persisted_to_env") is True,
          str(captured[-1] if captured else None))
    # next run: a config that loaded that env returns the written cg with no --phone
    check("writeback.next_run_uses_env",
          takeout.resolve_consent_grant(None, fresh_cache(), None, _cfg("cg_wb", env_path)) == "cg_wb")


# ── GATEWAY_URL normalization: tolerate a trailing /api/v1 (no doubled prefix) ──

def test_gateway_url_normalization() -> None:
    n = takeout.normalize_gateway_url
    check("url.origin", n("https://eleme-gateway.hicaspian.com") == "https://eleme-gateway.hicaspian.com")
    check("url.strips_api_v1",
          n("https://eleme-gateway.hicaspian.com/api/v1") == "https://eleme-gateway.hicaspian.com")
    check("url.strips_api_v1_slash",
          n("https://eleme-gateway.hicaspian.com/api/v1/") == "https://eleme-gateway.hicaspian.com")
    # and the resulting full request URL is single-prefixed (no /api/v1/api/v1)
    gw = takeout.GatewayClient(_cfg_url("https://h/api/v1"))
    set_response({"shops": []})
    gw.search_shops("cg_x", keyword="k")
    full = last_call()["path"]  # fake_urlopen returns full URL when base differs
    check("url.single_prefix",
          full == "https://h/api/v1/shops/search" and "/api/v1/api/v1" not in full, full)


def _cfg_url(gateway_url_raw: str) -> "takeout.Config":
    return takeout.Config(
        gateway_url=takeout.normalize_gateway_url(gateway_url_raw), api_key="KEY",
        consent_grant_id="", setup_url="", default_lat=None, default_lng=None,
        redis_url=None, timeout_ms=5000, env_path=Path(tempfile.mkdtemp()) / ".env",
    )


# ── consent error mapping (live-verified): INVALID ≠ EXPIRED ──────────────────
# The gateway's CONSENT_GRANT_INVALID message is literally "invalid or expired";
# it must route to CONSENT_INVALID (a never-bound user is "not bound", not
# "expired"). CONSENT_GRANT_EXPIRED routes to CONSENT_EXPIRED.

def test_consent_error_mapping() -> None:
    invalid = takeout.friendly_error(
        takeout.GatewayError(401, "CONSENT_GRANT_INVALID", "Consent grant id is invalid or expired"))
    check("consent.invalid_not_expired",
          "RECOVERY[CONSENT_INVALID]" in invalid and "CONSENT_EXPIRED" not in invalid, invalid)
    expired = takeout.friendly_error(
        takeout.GatewayError(401, "CONSENT_GRANT_EXPIRED", "Consent grant has expired, re-authorization required"))
    check("consent.expired_maps_expired", "RECOVERY[CONSENT_EXPIRED]" in expired, expired)
    required = takeout.friendly_error(
        takeout.GatewayError(401, "CONSENT_GRANT_REQUIRED", "Consent grant id is required"))
    check("consent.required_maps_invalid", "RECOVERY[CONSENT_INVALID]" in required, required)


# ── doc v1.5 §13 error codes all map to a tailored RECOVERY (not generic) ─────

def test_doc_v15_error_codes() -> None:
    DOC = {
        "AUTH_EXPIRED": "CONSENT_EXPIRED",
        "CAPABILITY_FORBIDDEN": "CAP_NOT_BOUND",
        "BINDING_LIMIT_REACHED": "BINDING_LIMIT_REACHED",
        "ADDRESS_REQUIRED": "ADDR_MISSING",
        "DETAIL_REQUIRED": "POI_DETAIL_REQUIRED",
        "SUGGESTION_EXPIRED": "SUGGESTION_EXPIRED",
        "PUBLIC_REFERENCE_INVALID": "REFERENCE_STALE",
        "SHOP_UNAVAILABLE": "SHOP_CLOSED",
        "ITEM_UNAVAILABLE": "ITEM_SOLD_OUT",
        "MISSING_REQUIRED_SELECTION": "MISSING_REQUIRED_SELECTION",  # v1.6 店铺必选组
        "BELOW_MIN_PURCHASE": "BELOW_MIN_PURCHASE",                  # v1.7 起购份数
        "CART_CONTEXT_EXPIRED": "REFERENCE_STALE",
        "COUPON_UNAVAILABLE": "COUPON_ISSUE",
        "COUPON_CONTEXT_EXPIRED": "COUPON_ISSUE",
        "PRICE_CHANGED": "PRICE_CHANGED",
        "CONFIRMATION_REQUIRED": "CONFIRMATION_REQUIRED",
        "CONFIRMATION_CONFLICT": "IDEMPOTENCY_CONFLICT",
        "ORDER_CREATE_FAILED": "ORDER_GENERIC_FAIL",
    }
    for code, expect in DOC.items():
        msg = takeout.friendly_error(takeout.GatewayError(400, code, f"{code} doc message"))
        check(f"docerr.{code}",
              f"RECOVERY[{expect}]" in msg and "请求失败" not in msg, msg)
    # AUTH_REQUIRED carrying a binding next_action (doc semantics) → bind recovery
    m = takeout.friendly_error(
        takeout.GatewayError(401, "AUTH_REQUIRED", "用户未授权", "request_user_bind"))
    check("docerr.auth_required_via_next_action",
          "RECOVERY[USER_NOT_BOUND_NEEDS_SMS]" in m, m)
    # AUTH_INVALID without binding next_action (deployment semantics) → api_key msg
    m2 = takeout.friendly_error(takeout.GatewayError(401, "AUTH_INVALID", "API Key is invalid"))
    check("docerr.auth_invalid_apikey", "API_KEY" in m2, m2)
    # store-level required group must NOT collapse into item-internal MUST_PICK_REQUIRED,
    # and 起购份数 must NOT collapse into 起送价 (both share loose CN tokens) — see playbook order.
    mrs = takeout.friendly_error(takeout.GatewayError(400, "MISSING_REQUIRED_SELECTION", "缺少必选商品组"))
    check("docerr.required_selection_distinct",
          "RECOVERY[MISSING_REQUIRED_SELECTION]" in mrs and "MUST_PICK_REQUIRED" not in mrs, mrs)
    bmp = takeout.friendly_error(takeout.GatewayError(400, "BELOW_MIN_PURCHASE", "低于起购下限"))
    check("docerr.min_purchase_distinct",
          "RECOVERY[BELOW_MIN_PURCHASE]" in bmp and "BELOW_MIN_ORDER" not in bmp, bmp)


# ── v1.6-v1.8 menu fields surfaced (required_groups / min_purchase / available_quantity) ──

def test_menu_v18_fields() -> None:
    # build_item_detail surfaces min_purchase (>1) + available_quantity (incl. 0=sold out)
    detail = takeout.build_item_detail({
        "item_id": "item_1", "name": "经典草本骨汤", "price": 298,
        "min_purchase": 2, "available_quantity": 5,
    })
    check("item.min_purchase", detail.get("min_purchase") == 2, str(detail))
    check("item.min_purchase_hint", "min_purchase_hint" in detail, str(detail))
    check("item.available_quantity", detail.get("available_quantity") == 5, str(detail))
    sold_out = takeout.build_item_detail({"item_id": "i", "name": "x", "price": 1,
                                          "available_quantity": 0})
    check("item.available_quantity_zero", sold_out.get("available_quantity") == 0, str(sold_out))
    # min_purchase==1 (no constraint) and null available_quantity are omitted (no noise)
    d2 = takeout.build_item_detail({"item_id": "i", "name": "x", "price": 1,
                                    "min_purchase": 1, "available_quantity": None})
    check("item.min_purchase_omitted", "min_purchase" not in d2, str(d2))
    check("item.available_quantity_omitted", "available_quantity" not in d2, str(d2))

    # build_menu_overview surfaces store-level required_groups with candidates
    menu = {
        "shop": {"shop_id": "shop_1", "name": "麻辣烫", "available": True},
        "categories": [], "items": [],
        "required_groups": [{
            "name": "必选好汤", "required": True, "min_select": 1,
            "candidate_item_ids": ["item_a"],
            "candidates": [{"item_id": "item_a", "name": "经典草本骨汤",
                            "price": 298, "available": True}],
        }],
    }
    ov = takeout.build_menu_overview(menu)
    rg = ov.get("required_groups")
    check("overview.required_groups",
          isinstance(rg, list) and len(rg) == 1 and rg[0].get("name") == "必选好汤"
          and rg[0].get("min_select") == 1
          and (rg[0].get("candidates") or [{}])[0].get("item_id") == "item_a", str(ov))
    check("overview.required_groups_hint", "required_groups_hint" in ov, str(ov))
    # no required_groups → key absent (no empty noise), same in compact mode
    ov2 = takeout.build_menu_overview({"shop": {"shop_id": "s"}, "categories": [], "items": []},
                                      compact=True)
    check("overview.no_required_groups_clean", "required_groups" not in ov2, str(ov2))


def main() -> int:
    global _CFG
    _CFG = _cfg("cg_personal")
    takeout.urlopen = _fake_urlopen

    test_client_contract()
    test_flow_threading()
    test_cart_miss_recovery()
    test_resolve_priority()
    test_env_writeback()
    test_gateway_url_normalization()
    test_consent_error_mapping()
    test_doc_v15_error_codes()
    test_menu_v18_fields()

    print(f"PASS {len(_RESULTS)} checks")
    if _FAILS:
        print(f"FAIL {len(_FAILS)}:")
        for f in _FAILS:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
