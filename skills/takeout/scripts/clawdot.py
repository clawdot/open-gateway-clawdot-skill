#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ── Config ──────────────────────────────────────────────────────────────────

DEFAULT_MCP_URL = "https://eleme-gateway.hicaspian.com/mcp/v1"
DEFAULT_SETUP_URL = "https://console.hicaspian.com/login"


@dataclass
class Config:
    mcp_url: str
    api_key: str
    consent_grant_id: str
    setup_url: str
    default_lat: float | None
    default_lng: float | None
    timeout_ms: int
    clawdot_home: Path


def load_dotenv(path: Path) -> None:
    """Minimal .env loader — no dependency on python-dotenv."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_mcp_url(raw: str) -> str:
    """Normalize GATEWAY_MCP_URL to a full tools/call endpoint.

    Accepts an origin (→ append /mcp/v1), an /mcp base (→ append /v1), or a
    full endpoint (kept as-is, trailing slash stripped)."""
    url = raw.strip().rstrip("/")
    if url.endswith("/mcp"):
        return url + "/v1"
    if url.endswith("/v1"):
        return url
    return url + "/mcp/v1"


def load_config() -> Config:
    """Load config from env vars (populated by the skill's .env if present)."""
    base_dir = Path(__file__).resolve().parent.parent
    load_dotenv(base_dir / ".env")

    def to_float(key: str) -> float | None:
        v = os.environ.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except ValueError:
            return None

    return Config(
        mcp_url=normalize_mcp_url(os.environ.get("GATEWAY_MCP_URL", DEFAULT_MCP_URL)),
        api_key=os.environ.get("API_KEY", ""),
        consent_grant_id=os.environ.get("CONSENT_GRANT_ID", ""),
        setup_url=os.environ.get("CLAWDOT_SETUP_URL", DEFAULT_SETUP_URL),
        default_lat=to_float("DEFAULT_LAT"),
        default_lng=to_float("DEFAULT_LNG"),
        timeout_ms=int(os.environ.get("TIMEOUT_MS", "30000")),
        clawdot_home=Path(os.environ.get("CLAWDOT_HOME") or (Path.home() / ".clawdot")),
    )


def normalize_phone(phone: str) -> str:
    """Normalize phone into the 11-digit form (strip +86 prefix)."""
    normalized = "".join(ch for ch in phone.strip() if ch.isdigit() or ch == "+")
    if normalized.startswith("+86") and len(normalized) == 14:
        return normalized[3:]
    return normalized


def mask_phone(phone: str) -> str:
    p = normalize_phone(phone)
    return f"{p[:3]}****{p[-4:]}" if len(p) >= 7 else "***"


# ── MCP Client ──────────────────────────────────────────────────────────────

class GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str, next_action: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        # Doc v1.5 §12.3: every error may carry a next_action enum — a more stable
        # routing signal than the code string (which differs doc vs deployment).
        self.next_action = next_action


class MCPClient:
    """open-gateway MCP client — one stateless JSON-RPC tools/call per action.

    鉴权：``Authorization: Bearer <api_key>`` 总是携带；用户态调用把 cg 作为
    ``consent_grant_id`` **参数**放进 tool arguments（绑定类 tool 不带）。
    """

    def __init__(self, config: Config):
        self.url = config.mcp_url
        self.api_key = config.api_key
        self.timeout = config.timeout_ms / 1000

    def _call(self, tool: str, arguments: dict) -> dict:
        args = {k: v for k, v in arguments.items() if v is not None}
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ClawDot-CLI/2.0",
        }
        req = Request(self.url, data=json.dumps(body, ensure_ascii=False).encode(),
                      headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except HTTPError as e:
            detail = ""
            try:
                err_body = json.loads(e.read())
                err = err_body.get("error", {}) if isinstance(err_body, dict) else {}
                detail = err.get("message") or ""
            except Exception:
                pass
            if e.code in (401, 403):
                raise GatewayError(e.code, "AUTH_INVALID", detail or str(e.reason)) from None
            raise GatewayError(e.code, "HTTP_ERROR", detail or str(e.reason)) from None
        except URLError as e:
            raise GatewayError(0, "NETWORK", str(e.reason)) from None

        try:
            rpc = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise GatewayError(0, "BAD_RESPONSE", raw[:200].decode(errors="replace")) from None

        if isinstance(rpc.get("error"), dict):
            err = rpc["error"]
            raise GatewayError(200, str(err.get("code", "RPC_ERROR")),
                               err.get("message", "JSON-RPC error"))

        result = rpc.get("result") or {}
        text = ""
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                break
        if result.get("isError"):
            # 非网关业务错（网关业务错走 isError=False 的 {"error": ...} 信封）
            raise GatewayError(200, "TOOL_ERROR", text or "tool execution failed")

        data: object | None = None
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
        if data is None:
            structured = result.get("structuredContent")
            if isinstance(structured, dict):
                data = structured
        if data is None:
            raise GatewayError(200, "BAD_RESPONSE", (text or "")[:200])

        # 业务错误信封：{"error": {"code", "message"[, "next_action"]}}（isError=False）
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("code"):
                raise GatewayError(200, str(err["code"]), err.get("message", ""),
                                   err.get("next_action"))
        return data if isinstance(data, dict) else {"result": data}

    # ── 绑定（不带 consent）──────────────────────────────────────────────────

    def request_bind(self, phone: str, auth_type: str = "sms") -> dict:
        """绑定第 1 步。sms（默认）发验证码，返回 bind_id；h5 签发授权链接，返回
        request_id + h5_url。"""
        return self._call("request_user_bind", {"phone": phone, "auth_type": auth_type})

    def verify_bind(self, auth_type: str = "sms", bind_id: str | None = None,
                    code: str | None = None, request_id: str | None = None) -> dict:
        """绑定第 2 步。sms 传 bind_id+code；h5 传 request_id。
        成功返回 {"bound": true, "consent_grant_id", "scopes", "expires_at", ...}。"""
        args: dict = {"auth_type": auth_type}
        if auth_type == "h5":
            args["request_id"] = request_id
        else:
            args["bind_id"] = bind_id
            args["code"] = code
        return self._call("verify_user_bind", args)

    # ── 业务（用户态，consent 作为参数）──────────────────────────────────────

    def get_auth_status(self, cg: str) -> dict:
        return self._call("get_user_auth_status", {"consent_grant_id": cg})

    def revoke_bind(self, cg: str) -> dict:
        """解绑：服务端立即作废该 cg（地址/订单史保留，重绑同号可恢复）。"""
        return self._call("revoke_user_bind", {"consent_grant_id": cg})

    def search_shops(self, cg: str, *, keyword: str | None = None,
                     lat: float | None = None, lng: float | None = None,
                     city: str | None = None, address_id: str | None = None,
                     offset: int = 0) -> dict:
        return self._call("search_shops", {
            "consent_grant_id": cg, "keyword": keyword, "address_id": address_id,
            "lat": lat, "lng": lng, "city": city, "offset": offset,
        })

    def get_shop_menu(self, cg: str, *, shop_id: str, cart_id: str,
                      address_id: str | None = None, lat: float | None = None,
                      lng: float | None = None, keyword: str | None = None,
                      limit: int | None = None, offset: int = 0) -> dict:
        return self._call("get_shop_menu", {
            "consent_grant_id": cg, "shop_id": shop_id, "cart_id": cart_id,
            "address_id": address_id, "lat": lat, "lng": lng,
            "keyword": keyword, "limit": limit, "offset": offset,
        })

    def get_item_options(self, cg: str, *, cart_id: str, items: list[dict]) -> dict:
        return self._call("get_item_options", {
            "consent_grant_id": cg, "cart_id": cart_id, "items": items,
        })

    def search_addresses(self, cg: str, *, keyword: str | None = None,
                         lat: float | None = None, lng: float | None = None,
                         city: str | None = None) -> dict:
        return self._call("search_addresses", {
            "consent_grant_id": cg, "keyword": keyword,
            "lat": lat, "lng": lng, "city": city,
        })

    def select_address(self, cg: str, *, contact_name: str, contact_phone: str,
                       suggestion_token: str | None = None,
                       address_id: str | None = None,
                       address_detail: str = "", tag: str | None = None) -> dict:
        return self._call("select_address", {
            "consent_grant_id": cg, "contact_name": contact_name,
            "contact_phone": contact_phone, "suggestion_token": suggestion_token,
            "address_id": address_id, "address_detail": address_detail, "tag": tag,
        })

    def update_address(self, cg: str, *, address_id: str,
                       suggestion_token: str | None = None,
                       address_detail: str | None = None,
                       tag: str | None = None) -> dict:
        return self._call("update_address", {
            "consent_grant_id": cg, "address_id": address_id,
            "suggestion_token": suggestion_token,
            "address_detail": address_detail, "tag": tag,
        })

    def preview_order(self, cg: str, *, shop_id: str, cart_id: str,
                      address_id: str, items: list[dict],
                      order_remark: str = "") -> dict:
        return self._call("preview_order", {
            "consent_grant_id": cg, "shop_id": shop_id, "cart_id": cart_id,
            "address_id": address_id, "items": items, "order_remark": order_remark,
        })

    def create_order(self, cg: str, *, preview_id: str, confirmation_token: str,
                     payment_method: str | None = None) -> dict:
        return self._call("create_order", {
            "consent_grant_id": cg, "preview_id": preview_id,
            "confirmation_token": confirmation_token, "payment_method": payment_method,
        })

    def get_order_status(self, cg: str, order_id: str) -> dict:
        return self._call("get_order_status", {
            "consent_grant_id": cg, "order_id": order_id,
        })


# ── Operational file cache（搜索/菜单/cart_id/地址；非凭据）──────────────────

CACHE_DIR = Path.home() / ".cache" / "clawdot-takeout"
CACHE_FILE = CACHE_DIR / "cache.json"


class Cache:
    def __init__(self) -> None:
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not CACHE_FILE.is_file():
            return
        try:
            raw = json.loads(CACHE_FILE.read_text())
            if isinstance(raw, dict):
                self._data = raw
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        CACHE_FILE.write_text(json.dumps(self._data, ensure_ascii=False))
        try:
            os.chmod(CACHE_FILE, 0o600)
        except OSError:
            pass

    def get(self, key: str) -> object | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.time() > entry.get("expires_at", 0):
            del self._data[key]
            self._save()
            return None
        return entry["data"]

    def set(self, key: str, data: object, ttl_seconds: float) -> None:
        self._data[key] = {"data": data, "expires_at": time.time() + ttl_seconds}
        self._prune()
        self._save()

    def delete(self, key: str) -> None:
        if key in self._data:
            del self._data[key]
            self._save()

    def _prune(self) -> None:
        now = time.time()
        expired = [k for k, v in self._data.items() if now > v.get("expires_at", 0)]
        for k in expired:
            del self._data[k]


# ── Shared credential store（DECISIONS M4）──────────────────────────────────
#
# cg 唯一持久化源：$CLAWDOT_HOME/credentials.json（默认 ~/.clawdot/）。
# 结构：{ "<sha256(API_KEY)[:12]>": { "<phone>": {"consent_grant_id", "expires_at",
# "updated_at"} } }。同实例多 skill 共用同一 consent 不互踢；跨实例（不同 key）
# 天然隔离；skill 升级/重装不丢绑定。不回写 .env（CONSENT_GRANT_ID env 只读）。

class CredStore:
    def __init__(self, api_key: str, home: Path):
        self.path = home / "credentials.json"
        self.fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:12]
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text())
            if isinstance(raw, dict):
                self._data = raw
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self) -> None:
        # credentials.json 存 bearer 等价凭证 → 目录 0700、文件 0600。
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _expired(entry: dict) -> bool:
        expires_at = entry.get("expires_at")
        if not expires_at:
            return False  # 无过期信息 → 交给服务端裁决（CONSENT_* 错误会引导重绑）
        try:
            dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            return dt.timestamp() < time.time()
        except (ValueError, TypeError):
            return False

    def get(self, phone: str) -> str | None:
        entry = self._data.get(self.fingerprint, {}).get(phone)
        if not isinstance(entry, dict) or self._expired(entry):
            return None
        return entry.get("consent_grant_id") or None

    def set(self, phone: str, consent_grant_id: str, expires_at: str | None) -> None:
        bucket = self._data.setdefault(self.fingerprint, {})
        bucket[phone] = {
            "consent_grant_id": consent_grant_id,
            "expires_at": expires_at,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self._save()

    def delete(self, phone: str) -> bool:
        """Remove the cached grant for phone under this API_KEY. True if removed."""
        bucket = self._data.get(self.fingerprint)
        if not isinstance(bucket, dict) or phone not in bucket:
            return False
        del bucket[phone]
        if not bucket:
            del self._data[self.fingerprint]
        self._save()
        return True

    def all(self) -> dict[str, str]:
        """{phone: cg} for all non-expired bound users under this API_KEY."""
        out: dict[str, str] = {}
        for phone, entry in self._data.get(self.fingerprint, {}).items():
            if isinstance(entry, dict) and not self._expired(entry):
                cg = entry.get("consent_grant_id")
                if cg:
                    out[phone] = cg
        return out


# ── Consent Grant Resolution（DECISIONS M4 优先级）───────────────────────────

def resolve_consent_grant(phone: str | None, creds: CredStore, config: Config) -> str:
    """Return the consent_grant_id (cg_) for the call.

    不带 --phone：``CONSENT_GRANT_ID`` env（只读预注入）→ 共享缓存中该 API_KEY
    指纹下唯一已绑用户 → 多个要求 --phone → 否则引导绑定。
    带 --phone：查共享缓存该手机号。缓存 miss → 引导 SMS/H5 绑定（**绝不静默重绑**：
    重绑会轮换作废旧 cg，必须由用户走 request_user_bind → verify_user_bind）。"""
    if phone is None:
        if config.consent_grant_id:
            return config.consent_grant_id
        bound = creds.all()
        if len(bound) == 1:
            return next(iter(bound.values()))
        if len(bound) > 1:
            die("已绑定多个用户，无法确定用哪个。请在调用时带 --phone <11位> 指定用户。")
        die_with_hint(
            "还没绑定用户。先问用户选哪种授权方式：短信验证码（默认）或打开链接授权（H5）；用户不选就走短信。",
            "USER_NOT_BOUND_NEEDS_SMS",
        )
        return ""  # unreachable

    norm = normalize_phone(phone)
    cg = creds.get(norm)
    if cg:
        return cg
    die_with_hint(
        f"用户 {norm} 还没绑定。先问用户选哪种授权方式：短信验证码（默认）或打开链接授权（H5）；"
        f"用户不选就走短信。",
        "USER_NOT_BOUND_NEEDS_SMS",
        ctx={"phone": norm},
    )
    return ""  # unreachable (die_with_hint exits)


# ── Response Trimmers ───────────────────────────────────────────────────────

def trim_search_results(raw: dict) -> dict:
    """Trim search_shops response into a compact shop list.

    Keeps cart_id so the caller can cache it per shop (menu/preview need it)."""
    shops = []
    for s in raw.get("shops", []):
        if not isinstance(s, dict):
            continue
        shops.append({
            "shop_id": s.get("shop_id"),
            "cart_id": s.get("cart_id"),
            "name": s.get("name", ""),
            "brand_name": s.get("brand_name"),
            "rating": s.get("rating"),
            "delivery_fee": s.get("delivery_fee_text"),
            "delivery_time": s.get("delivery_time_text"),
            "min_order_amount": s.get("min_order_amount"),
            "distance": s.get("distance_text"),
            "monthly_sales": s.get("monthly_sales_text"),
            "available": s.get("available", True),
            "unavailable_reason": s.get("unavailable_reason"),
            "tags": s.get("tags") or [],
            "highlights": [
                i.get("name") for i in (s.get("matched_items") or [])[:2]
                if isinstance(i, dict) and i.get("name")
            ],
        })
    result: dict = {"shops": shops, "count": len(shops)}
    if raw.get("next_offset") is not None:
        result["next_offset"] = raw["next_offset"]
    return result


def _item_overview(item: dict) -> dict:
    return {
        "item_id": item.get("item_id"),
        "name": item.get("name"),
        "price": item.get("price"),
        "available": item.get("available", True),
        "has_skus": len(item.get("sku_options") or []) > 0,
        "has_ingredients": len(item.get("ingredient_options") or []) > 0,
    }


def _trim_required_groups(menu: dict) -> list[dict]:
    """Store-level required item groups (doc v1.6, e.g. 麻辣烫「必选好汤」): the whole
    order must include ≥min_select from each group's candidates, else preview_order
    rejects with MISSING_REQUIRED_SELECTION (HTTP 400). Surface them up front so the
    agent lets the user pick a candidate instead of dead-ending at preview. Distinct
    from an item-internal required option group (那走 MUST_PICK_REQUIRED)."""
    groups: list[dict] = []
    for g in menu.get("required_groups", []) or []:
        if not isinstance(g, dict):
            continue
        candidates = [
            {"item_id": c.get("item_id"), "name": c.get("name"),
             "price": c.get("price"), "available": c.get("available", True)}
            for c in (g.get("candidates") or []) if isinstance(c, dict)
        ]
        groups.append({
            "name": g.get("name"),
            "min_select": g.get("min_select", 1),
            "candidates": candidates,
        })
    return groups


def build_menu_overview(menu: dict, compact: bool = False) -> dict:
    """Build a category overview from a shop menu response.

    compact=True (recommend): skip ¥0-only categories, top 2 items each, ≤5 cats."""
    shop = menu.get("shop", {})
    items_by_id = {it.get("item_id"): it for it in menu.get("items", []) if isinstance(it, dict)}

    categories = []
    for cat in menu.get("categories", []):
        if not isinstance(cat, dict):
            continue
        cat_items = [items_by_id.get(ci.get("item_id"), ci) for ci in cat.get("items", [])
                     if isinstance(ci, dict)]
        if compact:
            real = [it for it in cat_items if (it.get("price") or 0) > 0]
            if not real:
                continue
            cat_items = real
        top = [_item_overview(it) for it in cat_items[:2 if compact else 3]]
        categories.append({
            "name": cat.get("name", ""),
            "item_count": len(cat.get("items", [])),
            "top_items": top,
        })

    if compact:
        categories = categories[:5]

    result: dict = {
        "shop_id": shop.get("shop_id"),
        "shop_name": shop.get("name", ""),
        "available": shop.get("available", True),
        "categories": categories,
        "total_items": menu.get("total_items"),
    }
    required_groups = _trim_required_groups(menu)
    if required_groups:
        result["required_groups"] = required_groups
        result["required_groups_hint"] = (
            "这家店有店铺级必选组：整单必须从每组 candidates 里按 min_select 选够商品，"
            "作为普通商品加进下单 items[]（可连同规格/加料一起）。列给用户选，别替用户做主。"
        )
    return result


def build_category_detail(menu: dict, category: str) -> dict | None:
    """Filter menu items by category name (exact → index → substring)."""
    cat_names = [c.get("name", "") for c in menu.get("categories", []) if isinstance(c, dict)]
    target = None
    if category in cat_names:
        target = category
    else:
        try:
            idx = int(category)
            if 0 <= idx < len(cat_names):
                target = cat_names[idx]
        except ValueError:
            pass
        if target is None:
            for name in cat_names:
                if category in name:
                    target = name
                    break
    if target is None:
        return None
    items = [it for it in menu.get("items", [])
             if isinstance(it, dict) and (it.get("category_name") or "") == target]
    return {"category": target, "items": [build_item_detail(it) for it in items]}


def search_menu_items(menu: dict, keyword: str) -> dict:
    """Search across all menu items by name (client-side over the cached menu)."""
    kw = keyword.lower()
    hits = [build_item_detail(it) for it in menu.get("items", [])
            if isinstance(it, dict) and kw in str(it.get("name", "")).lower()]
    return {"keyword": keyword, "matches": hits, "count": len(hits)}


def build_item_detail(item: dict) -> dict:
    """Full single-item detail. sku_options carry public sku_id; ingredient_options
    carry public option_id — exactly what the agent passes back to preview."""
    detail = {
        "item_id": item.get("item_id"),
        "name": item.get("name"),
        "price": item.get("price"),
        "original_price": item.get("original_price"),
        "available": item.get("available", True),
        "unavailable_reason": item.get("unavailable_reason"),
        "category_name": item.get("category_name"),
        "description": item.get("description"),
    }
    # min_purchase (doc v1.7): 起购份数，≥1（1=无约束）。>1 时下单 quantity 必须达标，
    # 否则 preview 报 BELOW_MIN_PURCHASE——提前透出让 agent 把量提够。
    min_purchase = item.get("min_purchase")
    if isinstance(min_purchase, int) and min_purchase > 1:
        detail["min_purchase"] = min_purchase
        detail["min_purchase_hint"] = (
            f"起购 {min_purchase} 份：下单 quantity 必须 ≥ {min_purchase}，否则 preview 报 BELOW_MIN_PURCHASE。"
        )
    # available_quantity (doc v1.8): 库存余量（份）。0=售罄、正整数=剩余可购、null=充足或未知。
    aq = item.get("available_quantity")
    if aq is not None:
        detail["available_quantity"] = aq
    sku_options = item.get("sku_options") or []
    if sku_options:
        detail["sku_options"] = sku_options
        detail["sku_resolution_hint"] = (
            "多规格商品：下单时从 sku_options[].sku_id 取对应规格（杯型/份量）的 sku_id；"
            "不传 sku_id 则用默认 SKU。"
        )
    ingredient_options = item.get("ingredient_options") or []
    if ingredient_options:
        detail["ingredient_options"] = ingredient_options
        detail["ingredient_hint"] = (
            "加料/属性：把用户选中项的 option_id 放进下单 items[].ingredient_option_ids；"
            "selected_by_default=true 的是默认项。"
        )
    return detail


def normalize_saved_address(a: dict) -> dict:
    """Coerce lat/lng to float (gateway may return strings/null)."""
    out = dict(a)
    lat, lng = a.get("lat"), a.get("lng")
    out["lat"] = float(lat) if lat is not None else None
    out["lng"] = float(lng) if lng is not None else None
    return out


def normalize_address_search(raw: dict) -> dict:
    """Normalize search_addresses: float-coerce saved, rename suggestion token →
    sug_ref so a host's secret-redaction layer doesn't mask the handle by keyword."""
    saved = [normalize_saved_address(a) for a in raw.get("saved_addresses", [])
             if isinstance(a, dict) and a.get("address_id")]
    suggestions = []
    for s in raw.get("suggestions", []):
        if not isinstance(s, dict):
            continue
        s = dict(s)
        if "token" in s:
            s["sug_ref"] = s.pop("token")
        suggestions.append(s)
    out: dict = {"saved": saved, "suggestions": suggestions}
    if raw.get("nearest_address_id") is not None:
        out["nearest_address_id"] = raw["nearest_address_id"]
    return out


# ── Error Handling ──────────────────────────────────────────────────────────
#
# Each entry: (regex, code, user_message, recovery_hint_template). The regex
# matches against the gateway error.code OR error.message, so both structured
# open-gateway codes (CONSENT_GRANT_EXPIRED, PUBLIC_REFERENCE_INVALID, ...) and
# upstream business messages (起送/打烊/售罄) route to the same playbook.

ERROR_PLAYBOOK: list[tuple[str, str, str, str]] = [
    # 店铺级必选组（doc v1.6）：整单必须再点一个商品（如麻辣烫「必选好汤」）。
    # 放在 MUST_PICK_REQUIRED 之前——它的 `必选` 太宽会先吞掉这条；靠 code 精确命中。
    (r"MISSING_REQUIRED_SELECTION|必选商品组|必选组.*未选|缺.*必选组",
     "MISSING_REQUIRED_SELECTION",
     "店铺必选商品组未选满。",
     "这家店整单必须从某个必选组选够（如麻辣烫「必选好汤」，跟商品内部的加料必选组是两回事）。"
     "get_shop_menu --shop-id {shop_id} 看返回的 required_groups[]，从每组 candidates 里按 min_select 选够商品，"
     "作为普通商品加进 items[] 再 preview_order。让用户选具体哪个，禁止替用户做主。"),

    # 商品内部必选做法组（如必选温度/糖度）：某商品自己内部必须选够 option。
    (r"店铺必须商品未点|必选商品未点|必须先购买|必选",
     "MUST_PICK_REQUIRED",
     "店铺要求必选项未点。",
     "get_shop_menu --shop-id {shop_id} 查看商品的 ingredient_options（带 group_name 的加料/必选组），"
     "把用户选中项的 option_id 放进 items[].ingredient_option_ids 重 preview_order。"
     "**禁止替用户做主**——口味/规格类让用户选，别自动选。"),

    (r"COORDS_REQUIRED|ADDRESS_REQUIRED|无法确定.*位置|需要地址|缺.*坐标|缺少收货地址",
     "ADDR_MISSING",
     "缺用户坐标。",
     "直接问用户'你这会儿在哪边呀？地址直接说就行～'，拿到后 "
     "search_addresses --keyword '<用户给的地址>' --city '<推断或问用户>'。禁止用任何默认坐标。"),

    (r"DETAIL_REQUIRED|这个地址是新地点",
     "POI_DETAIL_REQUIRED",
     "POI 地址需要门牌号。",
     "问用户'几号楼几层几室？'，拿到后 "
     "select_address --sug-ref <sug_ref> --contact-name --contact-phone --address-detail '<具体内容>' 重试。"
     "门牌不能传'无'/空格。"),

    (r"CONTACT_REQUIRED|缺少收件人",
     "CONTACT_REQUIRED",
     "缺收件人姓名/手机号。",
     "问'收件人写谁？手机就用你这个 {phone_masked} 行吗？'，"
     "拿到后 select_address --sug-ref --contact-name --contact-phone 重试。"),

    (r"SUGGESTION_EXPIRED|地址候选已过期",
     "SUGGESTION_EXPIRED",
     "地址 sug_ref 已过期。",
     "search_addresses --keyword '<用户原话地址>' 重拿新 sug_ref，再 select_address。"),

    (r"PUBLIC_REFERENCE_INVALID|CART_CONTEXT_EXPIRED|cart_id|shop_id and cart_id|购物车上下文|未找到.*商品|未在.*菜单",
     "REFERENCE_STALE",
     "店铺/商品/购物车引用已失效。",
     "shop_id 或 item_id 已过期（菜单上下文有 TTL）。重新 search_shops/recommend 拿新 shop_id，"
     "再 get_shop_menu --shop-id {shop_id} 拿新 item_id / sku_id / option_id，然后重 preview_order。"
     "禁止跨店复用 item_id；禁止把中文菜名当 item_id 传。"),

    (r"SHOP_CART_MISS",
     "SHOP_CART_MISS",
     "缺该店购物车上下文。",
     "get_shop_menu/preview_order 需要先搜到这家店拿到上下文。先 search_shops --keyword '<店名/品类>' "
     "（或 recommend），再用返回的 shop_id 重试本次操作。"),

    (r"地址超过.*配送范围|不在配送范围|请重新选择地址后下单|配送范围",
     "OUT_OF_RANGE",
     "店铺不送当前地址。",
     "保留地址，recommend --keyword '<同品类>' --lat --lng --top-n 4 推荐其他店；"
     "或告诉用户'这家不送你这边，换家行不'。禁止换地址重试，禁止用同 shop_id 重 preview_order。"),

    # 单品起购份数不足（doc v1.7）——与「整单未达起送价」是两码事，放前面精确命中。
    (r"BELOW_MIN_PURCHASE|低于起购|起购份数|起购下限",
     "BELOW_MIN_PURCHASE",
     "商品数量低于起购份数。",
     "该商品有起购份数要求（get_shop_menu 商品详情里的 min_purchase）。把对应商品的 quantity 提到 "
     "min_purchase 及以上重 preview_order；或让用户换个无起购限制的商品。加量/加钱先跟用户说一声。"),

    (r"min order|minimum|未达起送价|起送",
     "BELOW_MIN_ORDER",
     "未达起送价。",
     "get_shop_menu --shop-id {shop_id} 翻菜单挑 1-2 个低价单品（饮料/小食），"
     "或告诉用户差多少让用户决定加什么。涉及花钱必须用户点头。"),

    (r"closed|not open|SHOP_UNAVAILABLE|SHOP_NOT_FOUND|店铺.*打烊|休息|未营业|店铺不可下单",
     "SHOP_CLOSED",
     "店铺暂未营业。",
     "recommend --keyword '<同品类>' --lat --lng 推同类其他店。不要重试同店。"),

    (r"out of stock|sold out|ITEM_UNAVAILABLE|售罄|缺货|商品不可购买",
     "ITEM_SOLD_OUT",
     "部分商品已售罄。",
     "get_shop_menu --shop-id {shop_id} 找同款替代（同分类下其他 item），拿替代款给用户确认后再 "
     "preview_order。不要自动替换。"),

    (r"PRICE_CHANGED|价格.*变",
     "PRICE_CHANGED",
     "价格发生变化。",
     "用同样的 shop_id/address_id/items 重新 preview_order 拿最新价格 + 新的 preview_id/confirmation_token，"
     "向用户确认新价后再 create_order。"),

    (r"CONFIRMATION_REQUIRED|缺少用户确认令牌",
     "CONFIRMATION_REQUIRED",
     "缺确认令牌。",
     "create_order 必须带 preview_order 返回的 --preview-id 和 --confirmation-token；缺了就先 preview_order "
     "再 create_order。"),

    (r"COUPON_UNAVAILABLE|COUPON_CONTEXT_EXPIRED|优惠券.*不可用|优惠券上下文",
     "COUPON_ISSUE",
     "优惠券不可用或已过期。",
     "重新 preview_order（不带该券）拿当前可用券与价格；让用户重选或不用券后再 create_order。"),

    (r"ORDER_FAILED|ORDER_CREATE_FAILED|ELEME_ERROR|Order render failed|Order creation failed|创建订单失败",
     "ORDER_GENERIC_FAIL",
     "订单创建/预览失败。",
     "get_shop_menu --shop-id {shop_id} 重看商品状态（是否下架），逐项核对 item_id/sku_id 后重 preview_order。"
     "如多次失败，告诉用户换家或调整组合。"),

    (r"IDEMPOTENCY_CONFLICT|CONFIRMATION_CONFLICT|确认令牌已被",
     "IDEMPOTENCY_CONFLICT",
     "下单参数与已用确认凭证不一致。",
     "confirmation_token 已被另一组参数消费。用同样的 shop_id/address_id/items 重新 preview_order 拿新的 "
     "preview_id + confirmation_token，再 create_order。"),

    # Match the EXPIRED *code* only — NOT a generic "expired" in the message:
    # CONSENT_GRANT_INVALID's message is "invalid or expired", which must route to
    # CONSENT_INVALID below (a never-bound user is "not bound", not "expired").
    (r"CONSENT_GRANT_EXPIRED|AUTH_EXPIRED|授权.*过期",
     "CONSENT_EXPIRED",
     "用户授权已过期。",
     "引导该用户重新授权：request_user_bind --phone {phone} → verify_user_bind（短信），"
     "或 request_user_bind --auth-type h5 --phone {phone} → verify_user_bind --auth-type h5（H5）。"
     "重绑后重试原命令。"),

    (r"CONSENT_GRANT_INVALID|CONSENT_GRANT_REQUIRED|CONSENT_GRANT_WRONG_CAP",
     "CONSENT_INVALID",
     "用户授权凭证无效或缺失。",
     "预注入模式检查 CONSENT_GRANT_ID 环境变量；多用户模式带 --phone 且该手机号已绑定。"
     "未绑定就走 request_user_bind → verify_user_bind 先拿凭证。"),

    (r"ELEME_USER_NOT_FOUND",
     "ELEME_USER_NOT_FOUND",
     "该手机号没有可绑定的淘宝闪购/饿了么账号。",
     "告诉用户：先用该手机号登录或开通淘宝闪购/饿了么后再绑定。换个已开通的手机号也行。"),

    (r"CAP_NOT_BOUND|CAPABILITY_FORBIDDEN|PROVIDER_NOT_AVAILABLE",
     "CAP_NOT_BOUND",
     "该 agent 未开通外卖能力。",
     "这是平台侧配置：联系 ClawDot 平台为该 API_KEY 开通 delivery 能力后再用。不是用户能自助解决的。"),

    (r"BINDING_LIMIT_REACHED",
     "BINDING_LIMIT_REACHED",
     "该 agent 已达可绑定用户数上限。",
     "平台侧配额问题：先对某个已绑用户走解绑（或联系 ClawDot 提升 max_bindings 配额）再绑新用户。"),

    (r"还没绑定|USER_NOT_BOUND",
     "USER_NOT_BOUND_NEEDS_SMS",
     "用户还未完成授权绑定。",
     "把手机号和方式合成一句问：'先告诉我手机号，顺便选一下用 H5 还是验证码方式绑定哦～'"
     "（已知手机号就只问方式；不选默认短信）。\n"
     "短信：request_user_bind --phone {phone} → 用户回 6 位码 → "
     "verify_user_bind --phone {phone} --bind-id <真实bind_id> --code <用户的码>。\n"
     "H5：request_user_bind --auth-type h5 --phone {phone} → 把返回的 h5_url 原样发给用户点开授权 → "
     "用户说完成后 verify_user_bind --auth-type h5 --phone {phone} --request-id <真实request_id>。\n"
     "绑定成功后重调原业务命令并带 --phone。bind_id/request_id 必须来自真实返回，禁止编造。"),
]


_PLACEHOLDER_DEFAULTS = {
    "shop_id": "<shop_id>",
    "address_id": "<address_id>",
    "phone_masked": "<手机号>",
    "phone": "<11位手机号>",
    "keyword": "<keyword>",
}


def _format_recovery(template: str, ctx: dict | None) -> str:
    merged = {**_PLACEHOLDER_DEFAULTS, **(ctx or {})}
    try:
        return template.format(**merged)
    except (KeyError, IndexError):
        return template


def _lookup_by_code(code: str) -> tuple[str, str, str] | None:
    for _pat, c, user_msg, hint in ERROR_PLAYBOOK:
        if c == code:
            return c, user_msg, hint
    return None


def _lookup_by_pattern(raw: str) -> tuple[str, str, str] | None:
    for pattern, code, user_msg, hint in ERROR_PLAYBOOK:
        if re.search(pattern, raw, re.IGNORECASE):
            return code, user_msg, hint
    return None


def friendly_error(err: GatewayError, ctx: dict | None = None) -> str:
    """Translate a gateway error into a user-facing line + a RECOVERY hint.

    Matches against both error.code and error.message so structured codes and
    upstream business messages both route to the playbook."""
    # Doc v1.5 §12.3: error.next_action is the stable routing signal. A binding
    # next_action means the user must (re)authorize — route there regardless of the
    # code string (handles doc's AUTH_REQUIRED="用户未授权" vs deployment's
    # AUTH_REQUIRED="api key missing").
    if (err.next_action or "") in ("request_user_bind", "request_bind", "verify_user_bind"):
        found = _lookup_by_code("USER_NOT_BOUND_NEEDS_SMS")
        if found:
            _c, user_msg, hint = found
            return f"{user_msg}\nRECOVERY[USER_NOT_BOUND_NEEDS_SMS]: {_format_recovery(hint, ctx)}"

    if err.code in ("AUTH_REQUIRED", "AUTH_INVALID"):
        # 注册页 URL 必须随错误一起给出——否则模型无处可引导（禁编造铁律下它只能卡死）。
        setup_url = os.environ.get("CLAWDOT_SETUP_URL", DEFAULT_SETUP_URL)
        return ("API_KEY 无效或缺失。\n"
                "RECOVERY[API_KEY_INVALID]: 检查/更换 .env 里的 API_KEY（clw_）；"
                f"还没有 key 就把注册页原样发给用户去获取：{setup_url} "
                "（拿到后写入 .env，不要复述展示 key）。")

    haystack = f"{err.code} {err.args[0] if err.args else ''}"
    matched = _lookup_by_pattern(haystack)
    if matched:
        code, user_msg, hint = matched
        return f"{user_msg}\nRECOVERY[{code}]: {_format_recovery(hint, ctx)}"
    raw = err.args[0] if err.args else err.code
    return f"请求失败：{raw}"


def die_with_hint(user_msg: str, code: str, ctx: dict | None = None,
                  extra: dict | None = None) -> None:
    """Emit a die() with a RECOVERY hint (looked up by code) appended.

    ``extra`` is an optional JSON block placed BEFORE the RECOVERY line so the
    LLM reads actionable structured data first."""
    parts: list[str] = [user_msg]
    if extra is not None:
        parts.append(json.dumps(extra, ensure_ascii=False))
    found = _lookup_by_code(code)
    if found:
        _c, _u, hint = found
        parts.append(f"RECOVERY[{code}]: {_format_recovery(hint, ctx)}")
    die("\n".join(parts))


# ── Cache keys & coords ─────────────────────────────────────────────────────

SEARCH_TTL = 5 * 60       # 5 minutes
MENU_TTL = 10 * 60        # 10 minutes
ADDRESS_TTL = 30 * 60     # 30 minutes
CART_TTL = 25 * 60        # 25 minutes (under the gateway cart TTL of 30 min)


def _addr_cache_key(phone: str | None) -> str:
    return f"addr:{phone}" if phone else "addr:user"


def _cart_cache_key(shop_id: str) -> str:
    return f"cart:{shop_id}"


def _menu_cache_key(cart_id: str) -> str:
    return f"menu:{cart_id}"


def remember_carts(cache: Cache, shops: list[dict]) -> None:
    """Cache cart_id per public shop_id so menu/preview can resolve it without
    the agent threading cart_id through the CLI."""
    for s in shops:
        sid, cid = s.get("shop_id"), s.get("cart_id")
        if sid and cid:
            cache.set(_cart_cache_key(sid), cid, CART_TTL)


def resolve_cart_id(cache: Cache, shop_id: str) -> str:
    cid = cache.get(_cart_cache_key(shop_id))
    if not cid:
        die_with_hint(
            f"店铺 {shop_id} 没有购物车上下文（未搜索过或已过期）。",
            "SHOP_CART_MISS",
            {"shop_id": shop_id},
        )
    return cid  # type: ignore[return-value]


def get_cached_address_coords(cache: Cache, phone: str | None) -> tuple[float | None, float | None]:
    addrs = cache.get(_addr_cache_key(phone))
    if isinstance(addrs, list) and addrs:
        first = addrs[0]
        return first.get("lat"), first.get("lng")
    return None, None


def _resolve_lat_lng(args: argparse.Namespace, cache: Cache, config: Config,
                     phone: str | None) -> tuple[float | None, float | None]:
    """Resolve lat/lng from CLI args > address cache > DEFAULT_* (no-phone only)."""
    if args.lat is not None and args.lng is not None:
        return args.lat, args.lng
    cached_lat, cached_lng = get_cached_address_coords(cache, phone)
    if cached_lat is not None and cached_lng is not None:
        return cached_lat, cached_lng
    if phone is None:
        return config.default_lat, config.default_lng
    return None, None


def _refresh_saved_cache(cache: Cache, phone: str | None, saved: list[dict]) -> None:
    key = _addr_cache_key(phone)
    if saved:
        cache.set(key, saved, ADDRESS_TTL)
    else:
        cache.delete(key)


# ── Commands ────────────────────────────────────────────────────────────────

def cmd_search_shops(args, gw: MCPClient, cache: Cache, config: Config,
                     cg: str, phone: str | None) -> None:
    lat, lng = _resolve_lat_lng(args, cache, config, phone)
    cache_key = f"search:{lat},{lng},{args.keyword or 'default'}"
    cached = cache.get(cache_key)
    if cached:
        output(cached)
        return
    raw = gw.search_shops(cg, keyword=args.keyword, lat=lat, lng=lng, city=args.city)
    trimmed = trim_search_results(raw)
    remember_carts(cache, trimmed["shops"])
    cache.set(cache_key, trimmed, SEARCH_TTL)
    output(trimmed)


def cmd_recommend(args, gw: MCPClient, cache: Cache, config: Config,
                  cg: str, phone: str | None) -> None:
    """复合命令：搜店 + 并行取 top N 家菜单一步到位。返回 {"shops": [...], "menus": [...]}。"""
    lat, lng = _resolve_lat_lng(args, cache, config, phone)
    try:
        top_n = min(int(args.top_n or 3), 5)
    except (TypeError, ValueError):
        top_n = 3

    search_cache_key = f"search:{lat},{lng},{args.keyword or 'default'}"
    trimmed = cache.get(search_cache_key)
    if not trimmed:
        raw = gw.search_shops(cg, keyword=args.keyword, lat=lat, lng=lng, city=args.city)
        trimmed = trim_search_results(raw)
        remember_carts(cache, trimmed["shops"])
        cache.set(search_cache_key, trimmed, SEARCH_TTL)

    top_shops = trimmed["shops"][:top_n]

    def _fetch_menu(shop: dict) -> dict:
        sid, cid = shop.get("shop_id"), shop.get("cart_id")
        if not sid or not cid:
            return {"shop_id": sid, "shop_name": shop.get("name"), "error": "缺少购物车上下文"}
        menu_key = _menu_cache_key(cid)
        menu = cache.get(menu_key)
        if not menu:
            try:
                menu = gw.get_shop_menu(cg, shop_id=sid, cart_id=cid)
                cache.set(menu_key, menu, MENU_TTL)
            except GatewayError:
                return {"shop_id": sid, "shop_name": shop.get("name"), "error": "菜单获取失败"}
        overview = build_menu_overview(menu, compact=True)
        overview["shop_id"] = sid
        return overview

    from concurrent.futures import ThreadPoolExecutor
    if top_shops:
        with ThreadPoolExecutor(max_workers=max(1, len(top_shops))) as pool:
            menus = list(pool.map(_fetch_menu, top_shops))
    else:
        menus = []

    output({"shops": top_shops, "menus": menus})


def cmd_get_shop_menu(args, gw: MCPClient, cache: Cache, config: Config,
                      cg: str, phone: str | None) -> None:
    if not args.shop_id:
        die("缺少 --shop-id 参数。")
    cart_id = resolve_cart_id(cache, args.shop_id)

    menu_key = _menu_cache_key(cart_id)
    menu = cache.get(menu_key)
    if not menu:
        try:
            menu = gw.get_shop_menu(cg, shop_id=args.shop_id, cart_id=cart_id)
        except GatewayError as e:
            die(friendly_error(e, {"shop_id": args.shop_id}))
        cache.set(menu_key, menu, MENU_TTL)

    if args.item_id:
        item = next((it for it in menu.get("items", [])
                     if isinstance(it, dict) and it.get("item_id") == args.item_id), None)
        if not item:
            die_with_hint(f"未在当前菜单找到商品 {args.item_id}", "REFERENCE_STALE",
                          {"shop_id": args.shop_id})
        output(build_item_detail(item))
        return

    if args.keyword:
        output(search_menu_items(menu, args.keyword))
        return

    if args.category:
        detail = build_category_detail(menu, args.category)
        if not detail:
            names = "、".join(c.get("name", "") for c in menu.get("categories", []))
            die_with_hint(f'未找到分类"{args.category}"，可用分类：{names}', "CATEGORY_NOT_FOUND")
        output(detail)
        return

    output(build_menu_overview(menu))


def cmd_get_item_options(args, gw: MCPClient, cache: Cache, config: Config,
                         cg: str, phone: str | None) -> None:
    if not args.shop_id or not args.items:
        die("缺少必要参数：--shop-id、--items")
    cart_id = resolve_cart_id(cache, args.shop_id)
    try:
        items = json.loads(args.items)
    except json.JSONDecodeError as e:
        die(f"--items JSON 解析失败：{e}")
        return
    if not isinstance(items, list) or not items:
        die('--items 必须是非空 JSON 数组，元素形如 {"item_id":"item_x","sku_id":"sku_y",'
            '"ingredient_option_ids":["opt_z"]}')
    try:
        result = gw.get_item_options(cg, cart_id=cart_id, items=items)
    except GatewayError as e:
        die(friendly_error(e, {"shop_id": args.shop_id}))
        return
    output(result)


def cmd_search_addresses(args, gw: MCPClient, cache: Cache, config: Config,
                         cg: str, phone: str | None) -> None:
    # 有搜索条件 → 搜索；否则列出已存地址（同一个 tool，keyword 缺省即列表）。
    if args.keyword or args.lat is not None or args.lng is not None or args.city:
        if args.city:
            call_lat, call_lng = None, None  # city beats historical coords
        else:
            call_lat, call_lng = args.lat, args.lng
        try:
            raw = gw.search_addresses(cg, keyword=args.keyword,
                                      lat=call_lat, lng=call_lng, city=args.city)
        except GatewayError as e:
            die(f"地址搜索失败：{friendly_error(e)}")
            return
        trimmed = normalize_address_search(raw)
        _refresh_saved_cache(cache, phone, trimmed["saved"])
        output(trimmed)
        return

    cached_lat, cached_lng = get_cached_address_coords(cache, phone)
    if cached_lat is None and cached_lng is None and phone is None:
        cached_lat, cached_lng = config.default_lat, config.default_lng
    try:
        raw = gw.search_addresses(cg, lat=cached_lat, lng=cached_lng)
    except GatewayError as e:
        die(f"获取地址失败：{friendly_error(e)}")
        return
    trimmed = normalize_address_search(raw)
    if not trimmed["saved"] and not trimmed["suggestions"]:
        die_with_hint(
            "[需要地址] 后端没有 saved 地址也没有历史记录——请直接问用户当前位置（'你这会儿在哪边呀？地址直接说就行～'）。",
            "ADDR_MISSING",
        )
    _refresh_saved_cache(cache, phone, trimmed["saved"])
    output(trimmed)


def cmd_select_address(args, gw: MCPClient, cache: Cache, config: Config,
                       cg: str, phone: str | None) -> None:
    if not args.sug_ref and not args.address_id:
        die("缺少 --sug-ref（search_addresses 返回的 suggestions[].sug_ref）或 --address-id。")
    if not args.contact_name or not args.contact_phone:
        die("保存地址需要 --contact-name 和 --contact-phone。")
    try:
        result = gw.select_address(
            cg,
            contact_name=args.contact_name,
            contact_phone=args.contact_phone,
            suggestion_token=args.sug_ref,
            address_id=args.address_id,
            address_detail=args.address_detail or "",
            tag=args.tag,
        )
    except GatewayError as e:
        if e.code == "CONTACT_REQUIRED":
            die_with_hint("缺少收件人姓名或手机号，请向用户确认后重试。", "CONTACT_REQUIRED",
                          {"phone_masked": mask_phone(phone) if phone else "<手机号>"})
        if e.code == "DETAIL_REQUIRED":
            die_with_hint("这个地址是新地点（POI），需要先问到具体门牌号/楼层/房间号，再带 --address-detail 重试。",
                          "POI_DETAIL_REQUIRED")
        if e.code == "SUGGESTION_EXPIRED":
            die_with_hint("地址候选已过期或已使用。", "SUGGESTION_EXPIRED")
        die(f"保存地址失败：{friendly_error(e)}")
        return
    new_addr = normalize_saved_address(result)
    addr_key = _addr_cache_key(phone)
    existing = cache.get(addr_key)
    existing = existing if isinstance(existing, list) else []
    existing = [a for a in existing if a.get("address_id") != new_addr.get("address_id")]
    existing.insert(0, new_addr)
    cache.set(addr_key, existing, ADDRESS_TTL)
    output(new_addr)


def _parse_items(raw_items: str) -> list[dict]:
    """Parse --items JSON into clean CartItem dicts (open-gateway shape):
    {item_id, sku_id?, quantity, ingredient_option_ids?, remark?}. The gateway
    forbids extra fields, so only these keys are forwarded."""
    parsed = json.loads(raw_items)
    if not isinstance(parsed, list) or not parsed:
        die("--items 必须是非空 JSON 数组，元素形如 "
            '{"item_id":"item_x","quantity":1,"sku_id":"sku_y","ingredient_option_ids":["opt_z"],"remark":"少冰"}')
    items: list[dict] = []
    for raw in parsed:
        if not isinstance(raw, dict) or not raw.get("item_id"):
            die("--items 每个元素必须含 item_id。")
        try:
            qty = int(raw.get("quantity", 1))
        except (TypeError, ValueError):
            qty = 1
        entry: dict = {"item_id": raw["item_id"], "quantity": max(1, qty)}
        if raw.get("sku_id"):
            entry["sku_id"] = raw["sku_id"]
        opt_ids = raw.get("ingredient_option_ids")
        if isinstance(opt_ids, list) and opt_ids:
            entry["ingredient_option_ids"] = [str(o) for o in opt_ids]
        if raw.get("remark"):
            entry["remark"] = str(raw["remark"])
        items.append(entry)
    return items


def cmd_preview_order(args, gw: MCPClient, cache: Cache, config: Config,
                      cg: str, phone: str | None) -> None:
    if not args.shop_id or not args.address_id or not args.items:
        die("缺少必要参数：--shop-id、--address-id、--items")
    cart_id = resolve_cart_id(cache, args.shop_id)
    items = _parse_items(args.items)
    try:
        result = gw.preview_order(cg, shop_id=args.shop_id, cart_id=cart_id,
                                  address_id=args.address_id, items=items,
                                  order_remark=args.note or "")
    except GatewayError as e:
        die(friendly_error(e, {"shop_id": args.shop_id, "address_id": args.address_id}))
        return
    output(result)


def cmd_create_order(args, gw: MCPClient, cache: Cache, config: Config,
                     cg: str, phone: str | None) -> None:
    if not args.preview_id or not args.confirmation_token:
        die("缺少 --preview-id / --confirmation-token（均来自 preview_order 的返回）。")
    try:
        result = gw.create_order(cg, preview_id=args.preview_id,
                                 confirmation_token=args.confirmation_token)
    except GatewayError as e:
        die(friendly_error(e))
        return
    # Surface the payment link (payment_action.action_url) as payment_link so the
    # agent's "always show the payment link" rule keeps working.
    action = result.get("payment_action")
    if isinstance(action, dict) and action.get("action_url"):
        result["payment_link"] = action["action_url"]
    output(result)


def cmd_get_order_status(args, gw: MCPClient, cache: Cache, config: Config,
                         cg: str, phone: str | None) -> None:
    if not args.order_id:
        die("缺少 --order-id 参数。")
    try:
        result = gw.get_order_status(cg, args.order_id)
    except GatewayError as e:
        die(friendly_error(e))
        return
    output(result)


def cmd_get_user_auth_status(args, gw: MCPClient, cache: Cache, config: Config,
                             cg: str, phone: str | None) -> None:
    try:
        result = gw.get_auth_status(cg)
    except GatewayError as e:
        die(friendly_error(e, {"phone": phone or "<11位手机号>"}))
        return
    output(result)


# ── Bind commands（不需要 consent；SMS 默认 / H5 链接授权）───────────────────

def cmd_request_user_bind(args, gw: MCPClient, creds: CredStore, config: Config) -> None:
    """绑定第 1 步。sms（默认）发验证码，返回 bind_id；h5 签发链接，返回 request_id + h5_url。"""
    if not args.phone:
        die("缺少 --phone 参数（用户手机号，11 位数字）")
    phone = normalize_phone(args.phone)
    masked = mask_phone(phone)

    if args.auth_type == "h5":
        try:
            result = gw.request_bind(phone, auth_type="h5")
        except GatewayError as e:
            die(f"获取授权链接失败：{friendly_error(e, {'phone': phone})}")
            return
        request_id = result.get("request_id")
        h5_url = result.get("h5_url")
        if not request_id or not h5_url:
            die(f"请求成功但 gateway 未返回 request_id/h5_url：{result}")
        output({
            "auth_type": "h5",
            "request_id": request_id,
            "h5_url": h5_url,
            "phone": phone,
            "phone_masked": result.get("masked_phone") or masked,
            "expires_in": result.get("expires_in", 300),
            "next_step": (
                f"把 h5_url 原样发给用户，让他点开完成授权。用户说授权完成后调用："
                f"verify_user_bind --auth-type h5 --phone {phone} --request-id {request_id}"
            ),
        })
        return

    try:
        result = gw.request_bind(phone, auth_type="sms")
    except GatewayError as e:
        die(f"发送验证码失败：{friendly_error(e, {'phone': phone})}")
        return
    bind_id = result.get("bind_id")
    if not bind_id:
        die(f"发送成功但 gateway 未返回 bind_id：{result}")
    output({
        "auth_type": "sms",
        "bind_id": bind_id,
        "phone": phone,
        "phone_masked": result.get("masked_phone") or masked,
        "next_step": (
            f"已发短信到 {masked}，请告诉用户回复 6 位验证码。用户回复后调用："
            f"verify_user_bind --phone {phone} --bind-id {bind_id} --code <用户输的6位>"
        ),
    })


def cmd_verify_user_bind(args, gw: MCPClient, creds: CredStore, config: Config) -> None:
    """绑定第 2 步。成功后把 consent_grant_id 按 (API_KEY 指纹, phone) 写进共享缓存。"""
    if not args.phone:
        die("缺少 --phone 参数")
    phone = normalize_phone(args.phone)

    if args.auth_type == "h5":
        if not args.request_id:
            die("缺少 --request-id 参数（来自 request_user_bind --auth-type h5 的返回）")
        try:
            result = gw.verify_bind(auth_type="h5", request_id=args.request_id)
        except GatewayError as e:
            die(f"查询授权结果失败：{friendly_error(e, {'phone': phone})}")
            return
        if not result.get("bound"):
            status = result.get("status") or "pending"
            if status == "expired":
                die("授权链接已过期。\n"
                    f"RECOVERY[H5_BIND_EXPIRED]: 重新调 request_user_bind --auth-type h5 --phone {phone} "
                    "拿新链接发给用户。")
            die("用户还没完成授权。\n"
                "RECOVERY[H5_BIND_PENDING]: 提醒用户点开刚才的链接完成授权；等用户说完成后用同一个 "
                "request_id 重调本命令。不要高频轮询。")
    else:
        if not args.bind_id:
            die("缺少 --bind-id 参数（来自 request_user_bind 的返回）")
        if not args.code:
            die("缺少 --code 参数（用户输的 6 位短信验证码）")
        try:
            result = gw.verify_bind(auth_type="sms", bind_id=args.bind_id, code=args.code)
        except GatewayError as e:
            die(f"验证失败：{friendly_error(e, {'phone': phone})}")
            return

    cg = result.get("consent_grant_id")
    if not cg:
        die(f"验证通过但 gateway 未返回 consent_grant_id：{result}")
    creds.set(phone, cg, result.get("expires_at"))
    out = {
        "consent_grant_id": cg,
        "expires_at": result.get("expires_at"),
        "scopes": result.get("scopes"),
        "phone": phone,
        "cached": True,
        "message": (
            "绑定成功，凭证已写入共享缓存（~/.clawdot/credentials.json，同一 API_KEY 下所有 skill 共用）。"
            "单用户后续调用无需 --phone；多用户场景请始终带 --phone 指定用户。"
        ),
    }
    # env CONSENT_GRANT_ID 优先级最高（M4 只读预注入）——残留旧值会遮蔽这次新绑的凭证
    if config.consent_grant_id and config.consent_grant_id != cg:
        out["warning"] = (
            "检测到 CONSENT_GRANT_ID 环境变量（通常来自 .env）且与本次新绑不同。"
            "不带 --phone 的调用会优先用它、遮蔽新凭证；若它已失效，请把 .env 里的 "
            "CONSENT_GRANT_ID 行删掉再重试。"
        )
    output(out)


def cmd_revoke_user_bind(args, gw: MCPClient, creds: CredStore, config: Config) -> None:
    """解绑：服务端撤销 consent + 清本机共享缓存条目（地址/订单史保留，重绑同号可恢复）。"""
    phone = normalize_phone(args.phone) if args.phone else None
    warning = None
    if phone:
        cg = creds.get(phone)
        if not cg:
            die(f"用户 {mask_phone(phone)} 在本机没有缓存的绑定，无需解绑。"
                "若要撤销一个已知的 consent_grant_id，用 "
                "call revoke_user_bind --json '{\"consent_grant_id\":\"cg_…\"}'。")
            return
    elif config.consent_grant_id:
        cg = config.consent_grant_id
        warning = (
            "撤销的是 CONSENT_GRANT_ID 环境变量（通常来自 .env）里的凭证——它现在已失效，"
            "请把 .env 里的 CONSENT_GRANT_ID 行删掉，否则后续调用会一直用这个死值报授权失效。"
        )
    else:
        bound = creds.all()
        if len(bound) > 1:
            die("已绑定多个用户，请带 --phone <11位> 指定要解绑的用户。")
            return
        if not bound:
            die("本机没有缓存的绑定，也没有 CONSENT_GRANT_ID 环境变量，无需解绑。")
            return
        phone, cg = next(iter(bound.items()))

    server_state = "revoked"
    try:
        gw.revoke_bind(cg)
    except GatewayError as e:
        # 该 cg 在服务端已失效（已撤销/过期/轮换）→ 目的已达成，继续清本地
        if str(e.code).startswith("CONSENT") or e.code == "AUTH_REQUIRED":
            server_state = "already_invalid"
        else:
            die(f"解绑失败：{friendly_error(e, {'phone': phone or '<11位手机号>'})}")
            return

    cache_deleted = creds.delete(phone) if phone else False
    out = {
        "revoked": True,
        "server_state": server_state,
        "phone": phone,
        "cache_deleted": cache_deleted,
        "message": "解绑完成。用户的地址和订单历史在服务端保留，重新绑定同一手机号即可恢复使用。",
    }
    if warning:
        out["warning"] = warning
    output(out)


# ── Generic passthrough（未文档化 tool 的机械通道，DECISIONS M7 附注）─────────

BIND_TOOLS = {"request_user_bind", "verify_user_bind"}


def cmd_call(args, gw: MCPClient, cache: Cache, config: Config,
             creds: CredStore, phone: str | None) -> None:
    try:
        arguments = json.loads(args.json_args) if args.json_args else {}
    except json.JSONDecodeError as e:
        die(f"--json 解析失败：{e}")
        return
    if not isinstance(arguments, dict):
        die("--json 必须是 JSON 对象（tool 的 arguments）。")
    if args.tool not in BIND_TOOLS and "consent_grant_id" not in arguments:
        arguments["consent_grant_id"] = resolve_consent_grant(phone, creds, config)
    try:
        result = gw._call(args.tool, arguments)
    except GatewayError as e:
        die(friendly_error(e, {"phone": phone or "<11位手机号>"}))
        return
    output(result)


# ── Main ────────────────────────────────────────────────────────────────────

COMMANDS = {
    "search_shops": cmd_search_shops,
    "recommend": cmd_recommend,
    "get_shop_menu": cmd_get_shop_menu,
    "get_item_options": cmd_get_item_options,
    "search_addresses": cmd_search_addresses,
    "select_address": cmd_select_address,
    "preview_order": cmd_preview_order,
    "create_order": cmd_create_order,
    "get_order_status": cmd_get_order_status,
    "get_user_auth_status": cmd_get_user_auth_status,
}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--phone", default=None,
        help="（多用户模式）用户手机号；按 (API_KEY, 手机号) 读取共享缓存里的 consent_grant_id。"
             "不传则用 CONSENT_GRANT_ID 环境变量或缓存中唯一已绑用户。",
    )

    parser = argparse.ArgumentParser(
        prog="clawdot.py",
        description="ClawDot 本地生活 CLI（open-gateway MCP 面）",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("request_user_bind", parents=[common],
                       help="用户绑定第 1 步（默认发短信验证码；--auth-type h5 签发授权链接）")
    p.add_argument("--auth-type", default="sms", choices=["sms", "h5"])

    p = sub.add_parser("verify_user_bind", parents=[common],
                       help="用户绑定第 2 步（短信验码 / H5 轮询授权结果），成功后写共享缓存")
    p.add_argument("--auth-type", default="sms", choices=["sms", "h5"])
    p.add_argument("--bind-id", default=None, help="（sms 必填）request_user_bind 返回的 bind_id")
    p.add_argument("--code", default=None, help="（sms 必填）用户回复的 6 位短信验证码")
    p.add_argument("--request-id", default=None,
                   help="（h5 必填）request_user_bind --auth-type h5 返回的 request_id")

    sub.add_parser("get_user_auth_status", parents=[common],
                   help="查询当前用户授权状态（验活 consent，不会触发重绑）")

    sub.add_parser("revoke_user_bind", parents=[common],
                   help="解绑：撤销服务端授权并清除本机共享缓存凭证（多用户带 --phone；"
                        "地址/订单史保留，重绑同号可恢复）")

    p = sub.add_parser("search_addresses", parents=[common],
                       help="列出已存收货地址；带 --keyword 搜索新地址（返回 suggestions[].sug_ref）")
    p.add_argument("--keyword", default=None, help="POI 搜索关键词；缺省则只列已存地址")
    p.add_argument("--lat", type=float, default=None)
    p.add_argument("--lng", type=float, default=None)
    p.add_argument("--city", default=None,
                   help="城市名（中文/拼音/缩写）。传了就覆盖历史坐标走城市搜索。")

    p = sub.add_parser("select_address", parents=[common],
                       help="把地址候选（--sug-ref）或已存地址（--address-id）落成收货地址")
    p.add_argument("--sug-ref", default=None,
                   help="search_addresses 返回的 suggestions[].sug_ref（内部作为 suggestion_token 发给网关）")
    p.add_argument("--address-id", default=None, help="已存地址 id（addr_…）")
    p.add_argument("--contact-name", default=None, help="收件人姓名（必填）")
    p.add_argument("--contact-phone", default=None, help="收件人手机号（必填）")
    p.add_argument("--address-detail", default=None, help="门牌/楼层/室号；POI suggestion 必填")
    p.add_argument("--tag", default=None, help="标签（≤6 字，如 家/公司/学校），仅 --sug-ref 模式生效")

    p = sub.add_parser("search_shops", parents=[common],
                       help="按关键词搜索附近店铺（返回每店上下文，供后续选菜/下单复用）")
    p.add_argument("--keyword", default=None, help="店名/品类/具体商品名；缺省浏览附近店")
    p.add_argument("--lat", type=float, default=None)
    p.add_argument("--lng", type=float, default=None)
    p.add_argument("--city", default=None)

    p = sub.add_parser("recommend", parents=[common],
                       help="复合命令：搜店 + 并行取 top N 家菜单一步到位")
    p.add_argument("--keyword", default=None)
    p.add_argument("--lat", type=float, default=None)
    p.add_argument("--lng", type=float, default=None)
    p.add_argument("--city", default=None)
    p.add_argument("--top-n", default=None, help="拉菜单的店铺数，默认 3、最多 5")

    p = sub.add_parser("get_shop_menu", parents=[common],
                       help="菜单钻取（概览→分类→商品详情；--keyword 跨分类搜菜）")
    p.add_argument("--shop-id", required=True)
    p.add_argument("--category", default=None, help="分类名/序号 → 该分类全部商品详情")
    p.add_argument("--item-id", default=None, help="单商品详情（sku_options / ingredient_options）")
    p.add_argument("--keyword", default=None, help="按菜名跨分类模糊搜（客户端在缓存菜单上过滤）")

    p = sub.add_parser("get_item_options", parents=[common],
                       help="批量查多个商品的完整规格/加料（含当前选中标记）")
    p.add_argument("--shop-id", required=True)
    p.add_argument("--items", required=True,
                   help='JSON array：[{"item_id":"item_x","sku_id":"sku_y","ingredient_option_ids":["opt_z"]}]')

    p = sub.add_parser("preview_order", parents=[common],
                       help="预览订单（价格、配送费、优惠），返回 preview_id + confirmation_token")
    p.add_argument("--shop-id", required=True)
    p.add_argument("--address-id", required=True, help="平台地址 id（addr_…）")
    p.add_argument("--items", required=True,
                   help='JSON array：[{"item_id":"item_x","quantity":1,"sku_id":"sku_y",'
                        '"ingredient_option_ids":["opt_z"],"remark":"少冰"}]')
    p.add_argument("--note", default=None, help="订单备注（order_remark）")

    p = sub.add_parser("create_order", parents=[common],
                       help="确认并提交订单（--preview-id + --confirmation-token，返回付款链接）")
    p.add_argument("--preview-id", required=True, help="preview_order 返回的 preview_id（prv_…）")
    p.add_argument("--confirmation-token", required=True,
                   help="preview_order 返回的 confirmation_token（cf_…）")

    p = sub.add_parser("get_order_status", parents=[common], help="查询订单配送状态")
    p.add_argument("--order-id", required=True)

    p = sub.add_parser("call", parents=[common],
                       help="通用通道：按 tool 名直调任意网关 MCP tool（未文档化，agent 勿用）")
    p.add_argument("tool", help="MCP tool 名")
    p.add_argument("--json", dest="json_args", default=None, help="tool arguments（JSON 对象）")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config()

    if not config.api_key:
        die(
            "还没配置外卖服务的 API_KEY。\n"
            f"让用户打开 {config.setup_url} 登录/注册 ClawDot 拿到 API_KEY，原文发回来；"
            "收到后写入本 skill 根目录 .env，内容两行：\n"
            f"GATEWAY_MCP_URL={DEFAULT_MCP_URL}\n"
            "API_KEY=<用户发来的key>\n"
            "不要复述或展示 key。写好后接着问绑定信息。\n"
            "RECOVERY[API_KEY_MISSING]: ① 把注册链接发给用户等 key → ② 写入 .env → "
            "③ 一句话问齐：'先告诉我手机号，顺便选一下用 H5 还是验证码方式绑定哦～'"
        )

    gw = MCPClient(config)
    creds = CredStore(config.api_key, config.clawdot_home)

    # ── 用户绑定流程（不需要 consent）──────────────────────────────
    if args.command == "request_user_bind":
        cmd_request_user_bind(args, gw, creds, config)
        return
    if args.command == "verify_user_bind":
        cmd_verify_user_bind(args, gw, creds, config)
        return
    if args.command == "revoke_user_bind":
        cmd_revoke_user_bind(args, gw, creds, config)
        return

    cache = Cache()

    if args.command == "call":
        cmd_call(args, gw, cache, config, creds, args.phone)
        return

    # ── 其他业务命令必须先解析 consent_grant_id（优先级见 resolve_consent_grant）──
    cg = resolve_consent_grant(args.phone, creds, config)
    try:
        COMMANDS[args.command](args, gw, cache, config, cg, args.phone)
    except GatewayError as e:
        die(friendly_error(e))
    except json.JSONDecodeError as e:
        die(f"JSON 解析失败：{e}")


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def output(data: object) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
