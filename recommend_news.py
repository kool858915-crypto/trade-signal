# -*- coding: utf-8 -*-
"""
recommend_news.py — おすすめ選定(チャート分析 + ニュース関連度)

選定ルール:
  1. 大型株24銘柄(従来どおり) ← LARGE_CAPS_24 を既存リストに差し替え可
  2. ニュースで浮上したテーマ株(中型株含む)を候補に追加
  3. チャートスコア(70%) + ニューススコア(30%) でランキング
  4. 上位5件のうち「ニュース関連」を最低2件含める

アプリ側からは get_recommendations() を呼ぶだけ。
返り値はそのまま画面表示に使えるdict(テーマチップ/バッジ/理由つき)。

オフライン確認: python recommend_news.py --mock
"""

from __future__ import annotations

import sys
from trade_news import run as run_news_analysis

# ============================================================
# 大型株24銘柄(※既存アプリのリストがあればそちらに置き換えてください)
# ============================================================
LARGE_CAPS_24: list[tuple[str, str]] = [
    ("7203", "トヨタ自動車"), ("6758", "ソニーグループ"), ("8306", "三菱UFJ FG"),
    ("6861", "キーエンス"), ("8035", "東京エレクトロン"), ("9983", "ファーストリテイリング"),
    ("9984", "ソフトバンクグループ"), ("4063", "信越化学工業"), ("8058", "三菱商事"),
    ("9432", "NTT"), ("6098", "リクルートHD"), ("4568", "第一三共"),
    ("8316", "三井住友FG"), ("6501", "日立製作所"), ("7974", "任天堂"),
    ("6902", "デンソー"), ("4502", "武田薬品工業"), ("8001", "伊藤忠商事"),
    ("6594", "ニデック"), ("6367", "ダイキン工業"), ("7741", "HOYA"),
    ("4519", "中外製薬"), ("8766", "東京海上HD"), ("9433", "KDDI"),
]

NEWS_WEIGHT = 0.3      # 総合スコアに占めるニュース関連度の重み
CHART_WEIGHT = 0.7
MIN_NEWS_IN_TOP5 = 2   # 上位5件に必ず含めるニュース関連銘柄の数
TOP_N = 5


# ============================================================
# チャート分析(yfinance)。取得失敗時は50点(中立)を返す
# ============================================================

def chart_score(code: str, mock: bool = False) -> tuple[float, str]:
    """0〜100のチャートスコアと、根拠の短文を返す"""
    if mock:
        # オフライン確認用: コード数値から擬似スコアを決定的に生成
        s = (int(code) * 37) % 60 + 30
        return float(s), "移動平均上向き(モック)"

    try:
        import yfinance as yf
        df = yf.Ticker(f"{code}.T").history(period="3mo")
        if df is None or len(df) < 30:
            return 50.0, "データ不足のため中立"
        close = df["Close"]
        ma5, ma25 = close.rolling(5).mean(), close.rolling(25).mean()
        score, reasons = 50.0, []

        # ① ゴールデンクロス気味か(短期MA > 長期MA)
        if ma5.iloc[-1] > ma25.iloc[-1]:
            score += 15
            reasons.append("短期線が長期線を上回る")
        # ② 直近5日の上昇率
        chg5 = (close.iloc[-1] / close.iloc[-6] - 1) * 100
        if chg5 > 2:
            score += 15
            reasons.append(f"直近5日で+{chg5:.1f}%")
        elif chg5 < -3:
            score -= 10
            reasons.append(f"直近5日で{chg5:.1f}%")
        # ③ RSI(14): 30以下は売られすぎ=反発期待、70以上は過熱
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] else 0
        rsi = 100 - 100 / (1 + rs) if rs else 50
        if rsi < 35:
            score += 10
            reasons.append(f"RSI{rsi:.0f}で売られすぎ")
        elif rsi > 70:
            score -= 10
            reasons.append(f"RSI{rsi:.0f}で過熱気味")
        # ④ 出来高急増(直近5日平均が25日平均の1.5倍以上)
        vol = df["Volume"]
        if vol.rolling(5).mean().iloc[-1] > vol.rolling(25).mean().iloc[-1] * 1.5:
            score += 10
            reasons.append("出来高が急増")

        score = max(0.0, min(100.0, score))
        return score, "、".join(reasons) or "目立った変化なし"
    except Exception as e:
        return 50.0, f"チャート取得失敗のため中立({type(e).__name__})"


