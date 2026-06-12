#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""初心者向けおすすめ銘柄の選定と理由付け（ニュース連動 + 中型株含む）"""

import json
from datetime import datetime

import trade_core as tc
import trade_news as tn

# 売買しやすい大型・有名銘柄プール
RECOMMEND_POOL = {
    "jp": {
        "7203": "トヨタ自動車 — 国内最大の売買代金、値動きが読みやすい",
        "6758": "ソニーグループ — 世界的ブランド、流動性が高い",
        "8306": "三菱UFJ — 大型金融株、相場の方向感が出やすい",
        "9984": "ソフトバンクG — ボラティリティあり、デイトレ向き",
        "6861": "キーエンス — 高値株だが売買単位100株で参加しやすい",
        "4063": "信越化学 — 半導体関連の代表、トレンドが明確になりやすい",
        "8035": "東京エレクトロン — 半導体設備の大手、出来高が厚い",
        "6098": "リクルート — 成長株代表、トレンドフォローしやすい",
        "9432": "日本電信電話 — 安定大型株、初心者の練習向き",
        "4519": "中外製薬 — ディフェンシブだが流動性十分",
        "6367": "ダイキン — 家電大手、海外需要テーマ",
        "6902": "デンソー — 自動車関連、トヨタと連動しやすい",
        "7974": "任天堂 — 個人投資家に人気、値動きが大きめ",
        "6501": "日立製作所 — 総合電機大手、日経の主役級",
        "7267": "ホンダ — 自動車セクター、トヨタとセットで見やすい",
        "8058": "三菱商事 — 商社代表、景気敏感でトレンドが出やすい",
        "9433": "KDDI — 通信セクター、安定しつつ動きもある",
        "7751": "キヤノン — 精密機器大手、外国人投資家も多い",
        "6981": "村田製作所 — 電子部品、半導体サイクル連動",
        "4502": "武田薬品 — 医薬品最大手、長期トレンド向き",
        "3382": "セブン&アイ — 小売大手、内需テーマ",
        "2914": "日本たばこ — 高配当・安定、スイング向き",
        "8411": "みずほFG — 金融セクター、市場全体の体温計",
        "6273": "SMC — 自動化関連、成長テーマ",
    },
    "us": {
        "AAPL": "Apple — 世界最大級の流動性、初心者の定番",
        "MSFT": "Microsoft — 安定成長、トレンドが読みやすい",
        "NVDA": "NVIDIA — AI関連の中心、値動き大きめ",
        "GOOGL": "Alphabet — 大型テック、出来高が厚い",
        "AMZN": "Amazon — 消費・クラウドテーマ",
        "META": "Meta — SNS大手、ボラティリティあり",
        "TSLA": "Tesla — 個人投資家に人気、動きが大きい",
        "JPM": "JPMorgan — 金融セクター代表",
        "V": "Visa — 決済大手、安定トレンド",
        "JNJ": "Johnson&Johnson — ディフェンシブ大型",
        "WMT": "Walmart — 消費セクター、景気後退時も堅い",
        "AMD": "AMD — 半導体、NVDAとセットで見やすい",
        "NFLX": "Netflix — グロース株、トレンドフォロー向き",
        "DIS": "Disney — エンタメ大手、イベントドリブン",
        "BAC": "Bank of America — 金利テーマに連動",
    },
}

COND_REASONS = {
    "ゴールデンクロス": "短期トレンドが上向きに転じた（買いのサイン）",
    "デッドクロス": "短期トレンドが下向きに転じた（売りのサイン）",
    "VWAPより上": "今日の平均価格より上 → 買い優勢の流れ",
    "VWAPより下": "今日の平均価格より下 → 売り優勢の流れ",
    "出来高増加": "注目が集まっており、値動きが本物になりやすい",
    "過熱でない": "買われ過ぎではなく、まだ伸び余地がある",
    "売られ過ぎでない": "売られ過ぎではなく、急落リスクが低め",
    "が上向き": "長期トレンドが上向きで、押し目買いしやすい",
    "が下向き": "長期トレンドが下向きで、戻り売りしやすい",
}


