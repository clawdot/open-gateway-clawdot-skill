#!/usr/bin/env python3
"""errand.py 契约 + 流程 + 能力分格测试（无 pytest 依赖，standalone）。

跑：python3 tests/test_errand_cli.py   （exit 0 全绿 / 1 有失败）

覆盖：
  · 能力分格（治"串"核心断言）：同 API_KEY 同手机号，errand 与 takeout 的 cg 各落各的
    文件、互不覆盖、重载后仍隔离。
  · CredStore 往返 / 过期。
  · MCP 工具名映射：每个 client 方法调对应的 errand_* 工具、consent 作参数、绑定类不带。
  · resolve_consent_grant 优先级。
  · argparse：19 子命令都能解析（与文档 v3.0 的 19 个工具 1:1）。
  · 错误 playbook 路由（WRONG_CAP → CONSENT_INVALID、报价过期 → QUOTE_EXPIRED 等）。
"""

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 普通 import（注册进 sys.modules，Python 3.9 dataclass 类型解析才正常）。
sys.path.insert(0, str(ROOT / "skills" / "errand" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "takeout" / "scripts"))
import errand  # noqa: E402
import clawdot  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}")
        FAILS.append(label)


# ── 能力分格：治"串"核心断言 ────────────────────────────────────────────────

def test_capability_partition() -> None:
    print("\n=== 能力分格（errand cg ⟂ takeout cg，同 key 同手机号不互踢）===")
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        api_key = "clw_same_key_for_both_caps"
        phone = "13800000000"

        e_store = errand.CredStore(api_key, home)
        t_store = clawdot.CredStore(api_key, home)

        # 文件级分格：两条能力落不同文件
        check(e_store.path.name == "errand-credentials.json", "errand 写 errand-credentials.json")
        check(t_store.path.name == "credentials.json", "takeout 写 credentials.json")
        check(e_store.path != t_store.path, "两条能力凭证文件不同")

        # 同一手机号、同一 api_key，各写各的 cg
        e_store.set(phone, "cg_errand_AAAA", None)
        t_store.set(phone, "cg_delivery_BBBB", None)

        check(e_store.get(phone) == "cg_errand_AAAA", "errand.get 拿到 errand cg")
        check(t_store.get(phone) == "cg_delivery_BBBB", "takeout.get 拿到 delivery cg")
        check(e_store.get(phone) != t_store.get(phone), "两条 cg 互不覆盖")

        # 重载（从盘重读）后仍隔离——证明是落盘隔离而非内存巧合
        e2 = errand.CredStore(api_key, home)
        t2 = clawdot.CredStore(api_key, home)
        check(e2.get(phone) == "cg_errand_AAAA", "重载后 errand cg 仍在")
        check(t2.get(phone) == "cg_delivery_BBBB", "重载后 delivery cg 仍在")

        # 各文件只含自己那条 cg，绝无对方的
        e_raw = e_store.path.read_text()
        t_raw = t_store.path.read_text()
        check("cg_delivery_BBBB" not in e_raw, "errand 文件里没有 delivery cg")
        check("cg_errand_AAAA" not in t_raw, "takeout 文件里没有 errand cg")


def test_credstore_roundtrip() -> None:
    print("\n=== CredStore 往返 / 过期 ===")
    with tempfile.TemporaryDirectory() as d:
        store = errand.CredStore("clw_k", Path(d))
        store.set("13811112222", "cg_x", "2999-01-01T00:00:00+08:00")
        check(store.get("13811112222") == "cg_x", "未过期 cg 可取")
        check(store.all() == {"13811112222": "cg_x"}, "all() 列出未过期用户")
        store.set("13833334444", "cg_expired", "2000-01-01T00:00:00+08:00")
        check(store.get("13833334444") is None, "过期 cg 视为无")
        check("13833334444" not in store.all(), "all() 剔除过期用户")


# ── MCP 工具名映射 ───────────────────────────────────────────────────────────

def test_tool_name_mapping() -> None:
    print("\n=== MCP 工具名映射（errand_* + consent 作参数）===")
    cfg = errand.Config(mcp_url="https://paotui.hicaspian.com/mcp/v1", api_key="clw_k",
                        consent_grant_id="", setup_url="", timeout_ms=30000,
                        clawdot_home=Path("/tmp"))
    gw = errand.MCPClient(cfg)
    calls: list[tuple[str, dict]] = []
    gw._call = lambda tool, args: (calls.append((tool, args)) or {"ok": True})  # type: ignore

    gw.request_bind("13800000000")
    gw.verify_bind("bind_1", "048231")
    gw.quote("cg_1", from_address={"address_id": "a"}, to_address={"address_id": "b"},
             goods=[{"name": "文件", "qty": 1}], person_direct=True, insured=True)
    gw.create("cg_1", quote_id="q_1", company_code=8889)
    gw.add_tip("cg_1", "err_1", 200)
    gw.search_addresses("cg_1", keyword="西湖", city=None)

    names = [c[0] for c in calls]
    expected = ["errand_request_user_bind", "errand_verify_user_bind", "errand_quote",
                "errand_create", "errand_add_tip", "errand_search_addresses"]
    check(names == expected, f"工具名逐一正确：{names}")

    # 绑定类不带 consent；业务类带
    bind_args = calls[0][1]
    check("consent_grant_id" not in bind_args, "request_bind 不带 consent_grant_id")
    check(calls[2][1].get("consent_grant_id") == "cg_1", "quote 带 consent_grant_id")
    # 增值项透传
    check(calls[2][1].get("person_direct") is True and calls[2][1].get("insured") is True,
          "quote 透传 person_direct / insured")
    check(calls[3][1].get("quote_id") == "q_1" and calls[3][1].get("company_code") == 8889,
          "create 透传 quote_id + company_code")