# ============================================================
# おすすめ選定本体
# ============================================================

def get_recommendations(mock: bool = False, top_n: int = TOP_N) -> dict:
    """
    返り値(画面表示にそのまま使えるJSON相当のdict):
    {
      "themes": [ {"theme": "半導体", "score": 5,
                   "headline": "...", "headline_link": "..."} , ... ],
      "recommendations": [
          {"code": "3436", "name": "SUMCO",
           "total_score": 78.5, "chart_score": 72.0, "news_score": 5,
           "badges": ["ニュース関連", "テーマ株"],
           "themes": ["半導体"],
           "reason": "ニュース関連(半導体): 短期線が長期線を上回る、出来高が急増。…"},
          ...
      ]
    }
    """
    # --- 1. ニュース分析 ---
    _, themes, news_candidates = run_news_analysis(mock=mock)
    max_news = max((c["news_score"] for c in news_candidates.values()), default=1)

    # --- 2. 候補プール = 大型株24 + ニュース浮上銘柄 ---
    pool: dict[str, dict] = {}
    for code, name in LARGE_CAPS_24:
        pool[code] = {"name": name, "is_large": True}
    for code, c in news_candidates.items():
        entry = pool.setdefault(code, {"name": c["name"], "is_large": False})
        entry.update({
            "themes": c["themes"],
            "news_score": c["news_score"],
            "headline": c["headline"],
        })

    # --- 3. スコアリング ---
    scored = []
    for code, info in pool.items():
        c_score, c_reason = chart_score(code, mock=mock)
        n_score_raw = info.get("news_score", 0)
        n_score = (n_score_raw / max_news) * 100 if n_score_raw else 0
        total = c_score * CHART_WEIGHT + n_score * NEWS_WEIGHT

        is_news = n_score_raw > 0
        badges = []
        if is_news:
            badges.append("ニュース関連")
        if is_news and not info.get("is_large", False):
            badges.append("テーマ株")

        theme_label = "・".join(info.get("themes", []))
        if is_news:
            head = (info.get("headline") or "")[:40]
            reason = f"ニュース関連({theme_label}): {c_reason}。参考: {head}…"
        else:
            reason = f"チャート分析: {c_reason}"

        scored.append({
            "code": code, "name": info["name"],
            "total_score": round(total, 1),
            "chart_score": round(c_score, 1),
            "news_score": n_score_raw,
            "is_news": is_news,
            "badges": badges,
            "themes": info.get("themes", []),
            "reason": reason,
        })
    scored.sort(key=lambda x: x["total_score"], reverse=True)

    # --- 4. 上位N件に「ニュース関連」を最低2件含める ---
    top = scored[:top_n]
    news_in_top = sum(1 for s in top if s["is_news"])
    if news_in_top < MIN_NEWS_IN_TOP5:
        extra_news = [s for s in scored[top_n:] if s["is_news"]]
        need = MIN_NEWS_IN_TOP5 - news_in_top
        for repl in extra_news[:need]:
            # スコア最下位の「非ニュース銘柄」を入れ替える
            for i in range(len(top) - 1, -1, -1):
                if not top[i]["is_news"]:
                    top[i] = repl
                    break
        top.sort(key=lambda x: x["total_score"], reverse=True)

    return {
        "themes": [
            {"theme": t.theme, "score": t.score,
             "headline": t.headline, "headline_link": t.headline_link}
            for t in themes
        ],
        "recommendations": top,
    }


if __name__ == "__main__":
    result = get_recommendations(mock="--mock" in sys.argv)
    print("=== 本日のニューステーマ(チップ表示用) ===")
    for t in result["themes"]:
        print(f"  ◆ {t['theme']} (score {t['score']})")
    if result["themes"]:
        print(f"\n  参考見出し: {result['themes'][0]['headline']}")
    print("\n=== おすすめ上位5件 ===")
    for r in result["recommendations"]:
        badge = " ".join(f"[{b}]" for b in r["badges"])
        print(f"  {r['code']} {r['name']:<14} 総合{r['total_score']:>5} {badge}")
        print(f"      └ {r['reason']}")
    news_count = sum(1 for r in result["recommendations"] if r["is_news"])
    print(f"\n  ニュース関連: {news_count}件 (最低{MIN_NEWS_IN_TOP5}件ルール)")