def _cond_to_reason(name: str) -> str | None:
    for key, reason in COND_REASONS.items():
        if key in name:
            return reason
    return None


def _build_reasons(signal: str, conds: list, cond_score: int,
                   pool_note: str, news_info: dict | None) -> list[str]:
    reasons = []

    if news_info:
        themes = "・".join(news_info.get("themes", [])[:2])
        headline = news_info.get("headline") or (news_info.get("headlines") or [""])[0]
        if headline:
            short = headline[:70] + ("…" if len(headline) > 70 else "")
            reasons.append(f"ニュース関連（{themes}）: {short}")
        else:
            reasons.append(f"ニュース関連テーマ: {themes}")
        if news_info.get("name"):
            reasons.append(f"{news_info['name']} — 中型・テーマ株も視野に入れた候補")

    if pool_note and " — " in pool_note:
        reasons.append(pool_note)

    if signal == "買い":
        reasons.insert(0, "今すぐ注目: 買いシグナルが出ています")
    elif signal == "売り":
        reasons.insert(0, "売りシグナルが出ています（下落トレンド注意）")
    elif cond_score >= 2:
        reasons.insert(0, f"条件{cond_score}/4を満たし、シグナルに近い状態です")
    elif not news_info:
        reasons.insert(0, "大型株として監視価値あり（様子見中）")
    else:
        reasons.insert(0, "ニュースで注目されているが、チャートは様子見")

    for c in conds:
        if not c.get("ok"):
            continue
        r = _cond_to_reason(c["name"])
        if r and r not in reasons:
            reasons.append(r)

    return reasons[:6]


def _rank_item(signal: str, cond_score: int, has_cross: bool) -> int:
    if signal == "買い":
        return 1000 + cond_score
    if signal == "売り":
        return 800 + cond_score
    if has_cross:
        return 500 + cond_score
    return cond_score


def _build_scan_universe(market: str, news_ctx: dict) -> dict[str, dict]:
    """大型株 + ニュースで浮上したテーマ株（中型含む）を統合"""
    universe = {}
    extended = tn.get_extended_universe(market)

    for ticker, note in RECOMMEND_POOL.get(market, {}).items():
        universe[ticker] = {
            "name": note.split(" — ")[0],
            "note": note,
            "source": "large_cap",
        }

    for ticker, info in news_ctx.get("ticker_map", {}).items():
        name = info["name"]
        in_large = ticker in RECOMMEND_POOL
        if ticker not in universe:
            label = "大型" if in_large else "中型・テーマ"
            universe[ticker] = {
                "name": name,
                "note": f"{name} — ニュース関連（{label}株）",
                "source": "news",
            }
        else:
            universe[ticker]["source"] = "news"
            universe[ticker]["note"] = (
                f"{universe[ticker]['name']} — ニュースでも注目の大型株"
            )
        universe[ticker]["news_info"] = info

    # ニュース未検出時はテーマ株から数件だけ候補に追加（中型株の視野確保）
    if not news_ctx.get("ticker_map"):
        extras = [t for t in extended if t not in universe][:6]
        for ticker in extras:
            name = extended[ticker]
            universe[ticker] = {
                "name": name,
                "note": f"{name} — テーマ株（中型含む、ニュース待ち）",
                "source": "theme",
            }

    return universe