def test_v3_tool_mapping() -> None:
    """v3.0 新增 6 工具 + 既有工具的新参数，逐一钉死出站工具名与关键字段。

    skill 侧写错工具名/字段名不会在本地报错——要到真调网关才炸，所以这里锁死。
    """
    print("\n=== v3.0 新工具与新参数 ===")
    cfg = errand.Config(mcp_url="https://paotui.hicaspian.com/mcp/v1", api_key="clw_k",
                        consent_grant_id="", setup_url="", timeout_ms=30000,
                        clawdot_home=Path("/tmp"))
    gw = errand.MCPClient(cfg)
    calls: list[tuple[str, dict]] = []
    gw._call = lambda tool, args: (calls.append((tool, args)) or {"ok": True})  # type: ignore

    gw.get_auth_status("cg_1")
    gw.revoke_bind("cg_1", reset_history=True)
    gw.update_address("cg_1", address_id="plat_12", detail="3栋502")
    gw.delete_address("cg_1", "plat_12")
    gw.list_goods_categories("cg_1")
    gw.list_schedule_slots("cg_1")

    names = [c[0] for c in calls]
    expected = ["errand_get_auth_status", "errand_revoke_user_bind", "errand_update_address",
                "errand_delete_address", "errand_list_goods_categories",
                "errand_list_schedule_slots"]
    check(names == expected, f"6 个新工具名逐一正确：{names}")
    check(calls[1][1].get("reset_history") is True, "revoke 透传 reset_history")
    check(calls[2][1].get("address_id") == "plat_12" and calls[2][1].get("detail") == "3栋502",
          "update_address 透传 address_id + detail")

    # 注：tag="" 的保留由 test_optional_args_omitted_when_unset 端到端验（过滤层之下），
    # 这里不再重复断言——在 _call 上方断言拦不住「过滤逻辑把空串一起丢掉」这种改动。

    # quote 的 goods_category_code（理赔口径）与 list_orders 的翻页/筛选
    calls.clear()
    gw.quote("cg_1", from_address={"address_id": "a"}, to_address={"address_id": "b"},
             goods=[{"name": "手机", "qty": 1}], goods_category_code=1672215005)
    check(calls[0][1].get("goods_category_code") == 1672215005,
          "quote 透传 goods_category_code（理赔只看品类，丢了会退回默认品类）")

    calls.clear()
    gw.list_orders("cg_1", limit=5, offset=5, status="delivering", created_after="2026-08-01")
    a = calls[0][1]
    check(a.get("offset") == 5 and a.get("status") == "delivering"
          and a.get("created_after") == "2026-08-01",
          "list_orders 透传 offset/status/created_after")

    # 按位置搜地址（"从我现在的位置"）
    calls.clear()
    gw.search_addresses("cg_1", lat=30.29, lng=120.096)
    check(calls[0][1].get("lat") == 30.29 and calls[0][1].get("lng") == 120.096,
          "search_addresses 支持只给坐标（按位置反查地点名）")


# 每个工具「传满所有参数时」应当出站的 key 集合——契约快照，抄自
# 《跑腿MCP接口说明文档 v3.0》§6-§9 各工具的 Arguments 表。
# 逐个方法传满参数、断言出站 key 集合**完全相等**：少一个=参数被丢（如 remark 静默消失），
# 多一个/改名=网关收不到（如 scheduled_at 写成 schedule_at 会被忽略→预约单静默降级成即时单）。
# 这比逐条 check 个别字段强：新增参数忘了透传、既有参数被改名，都会在这里变红。
TOOL_ARG_CONTRACT: dict[str, set[str]] = {
    "errand_request_user_bind": {"phone", "external_user_id"},
    "errand_verify_user_bind": {"bind_id", "code"},
    "errand_get_auth_status": {"consent_grant_id"},
    "errand_revoke_user_bind": {"consent_grant_id", "reset_history"},
    "errand_list_addresses": {"consent_grant_id"},
    "errand_search_addresses": {"consent_grant_id", "keyword", "city", "lat", "lng"},
    "errand_save_address": {"consent_grant_id", "address", "lat", "lng",
                            "contact_name", "contact_phone", "detail", "tag"},
    "errand_update_address": {"consent_grant_id", "address_id", "contact_name",
                              "contact_phone", "address", "lat", "lng", "detail", "tag"},
    "errand_delete_address": {"consent_grant_id", "address_id"},
    # 文档 §8.1/§8.2 未列 Arguments（清单类）；我方统一带 cg，与其余工具口径一致，实测可用
    "errand_list_goods_categories": {"consent_grant_id"},
    "errand_list_schedule_slots": {"consent_grant_id"},
    "errand_list_orders": {"consent_grant_id", "limit", "offset", "status",
                           "created_after", "created_before"},
    "errand_quote": {"consent_grant_id", "from_address", "to_address", "goods",
                     "total_weight_g", "scheduled_at", "person_direct", "insured",
                     "remark", "goods_category_code"},
    "errand_create": {"consent_grant_id", "quote_id", "company_code", "callback_url"},
    "errand_get_order": {"consent_grant_id", "order_id"},
    "errand_get_rider": {"consent_grant_id", "order_id"},
    "errand_pre_cancel": {"consent_grant_id", "order_id"},
    "errand_cancel": {"consent_grant_id", "order_id", "reason"},
    "errand_add_tip": {"consent_grant_id", "order_id", "tip_fee"},
}


