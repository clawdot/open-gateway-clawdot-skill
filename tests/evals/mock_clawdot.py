#!/usr/bin/env python3
"""Mock clawdot CLI for behavior evals — same argv surface, canned fixtures.

被 eval harness 当作真 CLI 喂给模型：模型发出的命令原样落到这里，返回固定 JSON
（stdout）或固定中文错误（stderr + exit 1）。**不打网关、不花钱、不产生副作用**，
且同一命令永远同一结果 → 行为断言可复现。

命中规则按子命令 + 关键参数匹配，未覆盖的组合返回一个合理的空结果而不是崩溃
（eval 关心的是模型的行为序列，不是 fixture 的完备性）。
"""
from __future__ import annotations

import json
import sys

OUT = lambda o: print(json.dumps(o, ensure_ascii=False))  # noqa: E731


def die(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


SAVED_ADDRESSES = {
    "saved": [
        {"address_id": "addr_home01", "address": "杭州市余杭区亲橙里3号楼", "tag": "家",
         "contact_name": "张三", "contact_phone": "138****0000", "lat": 30.28, "lng": 120.06},
        {"address_id": "addr_work01", "address": "杭州市余杭区阿里巴巴西溪园区A区", "tag": "公司",
         "contact_name": "张三", "contact_phone": "138****0000", "lat": 30.27, "lng": 120.02},
    ],
    "suggestions": [],
}

SEARCH_SUGGESTIONS = {
    "saved": [],
    "suggestions": [
        {"sug_ref": "sug_a1", "name": "中山路200号", "address": "南京市鼓楼区中山路200号",
         "requires_detail": True},
        {"sug_ref": "sug_a2", "name": "中山路200号-2号楼", "address": "南京市鼓楼区中山路200号2号楼",
         "requires_detail": True},
    ],
}

SHOPS = {
    "shops": [
        {"shop_id": "shop_mlt1", "cart_id": "cart_mlt1", "name": "郑恩强黏糊糊麻辣烫(仓前店)",
         "brand_name": "郑恩强", "delivery_fee": "免配送费", "delivery_time": "25分钟",
         "min_order_amount": 1900, "available": True, "tags": ["麻辣烫"],
         "highlights": ["浓汤麻辣烫", "自选菜"]},
        {"shop_id": "shop_mlt2", "cart_id": "cart_mlt2", "name": "杨国福麻辣烫(华夏之心店)",
         "brand_name": "杨国福", "delivery_fee": "免配送费", "delivery_time": "25分钟",
         "min_order_amount": 2000, "available": True, "tags": ["麻辣烫"],
         "highlights": ["骨汤锅底", "菜品全"]},
        {"shop_id": "shop_mlt3", "cart_id": "cart_mlt3", "name": "张亮麻辣烫(仓前店)",
         "brand_name": "张亮", "delivery_fee": "免配送费", "delivery_time": "19分钟",
         "min_order_amount": 2300, "available": True, "tags": ["麻辣烫"],
         "highlights": ["辣汤", "最快到"]},
    ],
    "count": 3,
}

TEA_SHOPS = {
    "shops": [
        {"shop_id": "shop_tea1", "cart_id": "cart_tea1", "name": "1点点(西溪天虹店)",
         "brand_name": "1点点", "delivery_fee": "¥4.4", "delivery_time": "26分钟",
         "min_order_amount": 1500, "available": True, "tags": ["奶茶"],
         "highlights": ["四季奶青", "QQ美莓奶茶"]},
        {"shop_id": "shop_tea2", "cart_id": "cart_tea2", "name": "茶百道(溪望路24h店)",
         "brand_name": "茶百道", "delivery_fee": "¥1.4", "delivery_time": "15分钟",
         "min_order_amount": 1500, "available": True, "tags": ["奶茶"],
         "highlights": ["杨枝甘露", "茉莉葡萄冰奶"]},
        {"shop_id": "shop_tea3", "cart_id": "cart_tea3", "name": "古茗(绿城未来PARK店)",
         "brand_name": "古茗", "delivery_fee": "¥1.8", "delivery_time": "26分钟",
         "min_order_amount": 1500, "available": True, "tags": ["奶茶"],
         "highlights": ["云岭茉莉", "四季青山鲜奶茶"]},
    ],
    "count": 3,
}

TEA_MENU = {
    "shop": {"shop_id": "shop_tea1", "name": "1点点(西溪天虹店)", "available": True},
    # 两个分类、四个商品：让"同店还有别的品类"成为可能，否则 context-else 断言测不出东西
    "categories": [{"name": "奶茶自由配", "items": ["item_sjnq", "item_qqmm"]},
                   {"name": "鲜果茶", "items": ["item_xgc", "item_bmg"]}],
    "items": [
        {"item_id": "item_sjnq", "name": "四季奶青", "price": 1900, "category_name": "奶茶自由配",
         "description": "茶味经典", "monthly_sales": 800,
         "sku_options": [
             {"sku_id": "sku_m", "name": "中杯", "price": 1900, "specs": ["中杯"]},
             {"sku_id": "sku_l", "name": "大杯", "price": 2200, "specs": ["大杯"]},
         ],
         "ingredient_options": [
             {"option_id": "opt_ice_less", "group_name": "温度", "name": "少冰",
              "selected_by_default": True, "price": 0, "available": True},
             {"option_id": "opt_ice_no", "group_name": "温度", "name": "去冰",
              "selected_by_default": False, "price": 0, "available": True},
             {"option_id": "opt_hot", "group_name": "温度", "name": "热",
              "selected_by_default": False, "price": 0, "available": True},
             {"option_id": "opt_sug_7", "group_name": "甜度", "name": "七分糖",
              "selected_by_default": True, "price": 0, "available": True},
             {"option_id": "opt_sug_full", "group_name": "甜度", "name": "全糖",
              "selected_by_default": False, "price": 0, "available": True},
             {"option_id": "opt_sug_5", "group_name": "甜度", "name": "五分糖",
              "selected_by_default": False, "price": 0, "available": True},
             {"option_id": "opt_sug_0", "group_name": "甜度", "name": "不加糖",
              "selected_by_default": False, "price": 0, "available": True},
         ]},
        {"item_id": "item_qqmm", "name": "QQ美莓奶茶", "price": 2000, "category_name": "奶茶自由配",
         "description": "招牌果味", "monthly_sales": 600, "sku_options": [], "ingredient_options": []},
        {"item_id": "item_xgc", "name": "西瓜果茶", "price": 1800, "category_name": "鲜果茶",
         "description": "夏日清爽", "monthly_sales": 300, "sku_options": [], "ingredient_options": []},
        {"item_id": "item_bmg", "name": "白桃芒果茶", "price": 2100, "category_name": "鲜果茶",
         "description": "果香浓", "monthly_sales": 450, "sku_options": [], "ingredient_options": []},
    ],
    "total_items": 4,
}

MLT_MENU = {
    "shop": {"shop_id": "shop_mlt1", "name": "郑恩强黏糊糊麻辣烫(仓前店)", "available": True},
    "categories": [{"name": "荤菜", "items": ["item_feiniu"]},
                   {"name": "素菜", "items": ["item_kuanfen", "item_youdoupi"]}],
    "required_groups": [
        {"name": "必选好汤", "min_select": 1, "candidates": [
            {"item_id": "item_gutang", "name": "草本骨汤", "price": 300},
            {"item_id": "item_fanqie", "name": "番茄汤", "price": 400},
            {"item_id": "item_gali", "name": "咖喱汤", "price": 400},
        ]},
    ],
    "items": [
        {"item_id": "item_feiniu", "name": "肥牛卷", "price": 1200, "category_name": "荤菜",
         "sku_options": [],
         "ingredient_options": [
             {"option_id": "opt_la_wei", "group_name": "辣度", "name": "微辣",
              "selected_by_default": True, "price": 0, "available": True},
             {"option_id": "opt_la_no", "group_name": "辣度", "name": "不辣",
              "selected_by_default": False, "price": 0, "available": True},
             {"option_id": "opt_la_zhong", "group_name": "辣度", "name": "中辣",
              "selected_by_default": False, "price": 0, "available": True},
             {"option_id": "opt_ma_wei", "group_name": "麻度", "name": "微麻",
              "selected_by_default": True, "price": 0, "available": True},
             {"option_id": "opt_ma_no", "group_name": "麻度", "name": "不麻",
              "selected_by_default": False, "price": 0, "available": True},
         ]},
        {"item_id": "item_kuanfen", "name": "宽粉", "price": 600, "category_name": "素菜",
         "sku_options": [
             {"sku_id": "sku_small", "name": "小份", "price": 600, "specs": ["小份"]},
             {"sku_id": "sku_big", "name": "大份", "price": 1000, "specs": ["大份"]},
         ], "ingredient_options": []},
        {"item_id": "item_youdoupi", "name": "油豆皮", "price": 500, "category_name": "素菜",
         "sku_options": [], "ingredient_options": []},
    ],
    "total_items": 3,
}

PREVIEW = {
    "preview_id": "prv_eval001", "confirmation_token": "cf_eval001",
    "shop_name": "1点点(西溪天虹店)", "items": [{"name": "四季奶青", "spec": "大杯/少冰/七分糖",
                                              "quantity": 1, "price": 2200}],
    "original_price": 2200, "packing_fee": 100, "delivery_fee": 440,
    "discount": 500, "total_price": 2240,
    "estimated_delivery_time": "12:40", "address": "杭州市余杭区亲橙里3号楼",
}

ORDER = {"order_id": "ord_eval001", "status": "pending_payment",
         "payment_link": "https://clawdot.example/pay/eval001",
         "estimated_delivery_time": "12:40"}

ORDER_STATUS = {"order_id": "ord_eval001", "status": "delivering",
                "status_text": "骑手已取餐，配送中", "estimated_delivery_time": "12:40",
                "rider": {"name": "李师傅", "phone": "138****0001"}}


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        die("缺少子命令")
    cmd = argv[0]
    flags: dict[str, str] = {}
    i = 1
    while i < len(argv):
        if argv[i].startswith("--"):
            key = argv[i][2:]
            val = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else "true"
            flags[key] = val
            i += 2 if val != "true" else 1
        else:
            i += 1

    if cmd == "search_addresses":
        OUT(SEARCH_SUGGESTIONS if flags.get("keyword") else SAVED_ADDRESSES)
    elif cmd == "select_address":
        if not flags.get("contact-name") or not flags.get("contact-phone"):
            die("保存地址需要 --contact-name 和 --contact-phone。\n"
                "RECOVERY[CONTACT_REQUIRED]: 问用户收件人姓名和手机号后重试。")
        # 真网关对 POI 候选（requires_detail=true）强制要门牌号
        if flags.get("sug-ref", "").startswith("sug_") and not flags.get("address-detail"):
            die("这个地址是新地点（POI），需要具体门牌号/楼层/房间号。\n"
                "RECOVERY[POI_DETAIL_REQUIRED]: 问到门牌号后带 --address-detail 重试。")
        OUT({"address_id": "addr_new01", "address": "南京市鼓楼区中山路200号",
             "contact_name": flags.get("contact-name"), "tag": flags.get("tag")})
    elif cmd in ("search_shops", "recommend"):
        kw = flags.get("keyword", "")
        base = MLT_MENU if False else None  # noqa: F841 - readability
        shops = SHOPS if "麻辣烫" in kw else TEA_SHOPS
        if cmd == "search_shops":
            OUT(shops)
        else:
            menus = [MLT_MENU] if "麻辣烫" in kw else [TEA_MENU]
            OUT({"shops": shops["shops"], "menus": menus})
    elif cmd == "get_shop_menu":
        sid = flags.get("shop-id", "")
        menu = MLT_MENU if "mlt" in sid else TEA_MENU
        item_id = flags.get("item-id")
        if item_id:
            hit = [i for i in menu["items"] if i["item_id"] == item_id]
            if not hit:
                die(f"商品 {item_id} 不在这家店的菜单里。\nRECOVERY[REFERENCE_STALE]: 重新拉菜单拿新 item_id。")
            OUT(hit[0])
        else:
            OUT(menu)
    elif cmd == "get_item_options":
        try:
            items = json.loads(flags.get("items", "[]"))
        except json.JSONDecodeError:
            die("--items 必须是 JSON 数组")
            return
        sid = flags.get("shop-id", "")
        menu = MLT_MENU if "mlt" in sid else TEA_MENU
        by_id = {i["item_id"]: i for i in menu["items"]}
        OUT({"items": [by_id.get(it.get("item_id"), {"item_id": it.get("item_id"),
                                                     "error": "not found"}) for it in items]})
    elif cmd == "preview_order":
        # 真网关会按 items 校验：id 有效性 / 起送价。mock 同契约，才测得出错误处置行为。
        sid = flags.get("shop-id", "")
        menu = MLT_MENU if "mlt" in sid else TEA_MENU
        prices = {i["item_id"]: i["price"] for i in menu["items"]}
        for g in menu.get("required_groups", []):
            prices.update({c["item_id"]: c["price"] for c in g["candidates"]})
        try:
            items = json.loads(flags.get("items", "[]"))
        except json.JSONDecodeError:
            die("--items 必须是 JSON 数组")
            return
        unknown = [it.get("item_id") for it in items if it.get("item_id") not in prices]
        if unknown:
            die(f"商品 {unknown[0]} 在这家店不存在或已失效。\n"
                "RECOVERY[REFERENCE_STALE]: 重新 get_shop_menu 拿当前店的 item_id 再试，"
                "禁止跨店复用 id、禁止把中文菜名当 item_id。")
        total = sum(prices.get(it.get("item_id"), 0) * max(1, int(it.get("quantity", 1)))
                    for it in items)
        shop_min = next((s["min_order_amount"] for s in
                         (SHOPS["shops"] + TEA_SHOPS["shops"]) if s["shop_id"] == sid), 0)
        if total < shop_min:
            gap = shop_min - total
            die(f"未达起送价，还差 ¥{gap / 100:.1f}。\n"
                f"RECOVERY[BELOW_MIN_ORDER]: get_shop_menu --shop-id {sid} 翻菜单挑 1-2 个低价单品，"
                "或告诉用户差多少让用户决定加什么。涉及花钱必须用户点头。")
        out = dict(PREVIEW)
        out["items"] = [{"name": next((i["name"] for i in menu["items"]
                                       if i["item_id"] == it.get("item_id")), it.get("item_id")),
                         "quantity": int(it.get("quantity", 1)),
                         "price": prices.get(it.get("item_id"), 0)} for it in items]
        out["original_price"] = total
        out["total_price"] = total + 100 + 440 - 500
        OUT(out)
    elif cmd == "create_order":
        if not flags.get("preview-id") or not flags.get("confirmation-token"):
            die("缺少 --preview-id 或 --confirmation-token")
        OUT(ORDER)
    elif cmd == "get_order_status":
        # 真 CLI 必填 --order-id（clawdot.py::cmd_get_order_status），mock 必须同契约，
        # 否则"没 order_id 就该问用户"这条行为断言会被假通过。
        oid = flags.get("order-id")
        if not oid:
            die("缺少 --order-id 参数。")
        if oid != ORDER["order_id"]:
            die(f"订单 {oid} 不存在或不属于该用户。")
        OUT(ORDER_STATUS)
    elif cmd == "get_user_auth_status":
        OUT({"bound": True, "phone": "138****0000", "expires_at": "2026-10-20T00:00:00+08:00"})
    else:
        die(f"未知子命令 {cmd}")


if __name__ == "__main__":
    main()