def _evaluate_ticker(ticker: str, meta: dict, ctx: tc.Ctx) -> dict | None:
    item = {
        "ticker": ticker,
        "name": meta["name"],
        "source": meta.get("source", "large_cap"),
    }
    news_info = meta.get("news_info")

    try:
        df = tc.add_indicators(tc.fetch_data(ticker, ctx), ctx)
    except Exception as e:
        item["error"] = str(e)
        return item

    if len(df) < ctx.s["slow"] + 6:
        item["error"] = "データ不足"
        return item

    r = tc.evaluate(df, ctx)
    conds = [{"name": k, "ok": bool(v)} for k, v in r["conds"].items()]
    cond_score = sum(1 for c in conds if c["ok"])
    has_cross = "クロス" in "".join(
        k for k, v in r["conds"].items() if v and "クロス" in k
    )

    t = r["time"]
    time_str = t.strftime("%m/%d %H:%M") if hasattr(t, "strftime") else str(t)

    rank_score = _rank_item(r["signal"], cond_score, has_cross)
    if news_info:
        rank_score += tn.news_bonus(
            news_info.get("news_score", 0),
            r["signal"] in ("買い", "売り"),
        )
        item["news_themes"] = news_info.get("themes", [])
        item["news_headline"] = (
            news_info.get("headline") or (news_info.get("headlines") or [""])[0]
        )

    item.update({
        "signal": r["signal"],
        "price": r["price"],
        "rsi": r["rsi"],
        "time": time_str,
        "cond_score": cond_score,
        "rank_score": rank_score,
        "reasons": _build_reasons(
            r["signal"], conds, cond_score, meta.get("note", ""),
            news_info,
        ),
        "conds": conds,
    })
    if r["signal"] in ("買い", "売り"):
        p = tc.plan_trade(r["price"], r["atr"], r["signal"], ctx)
        item["plan"] = {"stop": p["stop"], "target": p["target"], "qty": p["qty"]}
    return item