def test_outbound_arg_contract() -> None:
    """19 个工具「传满参数」时的出站 key 集合，必须与文档 Arguments 表逐字相等。"""
    print("\n=== 出站参数契约（19 工具 × 全参数）===")
    cfg = errand.Config(mcp_url="https://paotui.hicaspian.com/mcp/v1", api_key="clw_k",
                        consent_grant_id="", setup_url="", timeout_ms=30000,
                        clawdot_home=Path("/tmp"))
    gw = errand.MCPClient(cfg)
    seen: dict[str, set] = {}
    gw._call = lambda tool, args: (seen.__setitem__(tool, set(args)) or {"ok": True})  # type: ignore

    ep = {"address_id": "plat_1", "contact_name": "张三", "contact_phone": "13800000000"}
    gw.request_bind("13800000000", external_user_id="u1")
    gw.verify_bind("b1", "048231")
    gw.get_auth_status("cg_1")
    gw.revoke_bind("cg_1", reset_history=True)
    gw.list_addresses("cg_1")
    gw.search_addresses("cg_1", keyword="西湖", city="杭州", lat=30.2, lng=120.1)
    gw.save_address("cg_1", contact_name="张三", address="A", lat=1.0, lng=2.0,
                    detail="101", tag="家", contact_phone="13800000000")
    gw.update_address("cg_1", address_id="plat_1", contact_name="李四",
                      contact_phone="13900000000", address="B", lat=3.0, lng=4.0,
                      detail="202", tag="公司")
    gw.delete_address("cg_1", "plat_1")
    gw.list_goods_categories("cg_1")
    gw.list_schedule_slots("cg_1")
    gw.list_orders("cg_1", limit=5, offset=5, status="delivering",
                   created_after="2026-08-01", created_before="2026-08-31")
    gw.quote("cg_1", from_address=ep, to_address=ep, goods=[{"name": "文件", "qty": 1}],
             total_weight_g=1000, scheduled_at=1789886700000, person_direct=True,
             insured=True, remark="放门口", goods_category_code=8889)
    gw.create("cg_1", quote_id="q1", company_code=1, callback_url="https://x.example/cb")
    gw.get_order("cg_1", "err_1")
    gw.get_rider("cg_1", "err_1")
    gw.pre_cancel("cg_1", "err_1")
    gw.cancel("cg_1", "err_1", reason="不寄了")
    gw.add_tip("cg_1", "err_1", 200)

    check(set(seen) == set(TOOL_ARG_CONTRACT),
          f"19 个工具都被调到（差集 {set(TOOL_ARG_CONTRACT) ^ set(seen) or '无'}）")
    bad = []
    for tool, want in TOOL_ARG_CONTRACT.items():
        got = seen.get(tool, set())
        if got != want:
            bad.append(f"{tool}: 多{sorted(got - want) or '-'} 少{sorted(want - got) or '-'}")
    check(not bad, "每个工具的出站参数集合与文档一致" + (f"（偏差：{bad}）" if bad else ""))


