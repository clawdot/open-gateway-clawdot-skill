#!/usr/bin/env python3
"""MG6 live end-to-end smoke: CLI → MCP → real gateway, through create_order.

⚠️ Creates a REAL pending_payment order (no charge; auto-expires unpaid).
Opt-in only — run via: RUN_MG6=1 API_KEY=… CONSENT_GRANT_ID=… bash verify.sh
(or invoke directly with those env vars). Optional env: GATEWAY_MCP_URL,
SMOKE_LAT / SMOKE_LNG / SMOKE_KEYWORD.

Chain: get_user_auth_status → search_addresses → search_shops →
get_shop_menu → preview_order (BELOW_MIN_ORDER 加量重试 / required_groups
自动补候选) → create_order → get_order_status. Output is sanitized: id
prefixes only, payment link redacted to its host part."""
import json
import os
import subprocess
import sys
from pathlib import Path

CLI = str(Path(__file__).resolve().parent.parent
          / "skills" / "takeout" / "scripts" / "clawdot.py")

if not os.environ.get("API_KEY") or not os.environ.get("CONSENT_GRANT_ID"):
    print("MG6: missing API_KEY / CONSENT_GRANT_ID env", file=sys.stderr)
    sys.exit(2)

LAT = os.environ.get("SMOKE_LAT", "31.23")
LNG = os.environ.get("SMOKE_LNG", "121.47")
KEYWORD = os.environ.get("SMOKE_KEYWORD", "咖啡")


def run(*args):
    p = subprocess.run(["python3", CLI, *args], capture_output=True,
                       text=True, timeout=60)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def pid(v, n=12):
    return (v[:n] + "…") if isinstance(v, str) and len(v) > n else v


steps = []


def fail(msg):
    for name, detail in steps:
        print(f"  {name}: {detail}")
    print(f"MG6 FAIL: {msg}")
    sys.exit(1)


rc, out, err = run("get_user_auth_status")
if rc != 0:
    fail(f"auth status: {err[:200]}")
steps.append(("get_user_auth_status", f"bound={json.loads(out).get('bound')}"))

rc, out, err = run("search_addresses", "--lat", LAT, "--lng", LNG)
if rc != 0:
    fail(f"addresses: {err[:200]}")
saved = json.loads(out).get("saved") or []
if not saved:
    fail("no saved address for this consent")
addr_id = saved[0]["address_id"]
steps.append(("search_addresses", f"saved={len(saved)} use={pid(addr_id)}"))

rc, out, err = run("search_shops", "--keyword", KEYWORD, "--lat", LAT, "--lng", LNG)
if rc != 0:
    fail(f"search_shops: {err[:200]}")
shops = [s for s in json.loads(out)["shops"] if s.get("available")]
steps.append(("search_shops", f"available={len(shops)}"))

order = None
for shop in shops[:3]:
    sid = shop["shop_id"]
    rc, out, err = run("get_shop_menu", "--shop-id", sid)
    if rc != 0:
        steps.append((f"menu {pid(sid)}", f"skip: {err.splitlines()[0][:80]}"))
        continue
    menu = json.loads(out)
    pool = [it for cat in menu.get("categories", [])
            for it in cat.get("top_items", []) if it.get("available")]
    base = []
    for g in menu.get("required_groups") or []:
        cand = next((c for c in g.get("candidates", []) if c.get("available")), None)
        if cand:
            base.append({"item_id": cand["item_id"], "quantity": g.get("min_select", 1)})
    steps.append((f"get_shop_menu {pid(sid)}", f"cats={len(menu.get('categories', []))}"))

    for it in pool[:4]:
        done = False
        for qty in (1, 2, 3):
            items = json.dumps(base + [{"item_id": it["item_id"], "quantity": qty}],
                               ensure_ascii=False)
            rc, out, err = run("preview_order", "--shop-id", sid,
                               "--address-id", addr_id, "--items", items)
            if rc == 0:
                pv = json.loads(out)
                steps.append((f"preview_order {pid(sid)}",
                              f"{it.get('name')}×{qty} prv={pid(pv.get('preview_id'))}"))
                rc2, out2, err2 = run("create_order", "--preview-id", pv["preview_id"],
                                      "--confirmation-token", pv["confirmation_token"])
                if rc2 != 0:
                    fail(f"create_order: {err2.splitlines()[0][:150]}")
                od = json.loads(out2)
                link = od.get("payment_link") or ""
                order = od
                steps.append(("create_order",
                              f"order={pid(od.get('order_id'), 16)} status={od.get('status')} "
                              f"payment_link={'yes' if link else 'NO'}"))
                done = True
                break
            if "BELOW_MIN_ORDER" in err:
                continue
            steps.append((f"preview {it.get('name')}", err.splitlines()[0][:90]))
            break
        if done:
            break
    if order:
        break

if not order:
    fail("could not complete order chain in 3 shops")

rc, out, err = run("get_order_status", "--order-id", order["order_id"])
status = json.loads(out).get("status") if rc == 0 else f"ERR {err[:80]}"
steps.append(("get_order_status", f"status={status}"))

for name, detail in steps:
    print(f"  {name}: {detail}")
print("MG6: END-TO-END GREEN (real pending_payment order created, unpaid)")
