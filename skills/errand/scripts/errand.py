#!/usr/bin/env python3
"""跑腿助手驱动 — 帮真实客户下同城跑腿单（open-gateway errand MCP 面，纯标准库无依赖）。

每个子命令 = 一次 JSON-RPC ``tools/call`` POST 到 ``GATEWAY_MCP_URL``（默认
``https://paotui.hicaspian.com/mcp/v1``，stateless）。镜像 takeout(clawdot.py) 家法：
  · 鉴权：``Authorization: Bearer <api_key>`` 认 agent；用户态 cg 作 ``consent_grant_id``
    **工具参数**（绑定类工具不带）。
  · 成功 → JSON 打到 stdout，exit 0；失败 → 中文错误 + ``RECOVERY[CODE]`` 打到 stderr，exit 1。
  · 凭证：cg 由用户走一次短信绑定产生，写【能力分格】共享缓存
    ``~/.clawdot/errand-credentials.json``（按 API_KEY 指纹+手机号键控）。跑腿授权与外卖独立，
    各写各的文件、同号两条能力的 cg 天然并存不互踢。
  · 客户付款：create/add_tip 返回收银台链接（cashier_url），客户自己点开付——本脚本
    **不模拟支付、不碰回调**。
"""

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
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_MCP_URL = "https://paotui.hicaspian.com/mcp/v1"


# ── Config ──────────────────────────────────────────────────────────────────

@dataclass
class Config:
    mcp_url: str
    api_key: str
    consent_grant_id: str
    setup_url: str
    timeout_ms: int
    clawdot_home: Path