def test_optional_args_omitted_when_unset() -> None:
    """不传可选参数时，**真实出站 payload** 里不能出现这些 key（旧调用方式零回归）。

    ⚠️ 必须走 _run_main（mock urlopen）而不是 mock `_call`：None 过滤就发生在 `_call`
    **内部**，在它上方断言等于测了个寂寞——过滤逻辑被改坏也照样绿。首版就踩了这个，
    改用端到端路径后四条断言立刻由假绿转真红。
    """
    print("\n=== 可选参数缺省不污染出站（端到端，过滤后）===")
    env = {"API_KEY": "clw_test", "CONSENT_GRANT_ID": "cg_test"}
    ok = {"jsonrpc": "2.0", "id": 1,
          "result": {"content": [{"type": "text", "text": json.dumps({"ok": True})}]}}

    def args_of(argv):
        _out, req, _c = _run_main(argv, ok, env)
        return set(json.loads(req.data.decode())["params"]["arguments"]) if req else set()

    q = args_of(["quote", "--from-id", "a", "--to-id", "b", "--goods-name", "文件"])
    check(not ({"scheduled_at", "goods_category_code", "remark"} & q),
          f"旧式 quote 不带 v3 新参数（实际 {sorted(q)}）")

    lo = args_of(["list_orders"])
    check(lo == {"consent_grant_id", "limit"},
          f"无参 list_orders 只发 consent+limit（offset=0 被吞，实际 {sorted(lo)}）")

    rv = args_of(["revoke_user_bind"])
    check("reset_history" not in rv,
          f"不传 --reset-history 时该 key 完全不出现（不可逆动作默认安全，实际 {sorted(rv)}）")

    sa = args_of(["search_addresses", "--keyword", "西湖"])
    check(not ({"lat", "lng"} & sa), f"只给 keyword 时不发半截坐标（实际 {sorted(sa)}）")

    # tag="" 是「清空标签」的合法值——必须活过 _call 的过滤真正发出去。
    # （这条原本也断言在过滤层之上，拦不住「把空串一起丢掉」这种改动）
    _out, req, _c = _run_main(
        ["update_address", "--address-id", "plat_1", "--tag", ""], ok, env)
    ua = json.loads(req.data.decode())["params"]["arguments"] if req else {}
    check(ua.get("tag") == "", f"tag='' 端到端仍发出（清空标签，非丢弃）；实际 {ua!r}")


def test_revoke_never_unbinds_wrong_user() -> None:
    """解绑是**不可逆**动作（--reset-history 会清退地址簿与历史单），必须拒绝一切身份歧义。

    回归锁：resolve_consent_grant 有两条 env 兜底（不带 --phone 时 env 优先、带 --phone 但
    缓存 miss 时也回落 env）。读接口顶多读错人，解绑却会把**另一个用户**解掉且现场无异常。
    """
    print("\n=== 解绑安全：绝不解绑错的人 ===")
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        api_key = "clw_k"
        store = errand.CredStore(api_key, home)
        store.set("13800000001", "cg_userA", None)
        store.set("13800000002", "cg_userB", None)

        cfg = errand.Config(mcp_url="u", api_key=api_key, consent_grant_id="cg_preinjected",
                            setup_url="", timeout_ms=30000, clawdot_home=home)
        gw = errand.MCPClient(cfg)
        sent: list = []
        gw._call = lambda tool, args: (sent.append((tool, args)) or {"revoked": True})  # type: ignore

        class A:  # argparse 替身
            reset_history = True

        # ① 带 --phone 但该号从没绑过 → 必须拒绝（旧逻辑会去解绑 env 的 cg_preinjected）
        rc = _exit_code_of(lambda: errand.cmd_revoke_user_bind(
            A(), gw, cfg, "cg_preinjected", "13900000009"))
        check(rc == 1 and not sent, "未绑过的号 → 拒绝解绑，且**不发任何出站请求**")

        # ② 不带 --phone 但缓存里有 2 个用户 → 必须拒绝（不做猜测）
        sent.clear()
        rc = _exit_code_of(lambda: errand.cmd_revoke_user_bind(A(), gw, cfg, "cg_preinjected", None))
        check(rc == 1 and not sent, "多用户且身份不明 → 拒绝解绑，不发请求")

        # ③ 两个用户的本地凭证都必须原封不动
        s2 = errand.CredStore(api_key, home)
        check(s2.get("13800000001") == "cg_userA" and s2.get("13800000002") == "cg_userB",
              "被拒的解绑不得动任何人的本地凭证")

        # ④ 正常路径：带 --phone 且缓存命中 → 解绑该号，且**只** drop 这一条
        sent.clear()
        errand.cmd_revoke_user_bind(A(), gw, cfg, "cg_userA", "13800000001")
        check(sent and sent[0][1].get("consent_grant_id") == "cg_userA",
              "解绑发出的是该号自己的 cg（不是 env 顶替的）")
        s3 = errand.CredStore(api_key, home)
        check(s3.get("13800000001") is None, "解绑后该号本地凭证被作废")
        check(s3.get("13800000002") == "cg_userB", "**另一个用户的凭证不受牵连**")


def _exit_code_of(fn) -> int:
    """跑一个会 SystemExit 的调用，回退出码（0=没退出）。"""
    import contextlib
    import io
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            fn()
    except SystemExit as e:
        return int(e.code or 0)
    return 0


# ── resolve_consent_grant 优先级 ────────────────────────────────────────────