def _pick_diverse_top(candidates: list, limit: int) -> list:
    """ニュース関連を最低2件含めつつ上位を選ぶ"""
    ranked = sorted(candidates, key=lambda x: -x["rank_score"])
    news_items = [c for c in ranked if c.get("source") == "news" or c.get("news_themes")]
    other_items = [c for c in ranked if c not in news_items]

    picked = []
    for c in news_items[: max(2, limit // 2)]:
        picked.append(c)
    for c in ranked:
        if c not in picked:
            picked.append(c)
        if len(picked) >= limit:
            break
    return picked[:limit]


def _enrich_recommendation(r: dict, ctx: tc.Ctx) -> dict:
    """recommend_news の結果にシグナル・現値を付与"""
    ticker = r["ticker"]
    item = dict(r)
    item.setdefault("signal", "様子見")
    item.setdefault("cond_score", 0)
    try:
        df = tc.add_indicators(tc.fetch_data(ticker, ctx), ctx)
        if len(df) < ctx.s["slow"] + 6:
            return item
        ev = tc.evaluate(df, ctx)
        conds = [{"name": k, "ok": bool(v)} for k, v in ev["conds"].items()]
        item.update({
            "signal": ev["signal"],
            "price": ev["price"],
            "rsi": ev["rsi"],
            "cond_score": sum(1 for c in conds if c["ok"]),
            "conds": conds,
        })
        if ev["signal"] in ("買い", "売り"):
            p = tc.plan_trade(ev["price"], ev["atr"], ev["signal"], ctx)
            item["plan"] = {
                "stop": p["stop"], "target": p["target"], "qty": p["qty"],
            }
    except Exception:
        pass
    return item


def _action_recommend_jp(style: str, limit: int) -> dict:
    """チャート70% + ニュース30% の選定(recommend_news.py)"""
    import recommend_news as rn

    ctx = tc.Ctx("jp", style)
    result = rn.get_recommendations(mock=False, top_n=limit)

    top = []
    for r in result["recommendations"]:
        item = {
            "ticker": r["code"],
            "name": r["name"],
            "total_score": r["total_score"],
            "chart_score": r["chart_score"],
            "news_score": r["news_score"],
            "badges": r["badges"],
            "news_themes": r["themes"],
            "reasons": [r["reason"]],
            "source": "news" if r["is_news"] else "large_cap",
        }
        top.append(_enrich_recommendation(item, ctx))

    tickers = [c["ticker"] for c in top]
    tc._set_setting(f"recommended_jp_{style}", json.dumps(tickers))
    tc._set_setting(
        f"recommended_jp_{style}_at",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    themes = result.get("themes", [])
    theme_labels = [t["theme"] for t in themes]
    headlines = [t["headline"] for t in themes if t.get("headline")]
    news_count = sum(1 for c in top if c.get("news_themes"))

    return {
        "ok": True,
        "market": "jp",
        "style": style,
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pool_size": len(rn.LARGE_CAPS_24) + len(tn.get_extended_universe("jp")),
        "large_cap_count": len(rn.LARGE_CAPS_24),
        "theme_stock_count": len(tn.get_extended_universe("jp")),
        "recommendations": top,
        "news": {
            "active_themes": theme_labels,
            "theme_count": len(themes),
            "headlines": headlines[:5],
            "news_pick_count": news_count,
        },
        "note": (
            f"チャート{int(rn.CHART_WEIGHT * 100)}% + "
            f"ニュース{int(rn.NEWS_WEIGHT * 100)}%で選定。"
            f"大型株{len(rn.LARGE_CAPS_24)} + ニュース浮上株から上位{limit}件"
        ),
        "scoring": "chart70_news30",
    }


def action_recommend(market: str = "jp", style: str = "day", limit: int = 5) -> dict:
    """おすすめ上位を理由付きで返す（日本株は recommend_news、米国株は従来ロジック）"""
    if market == "jp":
        return _action_recommend_jp(style, limit)

    ctx = tc.Ctx(market, style)
    news_ctx = tn.get_news_context(market)
    universe = _build_scan_universe(market, news_ctx)

    candidates = []
    for ticker, meta in universe.items():
        result = _evaluate_ticker(ticker, meta, ctx)
        if result:
            candidates.append(result)

    ok_items = [c for c in candidates if "error" not in c]
    top = _pick_diverse_top(ok_items, limit)

    tickers = [c["ticker"] for c in top]
    tc._set_setting(f"recommended_{market}_{style}", json.dumps(tickers))
    tc._set_setting(
        f"recommended_{market}_{style}_at",
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    news_count = sum(1 for c in top if c.get("news_themes"))
    theme_labels = [t.get("label", t.get("id", "")) for t in news_ctx.get("themes", [])[:5]]

    return {
        "ok": True,
        "market": market,
        "style": style,
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pool_size": len(universe),
        "large_cap_count": len(RECOMMEND_POOL.get(market, {})),
        "theme_stock_count": len(tn.get_extended_universe(market)),
        "recommendations": top,
        "news": {
            "active_themes": theme_labels,
            "theme_count": news_ctx.get("active_theme_count", 0),
            "headlines": [h["title"] for h in news_ctx.get("headlines", [])[:5]],
            "news_pick_count": news_count,
        },
        "note": (
            f"大型株{len(RECOMMEND_POOL.get(market, {}))}銘柄 + "
            f"テーマ株{len(tn.get_extended_universe(market))}銘柄 + "
            f"本日のニュース({len(theme_labels)}テーマ)から上位{limit}件"
        ),
    }


def get_recommended_tickers(market: str, style: str) -> list[str]:
    raw = tc._get_setting(f"recommended_{market}_{style}")
    if raw:
        try:
            tickers = json.loads(raw)
            if tickers:
                return tickers
        except json.JSONDecodeError:
            pass
    return tc.MARKETS[market]["watchlist"]


def get_ticker_label(market: str, ticker: str) -> str:
    pool = RECOMMEND_POOL.get(market, {})
    if ticker in pool:
        note = pool[ticker]
        return note.split(" — ")[0] if " — " in note else note
    ext = tn.get_extended_universe(market)
    return ext.get(ticker, ticker)