def load_dotenv(path: Path) -> None:
    """Minimal .env loader — no dependency on python-dotenv."""
    if not path.is_file():
        return
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
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
    return Config(
        mcp_url=normalize_mcp_url(os.environ.get("GATEWAY_MCP_URL", DEFAULT_MCP_URL)),
        api_key=os.environ.get("API_KEY", ""),
        consent_grant_id=os.environ.get("CONSENT_GRANT_ID", ""),
        setup_url=os.environ.get(
            "CLAWDOT_SETUP_URL",
            "https://clawdot.hicaspian.com/developer/login",
        ),
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
        # 每个错误可能带 next_action 枚举——比 code 字符串更稳的路由信号。
        self.next_action = next_action


class MCPClient:
    """open-gateway errand MCP client — one stateless JSON-RPC tools/call per action.

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
            "User-Agent": "ClawDot-Errand-CLI/1.0",
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

    # ── 绑定（不带 consent；跑腿仅短信模式）──────────────────────────────────

    def request_bind(self, phone: str, external_user_id: str | None = None) -> dict:
        """绑定第 1 步：给手机号发短信验证码，返回 {bind_id, expires_in, masked_phone}。"""
        return self._call("errand_request_user_bind",
                          {"phone": phone, "external_user_id": external_user_id})

    def verify_bind(self, bind_id: str, code: str) -> dict:
        """绑定第 2 步：核验验证码，返回 {bound, consent_grant_id, scopes, expires_at}。"""
        return self._call("errand_verify_user_bind", {"bind_id": bind_id, "code": code})

    # ── 授权状态（用户态）────────────────────────────────────────────────────

    def get_auth_status(self, cg: str) -> dict:
        """查这枚 cg 是否仍有效。**凭证无效不报错**——看返回的 bound 字段判断要不要重绑。"""
        return self._call("errand_get_auth_status", {"consent_grant_id": cg})

    def revoke_bind(self, cg: str, reset_history: bool = False) -> dict:
        """解绑。reset_history=True 连地址簿与历史单一并清退，**不可逆**。"""
        return self._call("errand_revoke_user_bind", {
            "consent_grant_id": cg, "reset_history": reset_history or None,
        })

    # ── 业务（用户态，consent 作为参数）──────────────────────────────────────

    def list_addresses(self, cg: str) -> dict:
        return self._call("errand_list_addresses", {"consent_grant_id": cg})

    def search_addresses(self, cg: str, *, keyword: str | None = None,
                         city: str | None = None,
                         lat: float | None = None, lng: float | None = None) -> dict:
        """搜地址。keyword 与 lat/lng 至少给一样：给 keyword=按地名搜（可加 city 缩范围）；
        只给 lat/lng=按用户当前位置反查地点名。返回 {candidates, saved_matches}——
        saved_matches 是用户已存过且命中本次搜索的地址，门牌电话都齐，选它可直接下单。"""
        return self._call("errand_search_addresses", {
            "consent_grant_id": cg, "keyword": keyword, "city": city,
            "lat": lat, "lng": lng,
        })

    def update_address(self, cg: str, *, address_id: str, contact_name: str | None = None,
                       contact_phone: str | None = None, address: str | None = None,
                       lat: float | None = None, lng: float | None = None,
                       detail: str | None = None, tag: str | None = None) -> dict:
        """改址：只传要改的字段，没传的保持原样。改**位置**须 address+lat+lng 三个一起传。"""
        args: dict = {"consent_grant_id": cg, "address_id": address_id,
                      "contact_name": contact_name, "contact_phone": contact_phone,
                      "address": address, "lat": lat, "lng": lng, "detail": detail}
        # tag="" 是「清空标签」的合法值，会被 _call 的 None 过滤放行、但不能写成 None，
        # 故此处显式判 None 再塞（其余字段空串按「没传」处理，与网关口径一致）
        if tag is not None:
            args["tag"] = tag
        return self._call("errand_update_address", args)

    def delete_address(self, cg: str, address_id: str) -> dict:
        """删址，**不可撤销**。返回 {address_id, deleted, deleted_count}。"""
        return self._call("errand_delete_address", {
            "consent_grant_id": cg, "address_id": address_id,
        })

    # ── 询价辅助清单（摆给用户挑）────────────────────────────────────────────

    def list_goods_categories(self, cg: str) -> dict:
        """物品品类清单（15 项，前 7 项 valuable=贵重）。选中项的 code 回填 quote。
        **理赔只看品类码、不看物品名称**，所以要选对。清单固定、可缓存。"""
        return self._call("errand_list_goods_categories", {"consent_grant_id": cg})

    def list_schedule_slots(self, cg: str) -> dict:
        """可预约送达档位（一档 15 分钟，最早 45 分钟后、最晚明天 23:45）。
        **只能把清单里的 value 原样回填 quote 的 --scheduled-at**，自己算的会被拒。"""
        return self._call("errand_list_schedule_slots", {"consent_grant_id": cg})

    def save_address(self, cg: str, *, contact_name: str, address: str,
                     lat: float, lng: float, detail: str = "", tag: str = "",
                     contact_phone: str = "") -> dict:
        return self._call("errand_save_address", {
            "consent_grant_id": cg, "contact_name": contact_name, "address": address,
            "lat": lat, "lng": lng, "detail": detail, "tag": tag,
            "contact_phone": contact_phone,
        })

    def list_orders(self, cg: str, *, limit: int = 5, offset: int = 0,
                    status: str | None = None, created_after: str | None = None,
                    created_before: str | None = None) -> dict:
        """历史单。返回 **{orders, next_offset}**（next_offset=null 表示没有更多）。
        status 逗号分隔可多传；created_after/before 按北京时间、形如 2026-08-01[ 10:30:00]。"""
        return self._call("errand_list_orders", {
            "consent_grant_id": cg, "limit": limit, "offset": offset or None,
            "status": status, "created_after": created_after,
            "created_before": created_before,
        })

    def quote(self, cg: str, *, from_address: dict, to_address: dict, goods: list[dict],
              total_weight_g: int | None = None, scheduled_at: int | None = None,
              person_direct: bool = False, insured: bool = False,
              remark: str | None = None, goods_category_code: int | None = None) -> dict:
        """询价。goods_category_code 取自 list_goods_categories——**理赔以品类为准、与
        物品名称无关**，不传则退回平台默认品类。scheduled_at 只收 list_schedule_slots
        给出的档位值。物品名与 remark 都会做禁运校验（命中回 GOODS_PROHIBITED）。"""
        return self._call("errand_quote", {
            "consent_grant_id": cg, "from_address": from_address, "to_address": to_address,
            "goods": goods, "total_weight_g": total_weight_g, "scheduled_at": scheduled_at,
            "person_direct": person_direct, "insured": insured, "remark": remark,
            "goods_category_code": goods_category_code,
        })

    def create(self, cg: str, *, quote_id: str, company_code: int,
               callback_url: str | None = None) -> dict:
        return self._call("errand_create", {
            "consent_grant_id": cg, "quote_id": quote_id,
            "company_code": company_code, "callback_url": callback_url,
        })

    def get_order(self, cg: str, order_id: str) -> dict:
        return self._call("errand_get_order", {"consent_grant_id": cg, "order_id": order_id})

    def get_rider(self, cg: str, order_id: str) -> dict:
        return self._call("errand_get_rider", {"consent_grant_id": cg, "order_id": order_id})

    def pre_cancel(self, cg: str, order_id: str) -> dict:
        return self._call("errand_pre_cancel", {"consent_grant_id": cg, "order_id": order_id})

    def cancel(self, cg: str, order_id: str, reason: str | None = None) -> dict:
        return self._call("errand_cancel", {
            "consent_grant_id": cg, "order_id": order_id, "reason": reason,
        })

    def add_tip(self, cg: str, order_id: str, tip_fee: int) -> dict:
        return self._call("errand_add_tip", {
            "consent_grant_id": cg, "order_id": order_id, "tip_fee": tip_fee,
        })


# ── Shared credential store（能力分格：errand 专属文件）─────────────────────
#
# cg 唯一持久化源：$CLAWDOT_HOME/errand-credentials.json（默认 ~/.clawdot/）。
# 结构：{ "<sha256(API_KEY)[:12]>": { "<phone>": {"consent_grant_id", "expires_at",
# "updated_at"} } }。**按能力分格**——跑腿写 errand-credentials.json、外卖写
# credentials.json，各写各的文件；同一手机号在两条能力下各持一个 cg（cap 不互通，
# 分能力发放），文件级隔离故天然不互踢。跨实例（不同 key）天然隔离；skill 升级/重装
# 不丢绑定。不回写 .env（CONSENT_GRANT_ID env 只读预注入）。

class CredStore:
    def __init__(self, api_key: str, home: Path):
        self.path = home / "errand-credentials.json"
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
        # errand-credentials.json 存 bearer 等价凭证 → 目录 0700、文件 0600。
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

    def all(self) -> dict[str, str]:
        """{phone: cg} for all non-expired bound users under this API_KEY."""
        out: dict[str, str] = {}
        for phone, entry in self._data.get(self.fingerprint, {}).items():
            if isinstance(entry, dict) and not self._expired(entry):
                cg = entry.get("consent_grant_id")
                if cg:
                    out[phone] = cg
        return out

    def drop(self, phone: str) -> None:
        """解绑后作废**该号**的本地缓存——留着只会拿已失效的 cg 一直撞 CONSENT_GRANT_INVALID。

        刻意只提供单号删除、不提供「清空本指纹」：解绑是不可逆动作，批量清会波及同一
        API_KEY 下其他用户的有效凭证（他们网关侧还绑着，本地却没了、得重走短信绑定）。
        """
        bucket = self._data.get(self.fingerprint, {})
        if bucket.pop(phone, None) is not None:
            self._save()


# ── Consent Grant Resolution ─────────────────────────────────────────────────

def resolve_consent_grant(phone: str | None, creds: CredStore, config: Config) -> str:
    """Return the consent_grant_id (cg_) for the call.

    不带 --phone：``CONSENT_GRANT_ID`` env（只读预注入）→ 共享缓存中该 API_KEY
    指纹下唯一已绑用户 → 多个要求 --phone → 否则引导绑定。
    带 --phone：查共享缓存该手机号。缓存 miss → 引导 SMS 绑定（**绝不静默重绑**：
    重绑会轮换作废旧 cg，必须由用户走 request_user_bind → verify_user_bind）。"""
    if phone is None:
        if config.consent_grant_id:
            return config.consent_grant_id
        bound = creds.all()
        if len(bound) == 1:
            return next(iter(bound.values()))
        if len(bound) > 1:
            die_with_hint(
                "共享缓存里有多个已绑用户，无法确定给谁下单。",
                "MULTIPLE_USERS_NEED_PHONE",
            )
        die_with_hint("还没有已绑定的跑腿用户。", "USER_NOT_BOUND_NEEDS_SMS")
    phone = normalize_phone(phone)
    cg = creds.get(phone)
    if cg:
        return cg
    if config.consent_grant_id:
        return config.consent_grant_id
    die_with_hint(
        f"手机号 {mask_phone(phone)} 还没完成跑腿绑定。",
        "USER_NOT_BOUND_NEEDS_SMS",
        ctx={"phone": phone},
    )
    return ""  # unreachable（die_with_hint exits）


# ── Error playbook：gateway error → 中文 + RECOVERY 提示 ─────────────────────

ERROR_PLAYBOOK: list[tuple[str, str, str, str]] = [
    # 注意：不能用裸 `QUOTE.*INVALID`——会吞掉 QUOTE_FEE_INVALID（那是"金额异常，别硬下单"，
    # 语义与"报价过期"相反）。只匹配过期专属码 QUOTE_INVALID_OR_EXPIRED / QUOTE_EXPIRED。
    # 也**不能**再把 PUBLIC_REFERENCE_INVALID 并进来：文档 v3.0 起它专指 callback_url
    # 不是公网地址，与报价过期无关（并进来会让用户收到"重新询价"这种驴唇不对马嘴的指引）。
    (r"QUOTE_INVALID_OR_EXPIRED|QUOTE.*EXPIRED|报价.*(失效|过期)|令牌.*(失效|过期)",
     "QUOTE_EXPIRED",
     "报价已过期或失效。",
     "报价有效期约 10 分钟。用同样的收发地址/物品重新 quote 拿新的 quote_id，再 create。"),

    (r"PUBLIC_REFERENCE_INVALID",
     "CALLBACK_URL_INVALID",
     "回调地址不可用。",
     "create 的 --callback-url 必须是公网可访问的 http(s) 地址，localhost/内网地址会被拒。"
     "一般用不到这个参数，去掉重下即可。"),

    # ── 预约时间（v3.0：只收 list_schedule_slots 给出的档位）──
    (r"SCHEDULED_AT_NOT_ON_GRID",
     "SCHEDULED_AT_NOT_ON_GRID",
     "预约时间不在可选档位上。",
     "**别自己算时间戳**。调 list_schedule_slots 拿档位清单，把用户选中那档的 value 原样回填。"),

    (r"SCHEDULED_AT_PAST",
     "SCHEDULED_AT_PAST",
     "预约时间已经过去了。",
     "复用历史单最常撞这个——旧单的预约时间早过期了，别照抄。重新 list_schedule_slots 问用户约几点。"),

    (r"SCHEDULED_AT_TOO_SOON",
     "SCHEDULED_AT_TOO_SOON",
     "预约时间太近了。",
     "最早只能约 45 分钟后；错误消息里给了当前最早可约时间。要马上送就别传 --scheduled-at（即时单）。"),

    (r"SCHEDULED_AT_TOO_FAR",
     "SCHEDULED_AT_TOO_FAR",
     "预约时间太远了。",
     "最晚只能约到明天 23:45（北京时间）。跟用户说只能约到明天，重新选一档。"),

    (r"SCHEDULED_AT_INVALID",
     "SCHEDULED_AT_INVALID",
     "预约时间格式不对。",
     "须是 13 位**毫秒**时间戳（传成秒/小数/负数都会命中）。直接用 list_schedule_slots 的 value，别自己转。"),

    # ── 物品品类与禁运 ──
    (r"GOODS_PROHIBITED",
     "GOODS_PROHIBITED",
     "这个东西跑腿不能寄。",
     "物品名或备注命中禁运（毒品/枪爆/危化品/违法交易物），错误消息会点名具体词与类别。"
     "**如实告诉用户该物品无法配送，禁止改词重试绕过**。"
     "若是正常商品被误判（如「大麻花」这类含敏感字的合法食品），换个更准确的名称重新询价。"),

    (r"GOODS_CATEGORY_INVALID",
     "GOODS_CATEGORY_INVALID",
     "物品品类码不对。",
     "--goods-category-code 必须取自 list_goods_categories 返回的 code；重新拉一次清单再选。"),

    (r"REMARK_TOO_LONG",
     "REMARK_TOO_LONG",
     "给骑手的留言太长了。",
     "--remark 最多 200 字，精简后重试。"),

    # ── 历史单筛选 ──
    (r"ERRAND_STATUS_INVALID",
     "ERRAND_STATUS_INVALID",
     "订单状态筛选值不支持。",
     "--status 取订单状态原值（如 dispatching,delivering），逗号分隔；错误消息里列出了全部可选值。"),

    (r"ERRAND_TIME_RANGE_INVALID",
     "ERRAND_TIME_RANGE_INVALID",
     "时间筛选格式不对。",
     "--created-after/--created-before 写 2026-08-01 或 '2026-08-01 10:30:00'（北京时间，别自己换算时区）。"),

    # ── 改址 / 删址 ──
    (r"ADDRESS_COORDS_PAIRED",
     "ADDRESS_COORDS_PAIRED",
     "改地址位置要地址和坐标一起改。",
     "--address --lat --lng 三个一起传（只改文本不改坐标，骑手会按旧坐标去旧地方）。"
     "先 search_addresses 搜到新地点拿坐标。"),

    (r"ADDRESS_DUPLICATE",
     "ADDRESS_DUPLICATE",
     "改完会和已有的另一条地址重复。",
     "地址+门牌+联系人三者都相同即视为同一条。换个门牌/联系人，或直接用已有的那条。"),

    (r"ADDRESS_UPDATE_EMPTY",
     "ADDRESS_UPDATE_EMPTY",
     "没有指定要改什么。",
     "至少给一个要改的字段：--contact-name/--contact-phone/--detail/--tag，或 --address+--lat+--lng。"),

    (r"ADDRESS_FIELD_TOO_LONG",
     "ADDRESS_FIELD_TOO_LONG",
     "地址信息太长了。",
     "地址/联系人/门牌/标签/电话有长度上限，精简后重试。"),

    (r"CONSENT_GRANT_EXPIRED|AUTH_EXPIRED|授权.*过期",
     "CONSENT_EXPIRED",
     "用户授权已过期。",
     "引导该用户重新授权：request_user_bind --phone {phone} → 用户回 6 位码 → "
     "verify_user_bind --phone {phone} --bind-id <真实bind_id> --code <用户的码>。重绑后重试原命令。"),

    (r"CONSENT_GRANT_INVALID|CONSENT_GRANT_REQUIRED|CONSENT_GRANT_WRONG_CAP",
     "CONSENT_INVALID",
     "用户授权凭证无效或缺失。",
     "预注入模式检查 CONSENT_GRANT_ID 环境变量；多用户模式带 --phone 且该手机号已完成【跑腿】绑定。"
     "未绑定就走 request_user_bind → verify_user_bind 先拿凭证。注意跑腿授权与外卖独立、需单独绑定。"),

    (r"CAP_NOT_BOUND|CAPABILITY_FORBIDDEN|PROVIDER_NOT_AVAILABLE",
     "CAP_NOT_BOUND",
     "该 agent 未开通跑腿能力。",
     "这是平台侧配置：联系 ClawDot 平台为该 API_KEY 开通 errand 能力后再用。不是用户能自助解决的。"),

    (r"BINDING_LIMIT_REACHED",
     "BINDING_LIMIT_REACHED",
     "该 agent 已达可绑定用户数上限。",
     "平台侧配额问题：先解绑某个已绑用户（或联系 ClawDot 提升 max_bindings 配额）再绑新用户。"),

    (r"ERRAND_CROSS_CITY|跨城|不在同一城市|不同城市",
     "ERRAND_CROSS_CITY",
     "跑腿只支持同城配送，收发地址不在同一城市。",
     "确认取件/送达是同城；跨城需求要如实告诉用户跑腿做不了。"),

    (r"ERRAND_CITY_NOT_OPEN|CITY_NOT_OPEN|城市.*未开通|未开通.*城市",
     "ERRAND_CITY_NOT_OPEN",
     "该城市暂未开通跑腿服务。",
     "告诉用户这个城市暂时叫不到跑腿；换已开通城市的地址再试。"),

    (r"COORDS_REQUIRED|缺.*坐标|坐标.*缺",
     "COORDS_REQUIRED",
     "地址缺少坐标。",
     "纯地址文本无法下单：先 search_addresses 让用户选中候选拿到坐标，再回填 quote 的 from/to。"),

    (r"ADDRESS_REQUIRED|某一端缺地址|地址.*缺失",
     "ADDRESS_REQUIRED",
     "收发某一端缺地址。",
     "地址簿有 → 用 --from-id / --to-id；新地址 → 先 search_addresses 让用户选，"
     "再传 --from-text/--from-lat/--from-lng（收件端 --to-* 同理）。"),

    (r"还没绑定|USER_NOT_BOUND|NOT_BOUND",
     "USER_NOT_BOUND_NEEDS_SMS",
     "用户还未完成跑腿授权绑定。",
     "跑腿绑定走一次性短信验证码（无 H5）。先问手机号：\n"
     "request_user_bind --phone {phone} → 用户回 6 位码 → "
     "verify_user_bind --phone {phone} --bind-id <真实bind_id> --code <用户的码>。\n"
     "绑定成功后重调原业务命令并带 --phone。bind_id 必须来自真实返回，禁止编造。"
     "注意：跑腿授权与外卖独立，已绑外卖不代表已绑跑腿。"),

    # ── 绑定过程 ──
    (r"SMS_COOLDOWN|冷却|发送过于频繁",
     "SMS_COOLDOWN",
     "验证码刚发过，还在冷却期（约 60 秒）。",
     "别重复发。跟用户说「稍等一下再发」，或直接问他上一条短信里的码。"),

    (r"SMS_CODE_INVALID|验证码.*(错误|无效|过期)",
     "SMS_CODE_INVALID",
     "验证码不对或已过期。",
     "让用户核对最新一条短信重报；超过有效期就 request_user_bind 重发。禁止编码重试。"),

    # ── 下单要素 ──
    (r"CONTACT_REQUIRED|缺.*联系人|联系人.*缺",
     "CONTACT_REQUIRED",
     "收发某一端缺联系人或电话。",
     "走地址簿 id 时通常自动带出联系人/电话；这端没带出来说明地址簿没存 → "
     "问用户要一次姓名+电话，传 --to-name/--to-phone（发件端 --from-*）。"),

    (r"GOODS_REQUIRED|至少.*货品|缺.*货品",
     "GOODS_REQUIRED",
     "没说送什么东西。",
     "问一句「送什么？」，把用户原话填 --goods-name 即可，不必追问品类。"),

    (r"ADDRESS_TEXT_REQUIRED|必须.*address_text",
     "ADDRESS_TEXT_REQUIRED",
     "给了坐标但没给地址文本。",
     "传坐标时必须同时传 --from-text/--to-text（用 search_addresses 候选的 name+address）。"),

    (r"ADDRESS_INCOMPLETE|地址.*不完整",
     "ADDRESS_INCOMPLETE",
     "地址或坐标不完整，存不了。",
     "save_address 三样必给：--address --lat --lng，坐标取自 search_addresses 候选。"),

    (r"ADDRESS_NOT_FOUND|地址.*不存在",
     "ADDRESS_NOT_FOUND",
     "这个地址不在该用户名下。",
     "address_id 必须来自本手机号的 list_addresses；换成重新 list 出来的 id，或走坐标形态。"),

    (r"KEYWORD_REQUIRED|缺.*关键词",
     "KEYWORD_REQUIRED",
     "没给搜索关键词。",
     "search_addresses 要么给 --keyword（地名），要么给 --lat --lng（按用户当前位置搜）；"
     "两者至少给一样。"),

    # ── 地址服务 ──
    (r"ADDRESS_SEARCH_FAILED|搜索失败",
     "ADDRESS_SEARCH_FAILED",
     "地址搜索没成功。",
     "换更具体的关键词（带商圈/路名）重搜一次，或加 --city 缩范围。还不行就请用户换个说法。"),

    (r"ADDRESS_LOCATE_FAILED|定位失败|解析.*坐标.*失败",
     "ADDRESS_LOCATE_FAILED",
     "这个地址定不了位。",
     "让用户给更精确的地址（带门牌/楼号/校区名），重新 search_addresses 选一次。"),

    (r"ERRAND_LOCATE_UNAVAILABLE|地址搜索服务.*未开通",
     "ERRAND_LOCATE_UNAVAILABLE",
     "地址搜索服务暂时不可用。",
     "平台侧未配定位服务，不是用户问题。改让用户从 list_addresses 里选已存地址下单。"),

    # ── 报价 / 下单 ──
    (r"ERRAND_NO_QUOTE|无可用运力",
     "ERRAND_NO_QUOTE",
     "这两个点之间暂时没有运力接单。",
     "换个地址或稍后重试一次；仍不行就如实告诉用户这趟叫不到跑腿。"),

    (r"COMPANY_NOT_IN_QUOTE|运力.*不在.*报价",
     "COMPANY_NOT_IN_QUOTE",
     "选的运力不在本次报价里。",
     "--company-code 必须取自**本次** quote 返回的 quotes[]；重新 quote 再从新报价里选。"),

    (r"ERRAND_FEE_CHANGED|配送费.*变|费用.*变动",
     "ERRAND_FEE_CHANGED",
     "配送费刚变了。",
     "静默重新 quote 拿新价，跟用户说一句「我重新算了下价」，再确认下单。"),

    (r"QUOTE_FEE_INVALID|报价金额异常",
     "QUOTE_FEE_INVALID",
     "报价金额异常。",
     "别硬下单。重新 quote 一次；仍异常就告诉用户稍后再试。"),

    (r"ERRAND_SHOP_NOT_CONFIGURED|跑腿服务暂未开通",
     "ERRAND_SHOP_NOT_CONFIGURED",
     "该城市还没开通跑腿。",
     "平台侧未配该城市，不是用户问题。告诉用户这个城市暂时叫不到跑腿。"),

    # ── 订单 / 售后 ──
    (r"ERRAND_ORDER_NOT_FOUND|订单.*不存在",
     "ERRAND_ORDER_NOT_FOUND",
     "查不到这一单。",
     "--order-id 必须是 create 返回的那个（err_ 开头）；不确定就先 list_orders 拉近几单。"),

    (r"ERRAND_NO_RIDER|无骑手信息",
     "ERRAND_NO_RIDER",
     "这单还没有骑手。",
     "未付款或刚下单还没派单时没有骑手位置。跟用户说「骑手还没接单」，付款后过会儿再查。"),

    (r"ERRAND_CANCEL_NOT_ALLOWED|不.*允许.*取消|无法取消",
     "ERRAND_CANCEL_NOT_ALLOWED",
     "这单现在不能取消。",
     "已完成/已取消的单取消不了，如实告诉用户当前状态（先 get_order 看一眼再回答）。"),

    (r"ERRAND_TIP_NOT_ALLOWED|不.*允许.*小费",
     "ERRAND_TIP_NOT_ALLOWED",
     "这单现在加不了小费。",
     "骑手已接单/单子已终态就加不了。先 get_order 看状态再跟用户解释。"),

    (r"ERRAND_TIP_INVALID|小费.*(金额|无效)",
     "ERRAND_TIP_INVALID",
     "小费金额不对。",
     "--tip-fee 单位是**分**且必须大于 0（¥2 传 200）。跟用户确认金额后重试。"),

    # ── 支付 / 上游 ──
    (r"CASHIER_UNAVAILABLE|收银台.*不可用",
     "CASHIER_UNAVAILABLE",
     "支付服务暂时不可用。",
     "平台侧问题，不是用户问题。别重复下单（可能已产生待付单），让用户稍后再试。"),

    (r"PAYMENT_AMOUNT_MISMATCH|金额.*不(符|一致)",
     "PAYMENT_AMOUNT_MISMATCH",
     "支付金额与订单不符，已拦截。",
     "资金安全拦截，别绕过、别重试下单。让用户联系客服核对这一单。"),

    (r"ERRAND_TEMPORARILY_UNAVAILABLE|暂时不可用",
     "ERRAND_TEMPORARILY_UNAVAILABLE",
     "跑腿服务暂时不可用。",
     "上游波动。稍后重试一次；连续失败就告诉用户过会儿再叫。"),

    (r"ERRAND_PROVIDER_CONFIG_ERROR|provider.*配置",
     "ERRAND_PROVIDER_CONFIG_ERROR",
     "跑腿服务没配好。",
     "平台侧配置问题，不是用户问题。别重试，告诉用户暂时用不了。"),

    (r"ERRAND_UPSTREAM_ERROR|上游.*(异常|错误)",
     "ERRAND_UPSTREAM_ERROR",
     "跑腿上游返回异常。",
     "重试一次；仍失败就跟用户说「这趟暂时叫不到跑腿」，**禁止**编造具体原因。"),
]

_PLACEHOLDER_DEFAULTS = {
    "phone": "<11位手机号>",
    "order_id": "<order_id>",
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
    if (err.next_action or "") in (
        "request_user_bind", "verify_user_bind",
        "errand_request_user_bind", "errand_verify_user_bind",
    ):
        found = _lookup_by_code("USER_NOT_BOUND_NEEDS_SMS")
        if found:
            _c, user_msg, hint = found
            return f"{user_msg}\nRECOVERY[USER_NOT_BOUND_NEEDS_SMS]: {_format_recovery(hint, ctx)}"

    if err.code in ("AUTH_REQUIRED", "AUTH_INVALID"):
        return ("API_KEY 无效或缺失。\n"
                "RECOVERY[API_KEY_INVALID]: 检查/更换 .env 里的 API_KEY（clw_）；"
                "还没有 key 就按注册页引导用户获取后写入 .env。")

    haystack = f"{err.code} {err.args[0] if err.args else ''}"
    matched = _lookup_by_pattern(haystack)
    if matched:
        code, user_msg, hint = matched
        return f"{user_msg}\nRECOVERY[{code}]: {_format_recovery(hint, ctx)}"
    raw = err.args[0] if err.args else err.code
    return f"请求失败：{raw}"


def die_with_hint(user_msg: str, code: str, ctx: dict | None = None,
                  extra: dict | None = None) -> None:
    """Emit a die() with a RECOVERY hint (looked up by code) appended."""
    parts: list[str] = [user_msg]
    if extra is not None:
        parts.append(json.dumps(extra, ensure_ascii=False))
    found = _lookup_by_code(code)
    if found:
        _c, _u, hint = found
        parts.append(f"RECOVERY[{code}]: {_format_recovery(hint, ctx)}")
    else:
        parts.append(f"RECOVERY[{code}]: 见上。")
    die("\n".join(parts))


# ── 收/发一端要素 ───────────────────────────────────────────────────────────

def _endpoint(args, side: str, default_name: str, self_phone: str | None) -> dict:
    """收/发一端：--<side>-id（地址簿），或 --<side>-text/lat/lng（POI 搜索选中的坐标）。

    联系人/电话优先级：**显式入参 > 地址簿存的 > 主号兜底（仅发件）/ 问一次（收件）**。

    · **地址簿形态**（--<side>-id）：显式传了才带，没传就**留空**——网关用该地址存的
      联系人/电话兜底（"给妈妈寄"不必每单重报）。这里若擅自填主号，网关"入参优先"就永远
      命中入参、地址簿电话成死数据，骑手拿到的还是下单人自己的号。
    · **坐标形态**：没有地址簿可兜底。
      - 发件端（self_phone=主号）：寄件人默认是本人 → 兜底主号+占位名，不打扰用户。
      - 收件端（self_phone=None）：**绝不拿下单人号当收件人**——第一次给别人寄东西走 POI 时
        留空，网关回 CONTACT_REQUIRED，按 playbook 问一次姓名+电话（问完 save_address 存起来，
        下次走地址簿自动带）。这正是"骑手别打给下单人"的关键。
    """
    aid = getattr(args, f"{side}_id")
    text = getattr(args, f"{side}_text")
    lat = getattr(args, f"{side}_lat")
    lng = getattr(args, f"{side}_lng")
    name = getattr(args, f"{side}_name")
    ph = getattr(args, f"{side}_phone")
    if aid:
        ep: dict = {"address_id": str(aid)}
        if name:
            ep["contact_name"] = name
        if ph:
            ep["contact_phone"] = ph
        return ep
    if text and lat is not None and lng is not None:
        ep = {"address_text": text, "lat": lat, "lng": lng}
    else:
        die_with_hint(
            f"{'发件' if side == 'from' else '收件'}端缺地址。",
            "ADDRESS_REQUIRED",
        )
        return {}  # unreachable
    # 收件端 self_phone=None → 不兜底主号/占位名，交给网关 CONTACT_REQUIRED 触发问一次
    ep["contact_name"] = name or (default_name if self_phone else "")
    ep["contact_phone"] = ph or (self_phone or "")
    return ep


# ── 绑定命令（不需要 consent）────────────────────────────────────────────────

def cmd_request_user_bind(args, gw: MCPClient, creds: CredStore, config: Config) -> None:
    """绑定第 1 步：给手机号发短信验证码，返回 bind_id（跑腿仅短信模式）。"""
    if not args.phone:
        die("缺少 --phone 参数（用户手机号，11 位数字）")
    phone = normalize_phone(args.phone)
    masked = mask_phone(phone)
    try:
        result = gw.request_bind(phone, external_user_id=args.external_user_id)
    except GatewayError as e:
        die(f"发送验证码失败：{friendly_error(e, {'phone': phone})}")
        return
    bind_id = result.get("bind_id")
    if not bind_id:
        die(f"发送成功但 gateway 未返回 bind_id：{result}")
    output({
        "bind_id": bind_id,
        "phone": phone,
        "phone_masked": result.get("masked_phone") or masked,
        "expires_in": result.get("expires_in"),
        "next_step": (
            f"已发短信到 {masked}，请告诉用户回复 6 位验证码。用户回复后调用："
            f"verify_user_bind --phone {phone} --bind-id {bind_id} --code <用户输的6位>"
        ),
    })


def cmd_verify_user_bind(args, gw: MCPClient, creds: CredStore, config: Config) -> None:
    """绑定第 2 步：核验验证码，成功后把 consent_grant_id 按 (API_KEY 指纹, phone) 写进共享缓存。"""
    if not args.phone:
        die("缺少 --phone 参数")
    phone = normalize_phone(args.phone)
    if not args.bind_id:
        die("缺少 --bind-id 参数（来自 request_user_bind 的返回）")
    if not args.code:
        die("缺少 --code 参数（用户输的 6 位短信验证码）")
    try:
        result = gw.verify_bind(bind_id=args.bind_id, code=args.code)
    except GatewayError as e:
        die(f"验证失败：{friendly_error(e, {'phone': phone})}")
        return
    cg = result.get("consent_grant_id")
    if not cg:
        die(f"验证通过但 gateway 未返回 consent_grant_id：{result}")
    creds.set(phone, cg, result.get("expires_at"))
    output({
        "consent_grant_id": cg,
        "expires_at": result.get("expires_at"),
        "scopes": result.get("scopes"),
        "phone": phone,
        "cached": True,
        "message": (
            "跑腿绑定成功，凭证已写入共享缓存（~/.clawdot/errand-credentials.json，与外卖凭证分格互不影响）。"
            "单用户后续调用无需 --phone；多用户场景请始终带 --phone 指定用户。"
        ),
    })


# ── 业务命令（用户态）────────────────────────────────────────────────────────

def cmd_list_addresses(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    result = gw.list_addresses(cg)
    output(result)


def cmd_search_addresses(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    has_coords = args.lat is not None and args.lng is not None
    if not args.keyword and not has_coords:
        die("搜地址要么给 --keyword（地名，如 '西湖文化广场'，同名多地时带 --city），"
            "要么给 --lat --lng（按用户当前位置反查地点名）")
    result = gw.search_addresses(cg, keyword=args.keyword, city=args.city,
                                 lat=args.lat if has_coords else None,
                                 lng=args.lng if has_coords else None)
    output(result)


def cmd_update_address(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    if not args.address_id:
        die("缺 --address-id（要改哪条，来自 list_addresses 的 plat_ 开头那个）")
    # 改位置必须三件套齐全：只改文本不改坐标会让骑手按旧坐标去旧地方
    loc = [args.address, args.lat, args.lng]
    if any(v is not None for v in loc) and any(v is None for v in loc):
        die("改地址位置要 --address --lat --lng 三个一起传"
            "（只改文本不改坐标，骑手会按旧坐标去旧地方）；先 search_addresses 拿新坐标")
    if all(v is None for v in (args.contact_name, args.contact_phone, args.address,
                               args.lat, args.lng, args.detail, args.tag)):
        die("没有要改的字段：至少给一个 --contact-name/--contact-phone/--detail/--tag "
            "或 --address+--lat+--lng")
    output(gw.update_address(
        cg, address_id=args.address_id, contact_name=args.contact_name,
        contact_phone=args.contact_phone, address=args.address,
        lat=args.lat, lng=args.lng, detail=args.detail, tag=args.tag,
    ))


def cmd_delete_address(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    if not args.address_id:
        die("缺 --address-id（要删哪条，来自 list_addresses 的 plat_ 开头那个）")
    output(gw.delete_address(cg, args.address_id))


def cmd_list_goods_categories(args, gw: MCPClient, config: Config, cg: str,
                              phone: str | None) -> None:
    output(gw.list_goods_categories(cg))


def cmd_list_schedule_slots(args, gw: MCPClient, config: Config, cg: str,
                            phone: str | None) -> None:
    output(gw.list_schedule_slots(cg))


def cmd_auth_status(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    # 凭证无效不报错：看 bound 字段。bound=false 时 next_action=request_user_bind
    output(gw.get_auth_status(cg))


def cmd_revoke_user_bind(args, gw: MCPClient, config: Config, cg: str,
                         phone: str | None) -> None:
    """解绑。**破坏性且不可逆**（--reset-history 会清退地址簿与历史单），故这里不复用
    resolve_consent_grant 的宽松兜底：那条路径在「带 --phone 但缓存没命中」时会回落到
    CONSENT_GRANT_INVALID 环境变量的 cg——读接口顶多读错人，解绑却会把**另一个用户**
    解掉、甚至不可逆清掉他的地址簿，且现场看不出异常。所以此处要求 cg 与目标身份精确对应。
    """
    creds = CredStore(config.api_key, config.clawdot_home)
    bound = creds.all()
    target = normalize_phone(phone) if phone else None

    if target:
        cached = creds.get(target)
        if not cached:
            die(f"手机号 {mask_phone(target)} 在本地没有已绑定记录，不解绑。\n"
                "（避免误解绑别人：解绑只认本地缓存里这个号自己的凭证，不拿环境变量的 cg 顶替。）\n"
                "先确认手机号对不对；确实要解绑就用绑定时那台机器/那份缓存。")
        if cached != cg:
            die(f"手机号 {mask_phone(target)} 的凭证与本次解析到的不一致，不解绑。\n"
                "（多半是 CONSENT_GRANT_ID 环境变量顶替了缓存——解绑不接受这种兜底。）")
        cg = cached
    else:
        if len(bound) > 1:
            die("本地有多个已绑用户，解绑必须用 --phone 指明是哪一个。\n"
                f"（已绑 {len(bound)} 个号；解绑不可逆，不做猜测。）")
        if bound and next(iter(bound.values())) != cg:
            die("本次解析到的凭证与本地唯一已绑用户的不一致，不解绑。\n"
                "（多半是 CONSENT_GRANT_ID 环境变量顶替了缓存——解绑不接受这种兜底。）")
        target = next(iter(bound), None)  # 缓存为空=纯 env 预注入模式，无本地凭证可清

    result = gw.revoke_bind(cg, reset_history=bool(args.reset_history))
    # 网关侧已失效，本地缓存同步作废——留着只会拿废 cg 一直撞 CONSENT_GRANT_INVALID。
    # 只 drop 实际解绑的那一条，绝不 drop_all（别人的凭证不受牵连）。
    if target:
        creds.drop(target)
    output(result)


def cmd_save_address(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    if not args.address or args.lat is None or args.lng is None:
        die("存址要 --address --lat --lng（先 search_addresses 拿候选，客户选中后再存）")
    result = gw.save_address(
        cg, contact_name=args.contact_name or "", address=args.address,
        lat=args.lat, lng=args.lng, detail=args.detail or "", tag=args.tag or "",
        contact_phone=args.contact_phone or "",
    )
    output(result)


def cmd_list_orders(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    # 返回 {orders, next_offset}——翻页把 next_offset 原样当下次的 --offset；null=没有更多
    result = gw.list_orders(
        cg, limit=int(args.limit or 5), offset=int(args.offset or 0),
        status=args.status, created_after=args.created_after,
        created_before=args.created_before,
    )
    output(result)


def cmd_quote(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    # 发件端可兜底下单人主号（寄件人默认本人）；收件端传 None——绝不拿下单人号当收件人，
    # 收件联系人只能来自显式入参或地址簿，第一次给别人寄就 CONTACT_REQUIRED 问一次。
    from_ep = _endpoint(args, "from", "发件人", phone)
    to_ep = _endpoint(args, "to", "收件人", None)
    goods = [{"name": args.goods_name or "物品", "qty": 1}]
    if args.goods_price is not None:
        goods[0]["price_fen"] = args.goods_price
    result = gw.quote(
        cg, from_address=from_ep, to_address=to_ep, goods=goods,
        total_weight_g=args.weight, scheduled_at=args.scheduled_at,
        person_direct=args.person_direct, insured=args.insured, remark=args.remark,
        goods_category_code=args.goods_category_code,
    )
    # 返回 {quote_id, quotes, expires_in_seconds}：把 quote_id 与 quotes 原样交给 agent，
    # agent 把运力报价给用户选，选定后带 quote_id + company_code 调 create。
    output(result)


def cmd_create(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    if not args.quote_id:
        die("缺 --quote-id（来自 quote 返回；单次核销令牌）")
    if args.company_code is None:
        die("缺 --company-code（从 quote 的 quotes[].company_code 里选定的运力）")
    result = gw.create(
        cg, quote_id=args.quote_id, company_code=int(args.company_code),
        callback_url=args.callback_url,
    )
    # {order_id, status:pending_payment, quote_fee, cashier_url}：把 cashier_url 原样发用户付款。
    output(result)


def cmd_get_order(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    if not args.order_id:
        die("缺 --order-id")
    output(gw.get_order(cg, args.order_id))


def cmd_get_rider(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    if not args.order_id:
        die("缺 --order-id")
    output(gw.get_rider(cg, args.order_id))


def cmd_pre_cancel(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    if not args.order_id:
        die("缺 --order-id")
    output(gw.pre_cancel(cg, args.order_id))


def cmd_cancel(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    if not args.order_id:
        die("缺 --order-id")
    output(gw.cancel(cg, args.order_id, reason=args.reason))


def cmd_add_tip(args, gw: MCPClient, config: Config, cg: str, phone: str | None) -> None:
    if not args.order_id:
        die("缺 --order-id")
    if not args.tip_fee or args.tip_fee <= 0:
        die("小费金额要大于 0（单位分），如 --tip-fee 200 = ¥2")
    # {order_id, tip_fee, status:pending_payment, cashier_url}：小费独立支付，把 cashier_url 发用户付。
    output(gw.add_tip(cg, args.order_id, int(args.tip_fee)))


# ── Main ────────────────────────────────────────────────────────────────────

COMMANDS = {
    "auth_status": cmd_auth_status,
    "revoke_user_bind": cmd_revoke_user_bind,
    "list_addresses": cmd_list_addresses,
    "search_addresses": cmd_search_addresses,
    "save_address": cmd_save_address,
    "update_address": cmd_update_address,
    "delete_address": cmd_delete_address,
    "list_goods_categories": cmd_list_goods_categories,
    "list_schedule_slots": cmd_list_schedule_slots,
    "list_orders": cmd_list_orders,
    "quote": cmd_quote,
    "create": cmd_create,
    "get_order": cmd_get_order,
    "get_rider": cmd_get_rider,
    "pre_cancel": cmd_pre_cancel,
    "cancel": cmd_cancel,
    "add_tip": cmd_add_tip,
}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--phone", default=None,
        help="（多用户模式）用户手机号；按 (API_KEY, 手机号) 读取共享缓存里的 consent_grant_id。"
             "不传则用 CONSENT_GRANT_ID 环境变量或缓存中唯一已绑用户。",
    )

    parser = argparse.ArgumentParser(
        prog="errand.py",
        description="ClawDot 跑腿 CLI（open-gateway errand MCP 面）",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("request_user_bind", parents=[common],
                       help="用户绑定第 1 步：发短信验证码（跑腿仅短信模式）")
    p.add_argument("--external-user-id", default=None,
                   help="客户侧用户唯一标识（可选），注入收银台联登/支付 open_id")

    p = sub.add_parser("verify_user_bind", parents=[common],
                       help="用户绑定第 2 步：核验短信验证码，成功后写共享缓存")
    p.add_argument("--bind-id", default=None, help="request_user_bind 返回的 bind_id")
    p.add_argument("--code", default=None, help="用户回复的 6 位短信验证码")

    p = sub.add_parser("auth_status", parents=[common],
                       help="查授权是否仍有效（凭证失效不报错，看返回的 bound 字段）")

    p = sub.add_parser("revoke_user_bind", parents=[common],
                       help="解绑（撤销用户授权）；同时作废本地缓存的 cg")
    p.add_argument("--reset-history", action="store_true",
                   help="连地址簿与历史订单一并清退，**不可逆**；换人用这台设备/这个号才传")

    p = sub.add_parser("list_addresses", parents=[common],
                       help="列该手机号名下跑腿地址簿（id 恒 plat_ 前缀；与外卖地址簿独立）")

    p = sub.add_parser("search_addresses", parents=[common],
                       help="搜地点 → candidates + saved_matches（已存过的那份可直接下单）")
    p.add_argument("--keyword", default=None, help="地名关键词，如 '西湖文化广场'")
    p.add_argument("--city", default=None, help="城市名，可选（同名多地时缩小范围）")
    p.add_argument("--lat", type=float, default=None,
                   help="用户当前位置纬度；只给坐标=按位置反查地点名（'从我现在的位置'）")
    p.add_argument("--lng", type=float, default=None, help="用户当前位置经度")

    p = sub.add_parser("save_address", parents=[common],
                       help="把选中的地址存进地址簿，下次直接复用")
    p.add_argument("--address", required=True, help="地址文本（search 选中的 name+address）")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lng", type=float, required=True)
    p.add_argument("--contact-name", default=None, help="联系人（可选）")
    p.add_argument("--contact-phone", default=None,
                   help="联系电话（可选）；存了之后拿这个地址下单不必再报手机号。"
                        "落库即密文，出参只回脱敏 138****5678")
    p.add_argument("--detail", default=None,
                   help="门牌/楼层等补充（下单自动拼进地址；查地址簿时原样返回）")
    p.add_argument("--tag", default=None, help="标签，如 家/公司")

    p = sub.add_parser("update_address", parents=[common],
                       help="改地址簿某条（只传要改的字段；改位置须 address+lat+lng 齐传）")
    p.add_argument("--address-id", required=True, help="要改哪条（plat_ 前缀）")
    p.add_argument("--contact-name", default=None, help="新联系人")
    p.add_argument("--contact-phone", default=None, help="新联系电话（落库即密文，只回脱敏）")
    p.add_argument("--address", default=None, help="新地址文本（须与 --lat --lng 一起传）")
    p.add_argument("--lat", type=float, default=None, help="新纬度（须与 --address --lng 一起传）")
    p.add_argument("--lng", type=float, default=None, help="新经度（须与 --address --lat 一起传）")
    p.add_argument("--detail", default=None, help="新门牌/楼层")
    p.add_argument("--tag", default=None, help="新标签；传空串 '' 可清空标签")

    p = sub.add_parser("delete_address", parents=[common],
                       help="删地址簿某条（不可撤销，先跟用户确认是哪一条）")
    p.add_argument("--address-id", required=True, help="要删哪条（plat_ 前缀）")

    p = sub.add_parser("list_goods_categories", parents=[common],
                       help="物品品类清单（15 项，前 7 项贵重）；选中 code 回填 quote")

    p = sub.add_parser("list_schedule_slots", parents=[common],
                       help="可预约送达档位（一档 15 分钟）；只能回填清单给的 value")

    p = sub.add_parser("list_orders", parents=[common],
                       help='历史单（"还是上次那样" 复用收发/物品）；返回 {orders, next_offset}')
    p.add_argument("--limit", type=int, default=5, help="条数，默认 5、上限 20")
    p.add_argument("--offset", type=int, default=0,
                   help="翻页起点：把上次返回的 next_offset 原样传回")
    p.add_argument("--status", default=None,
                   help="按状态筛，逗号分隔可多传，如 dispatching,delivering；传错值会报错")
    p.add_argument("--created-after", default=None,
                   help="只看该时间之后的单，如 2026-08-01 或 '2026-08-01 10:30:00'（北京时间）")
    p.add_argument("--created-before", default=None,
                   help="只看该时间之前的单；只写日期时含当天全天")

    p = sub.add_parser("quote", parents=[common],
                       help="询价：多运力报价（每端用地址簿 id 或搜到的坐标）")
    p.add_argument("--from-id", default=None, help="发件地址 id（来自 list_addresses/save_address）")
    p.add_argument("--to-id", default=None, help="收件地址 id")
    p.add_argument("--from-text", default=None, help="发件地址文本（search 选中的 name+address）")
    p.add_argument("--from-lat", type=float, default=None)
    p.add_argument("--from-lng", type=float, default=None)
    p.add_argument("--to-text", default=None, help="收件地址文本")
    p.add_argument("--to-lat", type=float, default=None)
    p.add_argument("--to-lng", type=float, default=None)
    p.add_argument("--from-name", default=None,
                   help="发件联系人（缺省：地址簿存的 > 占位名）")
    p.add_argument("--from-phone", default=None,
                   help="发件电话（缺省：地址簿存的 > 下单人本号，多用户模式带 --phone 才有本号兜底）")
    p.add_argument("--to-name", default=None,
                   help="收件联系人（缺省：地址簿存的；都没有则由网关 CONTACT_REQUIRED 提示问一次）")
    p.add_argument("--to-phone", default=None,
                   help="收件电话（缺省：地址簿存的；**绝不套下单人本号**，都没有则问一次）")
    p.add_argument("--goods-name", default=None, help="物品名，如 文件/奶茶")
    p.add_argument("--goods-price", type=int, default=None, help="货值（分）")
    p.add_argument("--weight", type=int, default=1000, help="总重量（克），默认 1000")
    p.add_argument("--goods-category-code", type=int, default=None,
                   help="物品品类码，取自 list_goods_categories。**理赔只看品类、不看物品名**，"
                        "不传会退回平台默认品类")
    p.add_argument("--remark", default=None,
                   help="给骑手的留言（最多 200 字），如 '放门口别敲门，家里有狗'")
    p.add_argument("--scheduled-at", type=int, default=None,
                   help="预约送达时间：**只能填 list_schedule_slots 给出的 value**，"
                        "自己算的时间戳会被拒；不传=即时单（别传 0）")
    p.add_argument("--person-direct", action="store_true", help="专人直送（不拼单，费用更高）")
    p.add_argument("--insured", action="store_true", help="保价（按货值口径，额外收费）")

    p = sub.add_parser("create", parents=[common],
                       help="下单：核销 quote_id + 选定运力，返回付款链接")
    p.add_argument("--quote-id", required=True, help="quote 返回的 quote_id（单次核销令牌）")
    p.add_argument("--company-code", type=int, required=True,
                   help="选定运力码（须在本次 quote 报价集内）")
    p.add_argument("--callback-url", default=None, help="状态回调地址（可选，一般不填）")

    p = sub.add_parser("get_order", parents=[common], help="查订单状态/时间线/骑手")
    p.add_argument("--order-id", required=True)

    p = sub.add_parser("get_rider", parents=[common], help="骑手实时位置（配送中才有）")
    p.add_argument("--order-id", required=True)

    p = sub.add_parser("pre_cancel", parents=[common], help="取消前查违约金/可退金额")
    p.add_argument("--order-id", required=True)

    p = sub.add_parser("cancel", parents=[common], help="取消订单（已付按 实付−违约金 退）")
    p.add_argument("--order-id", required=True)
    p.add_argument("--reason", default=None)

    p = sub.add_parser("add_tip", parents=[common], help="加小费催单（独立付款链接）")
    p.add_argument("--order-id", required=True)
    p.add_argument("--tip-fee", type=int, required=True, help="小费（分），如 200 = ¥2")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config()

    if not config.api_key:
        die(
            "还没配置跑腿服务的 API_KEY。\n"
            f"让用户打开 {config.setup_url} 登录/注册 ClawDot 拿到 API_KEY，原文发回来；"
            "收到后写入本 skill 根目录 .env，内容两行：\n"
            f"GATEWAY_MCP_URL={DEFAULT_MCP_URL}\n"
            "API_KEY=<用户发来的key>\n"
            "不要复述或展示 key。写好后接着问绑定信息。\n"
            "RECOVERY[API_KEY_MISSING]: ① 把注册链接发给用户等 key → ② 写入 .env → "
            "③ 问手机号走短信绑定（request_user_bind → verify_user_bind）"
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

    # ── 其他业务命令必须先解析 consent_grant_id ──
    cg = resolve_consent_grant(args.phone, creds, config)
    try:
        COMMANDS[args.command](args, gw, config, cg, args.phone)
    except GatewayError as e:
        die(friendly_error(e, {"phone": args.phone or "<11位手机号>"}))
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