def test_resolve_consent() -> None:
    print("\n=== resolve_consent_grant 优先级 ===")
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        store = errand.CredStore("clw_k", home)
        store.set("13800000000", "cg_cached", None)
        cfg_env = errand.Config(mcp_url="", api_key="clw_k", consent_grant_id="cg_env",
                                setup_url="", timeout_ms=30000, clawdot_home=home)
        cfg_noenv = errand.Config(mcp_url="", api_key="clw_k", consent_grant_id="",
                                  setup_url="", timeout_ms=30000, clawdot_home=home)
        # 带 phone → 缓存优先
        check(errand.resolve_consent_grant("13800000000", store, cfg_noenv) == "cg_cached",
              "带 phone 命中缓存 cg")
        # 不带 phone + env 预注入 → env
        check(errand.resolve_consent_grant(None, store, cfg_env) == "cg_env",
              "不带 phone 用 CONSENT_GRANT_ID env")
        # 不带 phone + 无 env + 唯一已绑 → 该用户
        check(errand.resolve_consent_grant(None, store, cfg_noenv) == "cg_cached",
              "不带 phone 命中唯一已绑用户")


# ── argparse：19 子命令（= 文档 v3.0 工具总数）──────────────────────────────

def test_argparse() -> None:
    print("\n=== argparse：19 子命令都能解析 ===")
    parser = errand.build_parser()
    cases = [
        ["request_user_bind", "--phone", "13800000000"],
        ["verify_user_bind", "--phone", "13800000000", "--bind-id", "b", "--code", "048231"],
        ["list_addresses"],
        ["search_addresses", "--keyword", "西湖"],
        ["save_address", "--address", "x", "--lat", "30.2", "--lng", "120.1"],
        ["list_orders", "--limit", "5"],
        ["quote", "--from-id", "a", "--to-id", "b", "--goods-name", "文件",
         "--person-direct", "--insured", "--scheduled-at", "1784718580219"],
        ["create", "--quote-id", "q", "--company-code", "8889"],
        ["get_order", "--order-id", "err_1"],
        ["get_rider", "--order-id", "err_1"],
        ["pre_cancel", "--order-id", "err_1"],
        ["cancel", "--order-id", "err_1", "--reason", "x"],
        ["add_tip", "--order-id", "err_1", "--tip-fee", "200"],
        # v3.0 新增 6 个
        ["auth_status"],
        ["revoke_user_bind", "--reset-history"],
        ["update_address", "--address-id", "plat_1", "--detail", "3栋502"],
        ["delete_address", "--address-id", "plat_1"],
        ["list_goods_categories"],
        ["list_schedule_slots"],
        # v3.0 新参数
        ["search_addresses", "--lat", "30.29", "--lng", "120.096"],
        ["list_orders", "--offset", "5", "--status", "delivering"],
        ["quote", "--from-id", "a", "--to-id", "b", "--goods-name", "手机",
         "--goods-category-code", "1672215005"],
    ]
    ok = True
    for argv in cases:
        try:
            ns = parser.parse_args(argv)
            if ns.command != argv[0]:
                ok = False
        except SystemExit:
            ok = False
            print(f"      ✗ 解析失败：{argv}")
    check(ok, f"全部 {len(cases)} 条子命令用例解析通过（覆盖 19 个子命令）")
    # quote 的 store_true 生效
    ns = parser.parse_args(["quote", "--from-id", "a", "--to-id", "b", "--person-direct"])
    check(ns.person_direct is True and ns.insured is False, "quote store_true flag 生效")


# ── 收发端联系人/电话：地址簿兜底 vs 坐标形态兜底 ───────────────────────────

def test_endpoint_contact_fallback() -> None:
    """联系人/电话优先级：显式入参 > 地址簿存的 > 主号兜底（仅发件）/ 问一次（收件）。

    两条曾经的坑：
      1. 地址簿形态无条件把 contact_phone 填成下单人主号 → 网关"入参优先"永远命中入参、
         地址簿电话成死数据；
      2. 收件端坐标形态默认下单人主号 → 第一次给妈妈寄（走 POI）时骑手打给下单人。
    现在：地址簿形态留空交网关兜底；坐标形态**发件**兜底主号、**收件**留空强制问一次。
    """
    print("\n=== 收发端联系人/电话兜底 ===")
    parser = errand.build_parser()

    # 地址簿形态：没显式传 → 两个键都不出现（网关据此走地址簿兜底）
    ns = parser.parse_args(["quote", "--from-id", "1", "--to-id", "plat_2", "--goods-name", "文件"])
    to_ep = errand._endpoint(ns, "to", "收件人", None)
    check("contact_phone" not in to_ep, "地址簿形态：不塞下单人主号")
    check("contact_name" not in to_ep, "地址簿形态：不塞字面名'收件人'")
    check(to_ep["address_id"] == "plat_2", "地址簿形态：带上 address_id")

    # 地址簿形态 + 显式传 → 入参照常带上（入参优先）
    ns = parser.parse_args(["quote", "--from-id", "1", "--to-id", "2",
                            "--to-name", "李妈妈", "--to-phone", "13911112222",
                            "--goods-name", "文件"])
    to_ep = errand._endpoint(ns, "to", "收件人", None)
    check(to_ep["contact_phone"] == "13911112222" and to_ep["contact_name"] == "李妈妈",
          "地址簿形态：显式入参优先")

    # 坐标形态 — 发件端：兜底下单人主号（寄件人默认本人），不打扰用户
    ns = parser.parse_args(["quote", "--to-id", "9",
                            "--from-text", "我家", "--from-lat", "39.9", "--from-lng", "116.4",
                            "--goods-name", "文件"])
    from_ep = errand._endpoint(ns, "from", "发件人", "13800000000")
    check(from_ep["contact_phone"] == "13800000000" and from_ep["contact_name"] == "发件人",
          "坐标形态·发件：兜底主号+占位名")

    # 坐标形态 — 收件端：self_phone=None → 留空，网关据此 CONTACT_REQUIRED 问一次
    ns = parser.parse_args(["quote", "--from-id", "1",
                            "--to-text", "望京SOHO", "--to-lat", "39.9", "--to-lng", "116.4",
                            "--goods-name", "文件"])
    to_ep = errand._endpoint(ns, "to", "收件人", None)
    check(to_ep["contact_phone"] == "" and to_ep["contact_name"] == "",
          "坐标形态·收件：不拿下单人号，留空强制问一次")


