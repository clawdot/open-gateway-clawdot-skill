#!/usr/bin/env python3
"""ClawDot takeout ordering script — single entry point for all actions.

基于 **open-gateway** public v1 接口（consent_grant 体系）。支持两种鉴权模式：

* **Personal mode**：在 ``.env`` 配置 ``CONSENT_GRANT_ID``（用户授权凭证 ``cg_``），
  单用户长期复用。
* **用户绑定 mode**：CLI 传 ``--phone <11 位手机号>``，脚本按手机号缓存该用户的
  ``consent_grant_id``；缓存缺失时返回 ``RECOVERY[USER_NOT_BOUND_NEEDS_SMS]`` 引导
  用户走短信验证码（默认）或 H5 链接授权（``request_code`` → ``verify_code``）拿凭证。

与旧 clawdot-gateway 的差异（见仓库 DECISIONS.md）：``X-User-Token`` → ``X-Consent-Grant-Id``；
不再有 admin trustedBind（agent 静默绑定能力 open-gateway 已移除）；搜店返回 ``cart_id``
须贯穿 menu/preview；下单走 preview→(preview_id+confirmation_token)→create；金额单位为分。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ── Config ──────────────────────────────────────────────────────────────────

@dataclass
class Config:
    gateway_url: str
    api_key: str
    consent_grant_id: str
    setup_url: str
    default_lat: float | None
    default_lng: float | None
    redis_url: str | None
    timeout_ms: int
    env_path: Path


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


def write_env_var(path: Path, key: str, value: str) -> bool:
    """Upsert ``KEY=value`` into the skill's .env (read-modify-write, other lines
    preserved). Used to persist the consent_grant after binding so the next run
    works with just API_KEY injected (no --phone, no manual CONSENT_GRANT_ID).

    Best-effort: returns False if the file isn't writable (read-only install) —
    callers fall back to the per-phone cache. The file holds a bearer-equivalent
    credential, so it's chmod'd 0600; .env is gitignored."""
    try:
        lines = path.read_text().splitlines() if path.is_file() else []
        prefix = f"{key}="
        replaced = False
        for i, line in enumerate(lines):
            if line.lstrip().startswith(prefix) and not line.lstrip().startswith("#"):
                lines[i] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={value}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        os.environ[key] = value  # reflect in-process too
        return True
    except OSError:
        return False


def normalize_gateway_url(raw: str) -> str:
    """Normalize GATEWAY_URL to an origin (no trailing slash, no /api/v1).

    The client appends ``/api/v1/...`` itself, so a GATEWAY_URL that already
    includes the /api/v1 base (the natural way people write an API base URL)
    would otherwise produce a doubled ``/api/v1/api/v1/...`` path. Strip it."""
    url = raw.strip().rstrip("/")
    if url.endswith("/api/v1"):
        url = url[: -len("/api/v1")]
    return url


def load_config() -> Config:
    """Load config from env vars (populated by .env if present)."""
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
        gateway_url=normalize_gateway_url(os.environ.get("GATEWAY_URL", "http://127.0.0.1:3100")),
        api_key=os.environ.get("API_KEY", ""),
        consent_grant_id=os.environ.get("CONSENT_GRANT_ID", ""),
        setup_url=os.environ.get(
            "CLAWDOT_SETUP_URL",
            "https://clawdot.hicaspian.com/developer/login",
        ),
        default_lat=to_float("DEFAULT_LAT"),
        default_lng=to_float("DEFAULT_LNG"),
        redis_url=os.environ.get("REDIS_URL") or None,
        timeout_ms=int(os.environ.get("TIMEOUT_MS", "30000")),
        env_path=base_dir / ".env",
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


# ── Gateway Client ──────────────────────────────────────────────────────────

class GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


class GatewayClient:
    """open-gateway public v1 client.

    鉴权：``Authorization: Bearer <api_key>`` 总是携带；用户态调用追加
    ``X-Consent-Grant-Id: <cg>``。绑定接口（bind/request、bind/verify）只用 Bearer。
    所有 path 以 ``/api/v1/`` 开头。
    """

    def __init__(self, config: Config):
        self.base_url = config.gateway_url
        self.api_key = config.api_key
        self.timeout = config.timeout_ms / 1000

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        consent_grant: str | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "ClawDot-Takeout-OG/1.0",
        }
        if consent_grant:
            headers["X-Consent-Grant-Id"] = consent_grant
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except HTTPError as e:
            err_body = {}
            try:
                err_body = json.loads(e.read())
            except Exception:
                pass
            err = err_body.get("error", {}) if isinstance(err_body, dict) else {}
            raise GatewayError(
                e.code,
                err.get("code", "UNKNOWN"),
                err.get("message", e.reason),
            ) from None
        except URLError as e:
            raise GatewayError(0, "NETWORK", str(e.reason)) from None

    # ── 绑定（仅 Bearer，无 consent）──────────────────────────────────────────

    def request_bind(self, phone: str, auth_type: str = "sms") -> dict:
        """绑定第 1 步。sms（默认）发验证码，返回 {"bind_id", "expires_in", "masked_phone"}；
        h5 签发授权链接，返回 {"request_id", "h5_url", "expires_in", "masked_phone", ...}。"""
        body: dict = {"phone": phone, "auth_type": auth_type}
        return self._request("POST", "/api/v1/auth/bind/request", body)

    def verify_bind(self, auth_type: str = "sms", bind_id: str | None = None,
                    code: str | None = None, request_id: str | None = None) -> dict:
        """绑定第 2 步。sms 传 bind_id+code；h5 传 request_id。
        成功返回 {"bound": true, "consent_grant_id", "scopes", "expires_at", ...}；
        h5 未完成返回 {"bound": false, "status": "pending"|"expired", ...}。"""
        body: dict = {"auth_type": auth_type}
        if auth_type == "h5":
            body["request_id"] = request_id
        else:
            body["bind_id"] = bind_id
            body["code"] = code
        return self._request("POST", "/api/v1/auth/bind/verify", body)

    # ── 业务（用户态，带 consent）────────────────────────────────────────────

    def search_shops(self, cg: str, *, keyword: str | None = None,
                     lat: float | None = None, lng: float | None = None,
                     city: str | None = None, address_id: str | None = None,
                     offset: int = 0) -> dict:
        body: dict = {"offset": offset}
        if keyword:
            body["keyword"] = keyword
        if address_id:
            body["address_id"] = address_id
        if lat is not None:
            body["lat"] = lat
        if lng is not None:
            body["lng"] = lng
        if city:
            body["city"] = city
        return self._request("POST", "/api/v1/shops/search", body, consent_grant=cg)

    def get_shop_menu(self, cg: str, *, shop_id: str, cart_id: str,
                      address_id: str | None = None, lat: float | None = None,
                      lng: float | None = None, keyword: str | None = None,
                      limit: int | None = None, offset: int = 0) -> dict:
        body: dict = {"shop_id": shop_id, "cart_id": cart_id, "offset": offset}
        if address_id:
            body["address_id"] = address_id
        if lat is not None:
            body["lat"] = lat
        if lng is not None:
            body["lng"] = lng
        if keyword:
            body["keyword"] = keyword
        if limit is not None:
            body["limit"] = limit
        return self._request("POST", "/api/v1/shops/menu", body, consent_grant=cg)

    def search_addresses(self, cg: str, *, keyword: str | None = None,
                         lat: float | None = None, lng: float | None = None,
                         city: str | None = None) -> dict:
        body: dict = {}
        if keyword:
            body["keyword"] = keyword
        if lat is not None:
            body["lat"] = lat
        if lng is not None:
            body["lng"] = lng
        if city:
            body["city"] = city
        return self._request("POST", "/api/v1/addresses/search", body, consent_grant=cg)

    def select_address(self, cg: str, body: dict) -> dict:
        return self._request("POST", "/api/v1/addresses/select", body, consent_grant=cg)

    def update_address(self, cg: str, body: dict) -> dict:
        return self._request("POST", "/api/v1/addresses/update", body, consent_grant=cg)

    def preview_order(self, cg: str, body: dict) -> dict:
        return self._request("POST", "/api/v1/orders/preview", body, consent_grant=cg)

    def create_order(self, cg: str, *, preview_id: str, confirmation_token: str,
                     payment_method: str | None = None) -> dict:
        body: dict = {"preview_id": preview_id, "confirmation_token": confirmation_token}
        if payment_method:
            body["payment_method"] = payment_method
        return self._request("POST", "/api/v1/orders/create", body, consent_grant=cg)

    def get_order_status(self, cg: str, order_id: str) -> dict:
        # order_id 拼进 URL path 段，必须转义——挡住含 '/'、'..' 的恶意/错乱入参重塑请求路径
        # （恶意值被编码成单个无害 path 段，上游返 404，而非穿越到别的 endpoint）。
        return self._request("GET", f"/api/v1/orders/{quote(order_id, safe='')}", consent_grant=cg)


# ── File Cache ──────────────────────────────────────────────────────────────

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
        # 缓存里存的是 consent_grant（等价 bearer 凭证）+ 手机号——按 0700/0600 收紧，
        # 防同机其他本地用户读到 cg 冒充用户。
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

    def consent_grants(self) -> dict[str, str]:
        """{phone: consent_grant_id} for all non-expired bound users (cache key
        ``cg:<phone>``). Backstop for the no-phone path when .env wasn't written."""
        now = time.time()
        out: dict[str, str] = {}
        for key, entry in self._data.items():
            if not key.startswith("cg:") or now > entry.get("expires_at", 0):
                continue
            data = entry.get("data")
            if isinstance(data, dict) and data.get("consent_grant_id"):
                out[key[3:]] = data["consent_grant_id"]
        return out

    def _prune(self) -> None:
        now = time.time()
        expired = [k for k, v in self._data.items() if now > v.get("expires_at", 0)]
        for k in expired:
            del self._data[k]