# ── 错误 playbook 路由 ──────────────────────────────────────────────────────

def test_error_playbook() -> None:
    print("\n=== 错误 playbook 路由 ===")
    cases = [
        (errand.GatewayError(200, "CONSENT_GRANT_WRONG_CAP", "wrong cap"), "CONSENT_INVALID"),
        # v3.0 起该码专指 callback_url 非公网地址（不再是报价失效）
        (errand.GatewayError(200, "PUBLIC_REFERENCE_INVALID", "bad callback"), "CALLBACK_URL_INVALID"),
        (errand.GatewayError(200, "QUOTE_INVALID_OR_EXPIRED", "quote gone"), "QUOTE_EXPIRED"),
        (errand.GatewayError(200, "SCHEDULED_AT_NOT_ON_GRID", "off grid"), "SCHEDULED_AT_NOT_ON_GRID"),
        (errand.GatewayError(200, "GOODS_PROHIBITED", "禁运"), "GOODS_PROHIBITED"),
        (errand.GatewayError(200, "CAP_NOT_BOUND", "no errand cap"), "CAP_NOT_BOUND"),
        (errand.GatewayError(200, "ERRAND_CROSS_CITY", "跨城"), "ERRAND_CROSS_CITY"),
        (errand.GatewayError(401, "AUTH_INVALID", "bad key"), "API_KEY_INVALID"),
    ]
    for err, expect_code in cases:
        line = errand.friendly_error(err, {"phone": "13800000000"})
        check(f"RECOVERY[{expect_code}]" in line, f"{err.code} → RECOVERY[{expect_code}]")
    # 绑定类 next_action → USER_NOT_BOUND_NEEDS_SMS
    err = errand.GatewayError(200, "SOMETHING", "x", next_action="errand_request_user_bind")
    check("RECOVERY[USER_NOT_BOUND_NEEDS_SMS]" in errand.friendly_error(err),
          "next_action=request_user_bind → USER_NOT_BOUND_NEEDS_SMS")


# 网关 errand 面对外会抛的业务码 → **期望的** RECOVERY 码（真相源：open-gateway
# src/application/gateway/paotui/ 的 GatewayError + shared/exceptions 里 errand 用到的类）。
# 断言到**码级**（不只是"有 RECOVERY 标签"）——否则一个码被过宽正则误路由到别的码，
# 只查标签存在的测试照样绿（曾真的放过 QUOTE_FEE_INVALID→QUOTE_EXPIRED 的误路由）。
# 多数码自洽（RECOVERY 码=网关码）；少数上游/别名码归类到一个面向用户的 RECOVERY 码。
CODE_EXPECTED_RECOVERY = {
    # ── 自洽：RECOVERY 码 == 网关码 ──
    "ADDRESS_INCOMPLETE": "ADDRESS_INCOMPLETE",
    "ADDRESS_LOCATE_FAILED": "ADDRESS_LOCATE_FAILED",
    "ADDRESS_NOT_FOUND": "ADDRESS_NOT_FOUND",
    "ADDRESS_SEARCH_FAILED": "ADDRESS_SEARCH_FAILED",
    "ADDRESS_TEXT_REQUIRED": "ADDRESS_TEXT_REQUIRED",
    "CASHIER_UNAVAILABLE": "CASHIER_UNAVAILABLE",
    "COMPANY_NOT_IN_QUOTE": "COMPANY_NOT_IN_QUOTE",
    "CONTACT_REQUIRED": "CONTACT_REQUIRED",
    "COORDS_REQUIRED": "COORDS_REQUIRED",
    "ERRAND_CANCEL_NOT_ALLOWED": "ERRAND_CANCEL_NOT_ALLOWED",
    "ERRAND_CITY_NOT_OPEN": "ERRAND_CITY_NOT_OPEN",
    "ERRAND_CROSS_CITY": "ERRAND_CROSS_CITY",
    "ERRAND_FEE_CHANGED": "ERRAND_FEE_CHANGED",
    "ERRAND_LOCATE_UNAVAILABLE": "ERRAND_LOCATE_UNAVAILABLE",
    "ERRAND_NO_QUOTE": "ERRAND_NO_QUOTE",
    "ERRAND_NO_RIDER": "ERRAND_NO_RIDER",
    "ERRAND_ORDER_NOT_FOUND": "ERRAND_ORDER_NOT_FOUND",
    "ERRAND_PROVIDER_CONFIG_ERROR": "ERRAND_PROVIDER_CONFIG_ERROR",
    "ERRAND_SHOP_NOT_CONFIGURED": "ERRAND_SHOP_NOT_CONFIGURED",
    "ERRAND_TEMPORARILY_UNAVAILABLE": "ERRAND_TEMPORARILY_UNAVAILABLE",
    "ERRAND_TIP_INVALID": "ERRAND_TIP_INVALID",
    "ERRAND_TIP_NOT_ALLOWED": "ERRAND_TIP_NOT_ALLOWED",
    "ERRAND_UPSTREAM_ERROR": "ERRAND_UPSTREAM_ERROR",
    "GOODS_REQUIRED": "GOODS_REQUIRED",
    "KEYWORD_REQUIRED": "KEYWORD_REQUIRED",
    "PAYMENT_AMOUNT_MISMATCH": "PAYMENT_AMOUNT_MISMATCH",
    "QUOTE_FEE_INVALID": "QUOTE_FEE_INVALID",   # 独立码，别被 QUOTE_EXPIRED 吞（HIGH-1 回归锁）
    "SMS_CODE_INVALID": "SMS_CODE_INVALID",
    "SMS_COOLDOWN": "SMS_COOLDOWN",
    "CAP_NOT_BOUND": "CAP_NOT_BOUND",
    "BINDING_LIMIT_REACHED": "BINDING_LIMIT_REACHED",
    # ── v3.0 新增码（文档「跑腿MCP接口说明文档 v3.0」§12 常见错误码）──
    "SCHEDULED_AT_INVALID": "SCHEDULED_AT_INVALID",
    "SCHEDULED_AT_PAST": "SCHEDULED_AT_PAST",
    "SCHEDULED_AT_NOT_ON_GRID": "SCHEDULED_AT_NOT_ON_GRID",
    "SCHEDULED_AT_TOO_SOON": "SCHEDULED_AT_TOO_SOON",
    "SCHEDULED_AT_TOO_FAR": "SCHEDULED_AT_TOO_FAR",
    "GOODS_PROHIBITED": "GOODS_PROHIBITED",
    "GOODS_CATEGORY_INVALID": "GOODS_CATEGORY_INVALID",
    "REMARK_TOO_LONG": "REMARK_TOO_LONG",
    "ERRAND_STATUS_INVALID": "ERRAND_STATUS_INVALID",
    "ERRAND_TIME_RANGE_INVALID": "ERRAND_TIME_RANGE_INVALID",
    "ADDRESS_COORDS_PAIRED": "ADDRESS_COORDS_PAIRED",
    "ADDRESS_DUPLICATE": "ADDRESS_DUPLICATE",
    "ADDRESS_UPDATE_EMPTY": "ADDRESS_UPDATE_EMPTY",
    "ADDRESS_FIELD_TOO_LONG": "ADDRESS_FIELD_TOO_LONG",
    # ── 别名：网关真实码 → 面向用户的归类码 ──
    "QUOTE_INVALID_OR_EXPIRED": "QUOTE_EXPIRED",
    # PUBLIC_REFERENCE_INVALID 自 v3.0 起专指 callback_url 非公网地址，**不再**归到
    # QUOTE_EXPIRED——旧映射会给出"重新询价"这种驴唇不对马嘴的指引
    "PUBLIC_REFERENCE_INVALID": "CALLBACK_URL_INVALID",
    "CONSENT_GRANT_INVALID": "CONSENT_INVALID",
    "CONSENT_GRANT_REQUIRED": "CONSENT_INVALID",
    "CONSENT_GRANT_WRONG_CAP": "CONSENT_INVALID",
    "CONSENT_GRANT_EXPIRED": "CONSENT_EXPIRED",
    "AUTH_INVALID": "API_KEY_INVALID",
    "AUTH_REQUIRED": "API_KEY_INVALID",
}


def test_error_playbook_covers_every_gateway_code() -> None:
    """网关能抛的每个码都路由到**正确**的 RECOVERY 码（码级断言，非"有标签即过"）。"""
    print("\n=== 错误 playbook 全覆盖（码级） ===")
    import re
    bad = []
    for code, want in CODE_EXPECTED_RECOVERY.items():
        line = errand.friendly_error(errand.GatewayError(200, code, ""))
        m = re.search(r"RECOVERY\[([A-Z_]+)\]", line)
        got = m.group(1) if m else None
        if got != want:
            bad.append(f"{code}→{got}(应{want})")
    check(not bad, f"全部 {len(CODE_EXPECTED_RECOVERY)} 个网关码码级自洽"
                   + (f"（误路由：{bad}）" if bad else ""))
    # 未知码仍走兜底（不因过度宽泛的正则把陌生错误误判成已知情形）
    line = errand.friendly_error(errand.GatewayError(200, "SOME_BRAND_NEW_CODE", "陌生错误"))
    check(line.startswith("请求失败："), "未知码 → 兜底原文，不误认")