# ── Redis Cache (optional, for cross-process sharing) ───────────────────────

REDIS_CG_PREFIX = "clawdot:consent_grant:"
CONSENT_TTL = 3600  # fallback TTL (1 hour) when expires_at is unparseable


class RedisCache:
    """Minimal Redis client via raw sockets — no redis-py dependency."""

    def __init__(self, url: str):
        parsed = urlparse(url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or 6379
        self._password = parsed.password
        self._db = int(parsed.path.lstrip("/") or "0")

    @staticmethod
    def _build_cmd(*args: str) -> bytes:
        parts = [f"*{len(args)}\r\n".encode()]
        for a in args:
            encoded = a.encode()
            parts.append(f"${len(encoded)}\r\n".encode() + encoded + b"\r\n")
        return b"".join(parts)

    @staticmethod
    def _read_reply(sock) -> bytes | None:
        buf = b""
        while b"\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        if not buf:
            return None
        prefix = buf[0:1]
        line_end = buf.index(b"\r\n")
        line = buf[1:line_end]
        if prefix == b"+":
            return line
        if prefix == b"-":
            return None
        if prefix == b":":
            return line
        if prefix == b"$":
            length = int(line)
            if length == -1:
                return None
            data_start = line_end + 2
            total_needed = data_start + length + 2
            while len(buf) < total_needed:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            return buf[data_start:data_start + length]
        return None

    def _command(self, *args: str) -> bytes | None:
        import socket
        raw = self._build_cmd(*args)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        try:
            sock.connect((self._host, self._port))
            if self._password:
                sock.sendall(self._build_cmd("AUTH", self._password))
                self._read_reply(sock)
            if self._db != 0:
                sock.sendall(self._build_cmd("SELECT", str(self._db)))
                self._read_reply(sock)
            sock.sendall(raw)
            return self._read_reply(sock)
        finally:
            sock.close()

    def get(self, key: str) -> str | None:
        try:
            result = self._command("GET", key)
            return result.decode() if result else None
        except Exception:
            return None

    def setex(self, key: str, ttl: int, value: str) -> bool:
        try:
            self._command("SETEX", key, str(ttl), value)
            return True
        except Exception:
            return False


def _try_connect_redis(config: Config) -> RedisCache | None:
    if not config.redis_url:
        return None
    try:
        return RedisCache(config.redis_url)
    except Exception:
        return None


def _ttl_from_expires(expires_at: str | None) -> int:
    """Compute a cache TTL from an ISO8601 expires_at, clamped to [60s, 120d].

    Falls back to CONSENT_TTL when expires_at is missing/unparseable. The cache
    TTL tracks the cg's own validity (cg defaults to 90 days) so the per-phone
    backstop doesn't forget a still-valid cg early; the 120d ceiling only guards
    against a garbage far-future timestamp. A rotated/expired cg self-heals via
    the gateway's CONSENT_GRANT_{EXPIRED,INVALID} → re-bind path regardless."""
    if not expires_at:
        return CONSENT_TTL
    try:
        dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        seconds = int(dt.timestamp() - time.time())
        return max(60, min(seconds, 120 * 24 * 3600))
    except (ValueError, TypeError):
        return CONSENT_TTL


# ── Consent Grant Resolution ────────────────────────────────────────────────

def resolve_consent_grant(phone: str | None, cache: Cache,
                          redis: RedisCache | None, config: Config) -> str:
    """Return the consent_grant_id (cg_) for the call.

    No --phone (personal / ambient): ``CONSENT_GRANT_ID`` env wins (this includes
    the cg auto-written back to .env after a successful bind, so "inject API_KEY →
    bind once via SMS/H5 → just works" needs no --phone); else fall back to the
    single bound user in cache; multiple bound → require --phone; none → bind hint.

    With --phone (multi-user): Redis → file cache for that phone. Cache miss → die
    with a RECOVERY hint guiding the SMS/H5 bind flow (open-gateway has no silent
    admin bind — the real user must authorize via request_code/verify_code)."""
    if phone is None:
        if config.consent_grant_id:
            return config.consent_grant_id
        bound = cache.consent_grants()
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
    redis_key = f"{REDIS_CG_PREFIX}{norm}"
    file_key = f"cg:{norm}"

    if redis:
        cg = redis.get(redis_key)
        if cg:
            return cg

    cached = cache.get(file_key)
    if isinstance(cached, dict) and cached.get("consent_grant_id"):
        cg = cached["consent_grant_id"]
        if redis:
            redis.setex(redis_key, _ttl_from_expires(cached.get("expires_at")), cg)
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
    """Trim open-gateway search_shops response into a compact shop list.

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


def build_menu_overview(menu: dict, compact: bool = False) -> dict:
    """Build a category overview from an open-gateway shop menu response.

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

    return {
        "shop_id": shop.get("shop_id"),
        "shop_name": shop.get("name", ""),
        "available": shop.get("available", True),
        "categories": categories,
        "total_items": menu.get("total_items"),
    }


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
    """Normalize addresses/search: float-coerce saved, rename suggestion token →
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
    (r"店铺必须商品未点|必选商品未点|必须先购买|必选",
     "MUST_PICK_REQUIRED",
     "店铺要求必选项未点。",
     "menu --shop-id {shop_id} 查看商品的 ingredient_options（带 group_name 的加料/必选组），"
     "把用户选中项的 option_id 放进 items[].ingredient_option_ids 重 preview。"
     "**禁止替用户做主**——口味/规格类让用户选，别自动选。"),

    (r"COORDS_REQUIRED|无法确定.*位置|需要地址|缺.*坐标",
     "ADDR_MISSING",
     "缺用户坐标。",
     "直接问用户'你这会儿在哪边呀？地址直接说就行～'，拿到后 "
     "addresses --address-keyword '<用户给的地址>' --city '<推断或问用户>'。禁止用任何默认坐标。"),

    (r"DETAIL_REQUIRED|这个地址是新地点",
     "POI_DETAIL_REQUIRED",
     "POI 地址需要门牌号。",
     "问用户'几号楼几层几室？'，拿到后 "
     "addresses --select-token <sug_ref> --contact-name --contact-phone --address-detail '<具体内容>' 重 select。"
     "门牌不能传'无'/空格。"),

    (r"CONTACT_REQUIRED|缺少收件人",
     "CONTACT_REQUIRED",
     "缺收件人姓名/手机号。",
     "问'收件人写谁？手机就用你这个 {phone_masked} 行吗？'，"
     "拿到后 addresses --select-token --contact-name --contact-phone 重 select。"),

    (r"SUGGESTION_EXPIRED|地址候选已过期",
     "SUGGESTION_EXPIRED",
     "地址 sug_ref 已过期。",
     "addresses --address-keyword '<用户原话地址>' 重拿新 sug_ref，再 select。"),

    (r"PUBLIC_REFERENCE_INVALID|cart_id|shop_id and cart_id|未找到.*商品|item",
     "REFERENCE_STALE",
     "店铺/商品/购物车引用已失效。",
     "shop_id 或 item_id 已过期（菜单上下文有 TTL）。重新 search/recommend 拿新 shop_id，"
     "再 menu --shop-id {shop_id} 拿新 item_id / sku_id / option_id，然后重 preview。"
     "禁止跨店复用 item_id；禁止把中文菜名当 item_id 传。"),

    (r"SHOP_CART_MISS",
     "SHOP_CART_MISS",
     "缺该店购物车上下文。",
     "menu/preview 需要先 search 或 recommend 这家店拿到上下文。先 search --shop-keyword '<店名/品类>' "
     "（或 recommend），再用返回的 shop_id 重试本次操作。"),

    (r"地址超过.*配送范围|不在配送范围|请重新选择地址后下单|配送范围",
     "OUT_OF_RANGE",
     "店铺不送当前地址。",
     "保留地址，recommend --shop-keyword '<同品类>' --lat --lng --top-n 4 推荐其他店；"
     "或告诉用户'这家不送你这边，换家行不'。禁止换地址重试，禁止用同 shop_id 重 preview。"),

    (r"min order|minimum|未达起送价|起送",
     "BELOW_MIN_ORDER",
     "未达起送价。",
     "menu --shop-id {shop_id} 翻菜单挑 1-2 个低价单品（饮料/小食），"
     "或告诉用户差多少让用户决定加什么。涉及花钱必须用户点头。"),

    (r"closed|not open|店铺.*打烊|休息|未营业|SHOP_NOT_FOUND",
     "SHOP_CLOSED",
     "店铺暂未营业。",
     "recommend --shop-keyword '<同品类>' --lat --lng 推同类其他店。不要重试同店。"),

    (r"out of stock|sold out|售罄|缺货",
     "ITEM_SOLD_OUT",
     "部分商品已售罄。",
     "menu --shop-id {shop_id} 找同款替代（同分类下其他 item），拿替代款给用户确认后再 preview。不要自动替换。"),

    (r"ORDER_FAILED|ELEME_ERROR|Order render failed|Order creation failed",
     "ORDER_GENERIC_FAIL",
     "订单创建/预览失败。",
     "menu --shop-id {shop_id} 重看商品状态（是否下架），逐项核对 item_id/sku_id 后重 preview。"
     "如多次失败，告诉用户换家或调整组合。"),

    (r"IDEMPOTENCY_CONFLICT",
     "IDEMPOTENCY_CONFLICT",
     "下单参数与已用确认凭证不一致。",
     "confirmation_token 已被另一组参数消费。用同样的 shop_id/address_id/items 重新 preview 拿新的 "
     "preview_id + confirmation_token，再 order。"),

    # Match the EXPIRED *code* only — NOT a generic "expired" in the message:
    # CONSENT_GRANT_INVALID's message is "invalid or expired", which must route to
    # CONSENT_INVALID below (a never-bound user is "not bound", not "expired").
    (r"CONSENT_GRANT_EXPIRED|授权.*过期",
     "CONSENT_EXPIRED",
     "用户授权已过期。",
     "引导该用户重新授权：request_code --phone {phone} → verify_code（短信），"
     "或 request_code --auth-type h5 --phone {phone} → verify_code --auth-type h5（H5）。重绑后重试原 action。"),

    (r"CONSENT_GRANT_INVALID|CONSENT_GRANT_REQUIRED|CONSENT_GRANT_WRONG_CAP",
     "CONSENT_INVALID",
     "用户授权凭证无效或缺失。",
     "personal 模式检查 .env 的 CONSENT_GRANT_ID；多用户模式带 --phone 且该手机号已绑定。"
     "未绑定就走 request_code → verify_code 先拿凭证。"),

    (r"ELEME_USER_NOT_FOUND",
     "ELEME_USER_NOT_FOUND",
     "该手机号没有可绑定的淘宝闪购/饿了么账号。",
     "告诉用户：先用该手机号登录或开通淘宝闪购/饿了么后再绑定。换个已开通的手机号也行。"),

    (r"CAP_NOT_BOUND|PROVIDER_NOT_AVAILABLE",
     "CAP_NOT_BOUND",
     "该 agent 未开通外卖能力。",
     "这是平台侧配置：联系 ClawDot 平台为该 API_KEY 开通 delivery 能力后再用。不是用户能自助解决的。"),

    (r"还没绑定|USER_NOT_BOUND",
     "USER_NOT_BOUND_NEEDS_SMS",
     "用户还未完成授权绑定。",
     "把手机号和方式合成一句问：'先告诉我手机号，顺便选一下用 H5 还是验证码方式绑定哦～'"
     "（已知手机号就只问方式；不选默认短信）。\n"
     "短信：request_code --phone {phone} → 用户回 6 位码 → "
     "verify_code --phone {phone} --bind-id <真实bind_id> --code <用户的码>。\n"
     "H5：request_code --auth-type h5 --phone {phone} → 把返回的 h5_url 原样发给用户点开授权 → "
     "用户说完成后 verify_code --auth-type h5 --phone {phone} --request-id <真实request_id>。\n"
     "绑定成功后重调原业务 action 并带 --phone。bind_id/request_id 必须来自真实返回，禁止编造。"),
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
    if err.code in ("AUTH_REQUIRED", "AUTH_INVALID"):
        return "API_KEY 无效或缺失，请检查 .env 的 API_KEY 配置。"

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
    """Resolve lat/lng from CLI args > address cache > DEFAULT_* (personal only)."""
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


# ── Actions ─────────────────────────────────────────────────────────────────

def action_search(args, gw: GatewayClient, cache: Cache, config: Config,
                  cg: str, phone: str | None) -> None:
    lat, lng = _resolve_lat_lng(args, cache, config, phone)
    cache_key = f"search:{lat},{lng},{args.shop_keyword or 'default'}"
    cached = cache.get(cache_key)
    if cached:
        output(cached)
        return
    raw = gw.search_shops(cg, keyword=args.shop_keyword, lat=lat, lng=lng)
    trimmed = trim_search_results(raw)
    remember_carts(cache, trimmed["shops"])
    cache.set(cache_key, trimmed, SEARCH_TTL)
    output(trimmed)


def action_recommend(args, gw: GatewayClient, cache: Cache, config: Config,
                     cg: str, phone: str | None) -> None:
    """搜店 + 并行取 top N 家菜单一步到位。返回 {"shops": [...], "menus": [...]}。"""
    lat, lng = _resolve_lat_lng(args, cache, config, phone)
    try:
        top_n = min(int(args.top_n or 3), 5)
    except (TypeError, ValueError):
        top_n = 3

    search_cache_key = f"search:{lat},{lng},{args.shop_keyword or 'default'}"
    trimmed = cache.get(search_cache_key)
    if not trimmed:
        raw = gw.search_shops(cg, keyword=args.shop_keyword, lat=lat, lng=lng)
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


def action_menu(args, gw: GatewayClient, cache: Cache, config: Config,
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

    if args.shop_keyword:
        output(search_menu_items(menu, args.shop_keyword))
        return

    if args.category:
        detail = build_category_detail(menu, args.category)
        if not detail:
            names = "、".join(c.get("name", "") for c in menu.get("categories", []))
            die_with_hint(f'未找到分类"{args.category}"，可用分类：{names}', "CATEGORY_NOT_FOUND")
        output(detail)
        return

    output(build_menu_overview(menu))


def action_addresses(args, gw: GatewayClient, cache: Cache, config: Config,
                     cg: str, phone: str | None) -> None:
    addr_key = _addr_cache_key(phone)

    # ── Branch 1: Select (save) an address via suggestion token ──
    if args.select_token:
        if not args.contact_name or not args.contact_phone:
            die("保存地址需要 --contact-name 和 --contact-phone。")
        body: dict = {
            "suggestion_token": args.select_token,
            "contact_name": args.contact_name,
            "contact_phone": args.contact_phone,
        }
        if args.address_detail:
            body["address_detail"] = args.address_detail
        try:
            result = gw.select_address(cg, body)
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
        # Optional: set a tag via a follow-up update (select itself takes no tag).
        if args.address_tag and result.get("address_id"):
            try:
                result = gw.update_address(cg, {"address_id": result["address_id"],
                                                "tag": args.address_tag})
            except GatewayError:
                pass  # tagging is best-effort; the address is already saved
        new_addr = normalize_saved_address(result)
        existing = cache.get(addr_key)
        existing = existing if isinstance(existing, list) else []
        existing = [a for a in existing if a.get("address_id") != new_addr.get("address_id")]
        existing.insert(0, new_addr)
        cache.set(addr_key, existing, ADDRESS_TTL)
        output(new_addr)
        return

    # ── Branch 2: Search (by keyword and/or coords/city) ──
    if args.address_keyword or args.lat is not None or args.lng is not None or args.city:
        if args.city:
            call_lat, call_lng = None, None  # city beats historical coords
        else:
            call_lat, call_lng = args.lat, args.lng
        try:
            raw = gw.search_addresses(cg, keyword=args.address_keyword,
                                      lat=call_lat, lng=call_lng, city=args.city)
        except GatewayError as e:
            die(f"地址搜索失败：{friendly_error(e)}")
            return
        trimmed = normalize_address_search(raw)
        _refresh_saved_cache(cache, phone, trimmed["saved"])
        output(trimmed)
        return

    # ── Branch 3: Default — list saved + suggestions ──
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


def action_preview(args, gw: GatewayClient, cache: Cache, config: Config,
                   cg: str, phone: str | None) -> None:
    if not args.shop_id or not args.address_id or not args.items:
        die("缺少必要参数：--shop-id、--address-id、--items")
    cart_id = resolve_cart_id(cache, args.shop_id)
    items = _parse_items(args.items)

    body: dict = {
        "shop_id": args.shop_id,
        "cart_id": cart_id,
        "address_id": args.address_id,
        "items": items,
    }
    if args.note:
        body["order_remark"] = args.note

    try:
        result = gw.preview_order(cg, body)
    except GatewayError as e:
        die(friendly_error(e, {"shop_id": args.shop_id, "address_id": args.address_id}))
        return
    output(result)


def action_order(args, gw: GatewayClient, cache: Cache, config: Config,
                 cg: str, phone: str | None) -> None:
    if not args.preview_id or not args.confirmation_token:
        die("缺少 --preview-id / --confirmation-token（均来自 preview 的返回）。")
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


def action_order_status(args, gw: GatewayClient, cache: Cache, config: Config,
                        cg: str, phone: str | None) -> None:
    if not args.order_id:
        die("缺少 --order-id 参数。")
    try:
        result = gw.get_order_status(cg, args.order_id)
    except GatewayError as e:
        die(friendly_error(e))
        return
    output(result)


# ── Bind actions（不需要 consent；SMS 默认 / H5 链接授权）─────────────────────

def action_request_code(args, gw: GatewayClient, cache: Cache, config: Config) -> None:
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
                f"verify_code --auth-type h5 --phone {phone} --request-id {request_id}"
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
            f"verify_code --phone {phone} --bind-id {bind_id} --code <用户输的6位>"
        ),
    })


def action_verify_code(args, gw: GatewayClient, cache: Cache, config: Config) -> None:
    """绑定第 2 步。成功后把 consent_grant_id 按手机号写进 file/Redis 缓存。"""
    if not args.phone:
        die("缺少 --phone 参数")
    phone = normalize_phone(args.phone)

    if args.auth_type == "h5":
        if not args.request_id:
            die("缺少 --request-id 参数（来自 request_code --auth-type h5 的返回）")
        try:
            result = gw.verify_bind(auth_type="h5", request_id=args.request_id)
        except GatewayError as e:
            die(f"查询授权结果失败：{friendly_error(e, {'phone': phone})}")
            return
        if not result.get("bound"):
            status = result.get("status") or "pending"
            if status == "expired":
                die("授权链接已过期。\n"
                    f"RECOVERY[H5_BIND_EXPIRED]: 重新调 request_code --auth-type h5 --phone {phone} 拿新链接发给用户。")
            die("用户还没完成授权。\n"
                "RECOVERY[H5_BIND_PENDING]: 提醒用户点开刚才的链接完成授权；等用户说完成后用同一个 request_id 重调本命令。不要高频轮询。")
    else:
        if not args.bind_id:
            die("缺少 --bind-id 参数（来自 request_code 的返回）")
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
    file_key = f"cg:{phone}"
    ttl = _ttl_from_expires(result.get("expires_at"))
    cache.set(file_key, result, ttl)
    redis = _try_connect_redis(config)
    if redis:
        redis.setex(f"{REDIS_CG_PREFIX}{phone}", ttl, cg)
    # Persist as the default consent grant so subsequent calls work with just
    # API_KEY injected — no --phone, no manual CONSENT_GRANT_ID. Best-effort: if
    # .env isn't writable, the per-phone cache (above) still serves --phone calls.
    persisted = write_env_var(config.env_path, "CONSENT_GRANT_ID", cg)
    output({
        "consent_grant_id": cg,
        "expires_at": result.get("expires_at"),
        "scopes": result.get("scopes"),
        "phone": phone,
        "persisted_to_env": persisted,
        "message": (
            ("绑定成功，consent_grant_id 已写入 .env 作为默认用户。后续业务调用直接进行即可，无需 --phone。"
             if persisted else
             "绑定成功，consent_grant_id 已缓存（.env 不可写、未持久化）。后续业务调用带 --phone 复用此凭证。")
            + "多用户场景请始终带 --phone 指定用户。"
        ),
    })


# ── Main ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ClawDot takeout ordering (open-gateway)")
    parser.add_argument(
        "--phone", default=None,
        help="（多用户模式）用户手机号；脚本按手机号读取已缓存的 consent_grant_id。"
             "不传则退化到 personal 模式，使用 .env 里的 CONSENT_GRANT_ID。",
    )
    parser.add_argument("--action", required=True,
                        choices=["search", "menu", "recommend", "addresses",
                                 "preview", "order", "order_status",
                                 "request_code", "verify_code"])
    # search / recommend / menu cross-search
    parser.add_argument("--shop-keyword", "--keyword", dest="shop_keyword", default=None,
                        help="搜索店铺关键词（兼容旧别名 --keyword）；menu 上下文下用作菜品跨分类模糊搜。")
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lng", type=float, default=None)
    # menu
    parser.add_argument("--shop-id", default=None)
    parser.add_argument("--category", default=None)
    parser.add_argument("--item-id", default=None)
    # addresses
    parser.add_argument("--address-keyword", "--search-keyword", dest="address_keyword",
                        default=None, help="搜索地址关键词（兼容旧别名 --search-keyword）。")
    parser.add_argument("--city", default=None,
                        help="城市名（中文/拼音/缩写）。传了就覆盖历史坐标走 cityId 搜索。")
    parser.add_argument("--select-token", default=None,
                        help="suggestion 的 sug_ref（addresses search 返回的 suggestions[].sug_ref；"
                             "脚本内部作为 suggestion_token 发给网关），与 --contact-name/--contact-phone 配套。")
    parser.add_argument("--contact-name", default=None, help="收件人姓名（select 必填）")
    parser.add_argument("--contact-phone", default=None, help="收件人手机号（select 必填）")
    parser.add_argument("--address-detail", default=None, help="门牌/楼层/室号；POI suggestion 必填")
    parser.add_argument("--address-tag", default=None, help="标签：home/work/school（select 后顺带设置）")
    # preview
    parser.add_argument("--address-id", default=None, help="平台地址 id（addr_…）")
    parser.add_argument("--items", default=None,
                        help='JSON array：[{"item_id":"item_x","quantity":1,"sku_id":"sku_y",'
                             '"ingredient_option_ids":["opt_z"],"remark":"少冰"}]')
    parser.add_argument("--note", default=None, help="订单备注（order_remark）")
    # order
    parser.add_argument("--preview-id", default=None, help="preview 返回的 preview_id（prv_…）")
    parser.add_argument("--confirmation-token", default=None,
                        help="preview 返回的 confirmation_token（cf_…）")
    # order_status
    parser.add_argument("--order-id", default=None)
    # recommend
    parser.add_argument("--top-n", default=None, help="recommend：拉菜单的店铺数，默认 3、最多 5")
    # Bind (request_code / verify_code)
    parser.add_argument("--auth-type", default="sms", choices=["sms", "h5"],
                        help="绑定授权方式：sms（默认，短信验证码）/ h5（授权链接，用户点开授权后轮询结果）")
    parser.add_argument("--bind-id", default=None, help="（sms verify_code 必填）request_code 返回的 bind_id")
    parser.add_argument("--code", default=None, help="（sms verify_code 必填）用户回复的 6 位短信验证码")
    parser.add_argument("--request-id", default=None,
                        help="（h5 verify_code 必填）request_code --auth-type h5 返回的 request_id")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config()

    if not config.api_key:
        gw_url = config.gateway_url
        if gw_url.startswith("http://127.0.0.1"):
            gw_url = "https://clawdot.hicaspian.com/gateway"
        die(
            "还没配置外卖服务的 API_KEY。\n"
            f"让用户打开 {config.setup_url} 登录/注册 ClawDot 拿到 API_KEY，原文发回来；"
            "收到后写入本 skill 根目录 .env，内容两行：\n"
            f"GATEWAY_URL={gw_url}\n"
            "API_KEY=<用户发来的key>\n"
            "不要复述或展示 key。写好后接着问绑定信息。\n"
            "RECOVERY[API_KEY_MISSING]: ① 把注册链接发给用户等 key → ② 写入 .env → "
            "③ 一句话问齐：'先告诉我手机号，顺便选一下用 H5 还是验证码方式绑定哦～'"
        )

    gw = GatewayClient(config)
    cache = Cache()

    # ── 用户绑定流程（不需要 consent）──────────────────────────────
    if args.action == "request_code":
        action_request_code(args, gw, cache, config)
        return
    if args.action == "verify_code":
        action_verify_code(args, gw, cache, config)
        return

    # ── 其他业务 action 必须先解析 consent_grant_id ──────────────────
    # 解析优先级（不带 --phone）：env CONSENT_GRANT_ID（含绑定后回写的）→ 缓存唯一已绑用户
    #   → 多个则要求 --phone → 否则引导绑定。带 --phone：Redis → 文件缓存 → 引导绑定。
    redis = _try_connect_redis(config)
    cg = resolve_consent_grant(args.phone, cache, redis, config)

    actions = {
        "search": action_search,
        "menu": action_menu,
        "recommend": action_recommend,
        "addresses": action_addresses,
        "preview": action_preview,
        "order": action_order,
        "order_status": action_order_status,
    }
    try:
        actions[args.action](args, gw, cache, config, cg, args.phone)
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