# ── 全链路 dispatch（main→cmd→_call→stdout，mock 网关，无真实网络）──────────

class _FakeResp:
    def __init__(self, payload: bytes):
        self._p = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._p


def _run_main(argv: list[str], canned: dict, env: dict):
    """真跑 errand.main()：mock urlopen 喂 canned MCP 响应，捕获出站 Request + stdout。"""
    import contextlib
    import io

    captured: dict = {}
    payload = json.dumps(canned).encode()

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _FakeResp(payload)

    old_urlopen, old_argv = errand.urlopen, sys.argv
    saved_env = {k: os.environ.get(k) for k in ("API_KEY", "CONSENT_GRANT_ID", "GATEWAY_MCP_URL")}
    try:
        errand.urlopen = fake_urlopen  # type: ignore
        sys.argv = ["errand.py", *argv]
        for k in ("API_KEY", "CONSENT_GRANT_ID", "GATEWAY_MCP_URL"):
            os.environ.pop(k, None)
        os.environ.update(env)
        buf = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(buf):
            try:
                errand.main()
            except SystemExit as e:
                code = e.code or 0
        return buf.getvalue().strip(), captured.get("req"), code
    finally:
        errand.urlopen, sys.argv = old_urlopen, old_argv
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_full_dispatch() -> None:
    print("\n=== 全链路 dispatch（main→cmd→_call→stdout，mock 网关）===")
    env = {"API_KEY": "clw_test", "CONSENT_GRANT_ID": "cg_test"}

    # quote：全程走 main → cmd_quote → gw.quote → _call → 解析 → output
    canned_quote = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text",
        "text": json.dumps({"quote_id": "q_1", "quotes": [{"company_code": 8889, "fee": 500}]})}]}}
    out, req, code = _run_main(
        ["quote", "--from-id", "a", "--to-id", "b", "--goods-name", "文件", "--person-direct"],
        canned_quote, env)
    check(code == 0, "quote 退出码 0")
    data = json.loads(out) if out else {}
    check(data.get("quote_id") == "q_1", "main→quote→stdout 出 quote_id")
    body = json.loads(req.data.decode()) if req else {}
    check(body.get("params", {}).get("name") == "errand_quote", "出站工具名=errand_quote")
    a = body.get("params", {}).get("arguments", {})
    check(a.get("consent_grant_id") == "cg_test", "出站带 consent_grant_id（来自 env）")
    check(a.get("person_direct") is True, "出站 person_direct=True")
    check(req.get_header("Authorization") == "Bearer clw_test", "出站 Bearer=API_KEY")
    check(req.full_url.endswith("/mcp/v1"), "出站打默认 paotui MCP 端点")

    # create：核销 quote_id + company_code
    canned_create = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text",
        "text": json.dumps({"order_id": "err_1", "status": "pending_payment", "cashier_url": "https://x"})}]}}
    out, req, code = _run_main(["create", "--quote-id", "q_1", "--company-code", "8889"], canned_create, env)
    body = json.loads(req.data.decode()) if req else {}
    check(json.loads(out).get("order_id") == "err_1" if out else False, "main→create→stdout 出 order_id")
    check(body.get("params", {}).get("name") == "errand_create", "create 出站工具名=errand_create")

    # 业务错误信封（isError=False 的 {"error":{code}}）→ friendly_error → stderr + exit 1
    canned_err = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text",
        "text": json.dumps({"error": {"code": "CONSENT_GRANT_WRONG_CAP", "message": "wrong cap"}})}]}}
    out, req, code = _run_main(["list_addresses"], canned_err, env)
    check(code == 1, "业务错误 → 退出码 1")

    # 缺 API_KEY → RECOVERY[API_KEY_MISSING]（不打网络）
    out, req, code = _run_main(["list_addresses"], {}, {})
    check(code == 1 and req is None, "缺 API_KEY 直接退出、不发请求")


def main() -> None:
    test_capability_partition()
    test_credstore_roundtrip()
    test_tool_name_mapping()
    test_v3_tool_mapping()
    test_outbound_arg_contract()
    test_optional_args_omitted_when_unset()
    test_revoke_never_unbinds_wrong_user()
    test_resolve_consent()
    test_argparse()
    test_endpoint_contact_fallback()
    test_error_playbook()
    test_error_playbook_covers_every_gateway_code()
    test_full_dispatch()
    print()
    if FAILS:
        print(f"❌ {len(FAILS)} 项失败：")
        for f in FAILS:
            print(f"   - {f}")
        sys.exit(1)
    print("✅ test_errand_cli.py 全绿")


if __name__ == "__main__":
    main()
