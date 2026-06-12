# -*- coding: utf-8 -*-
"""
trade_news.py — ニュース分析モジュール(APIキー不要)

  1. Google News RSS と yfinance から市場ニュース見出しを取得
  2. 見出しのキーワードからテーマを自動判定(半導体/AI/自動車/防衛/医薬/...)
  3. テーマごとの銘柄マップ(中型株含む)から「ニュース浮上銘柄」を返す

単体テスト:  python trade_news.py            (実際にRSSを取得して表示)
オフライン:  python trade_news.py --mock     (サンプル見出しで動作確認)
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# ============================================================
# テーマ定義: キーワード + 関連銘柄マップ(中型株を含む)
# ============================================================

THEME_MAP: dict[str, dict] = {
    "半導体": {
        "keywords": ["半導体", "チップ", "TSMC", "エヌビディア", "NVIDIA", "ファウンドリ",
                     "シリコンウエハ", "露光", "後工程", "HBM", "メモリ", "DRAM", "NAND"],
        "stocks": [
            ("8035", "東京エレクトロン"), ("6857", "アドバンテスト"),
            ("6146", "ディスコ"), ("6920", "レーザーテック"),
            ("6723", "ルネサスエレクトロニクス"), ("3436", "SUMCO"),
            ("7735", "SCREENホールディングス"), ("6963", "ローム"),
        ],
    },
    "AI": {
        "keywords": ["AI", "人工知能", "生成AI", "ChatGPT", "Claude", "LLM",
                     "データセンター", "機械学習", "クラウド"],
        "stocks": [
            ("9613", "NTTデータグループ"), ("3993", "PKSHA Technology"),
            ("4382", "HEROZ"), ("6701", "NEC"), ("6702", "富士通"),
            ("3655", "ブレインパッド"), ("4180", "Appier Group"),
        ],
    },
    "自動車": {
        "keywords": ["自動車", "EV", "電気自動車", "トヨタ", "ホンダ", "日産",
                     "車載", "自動運転", "ハイブリッド", "関税"],
        "stocks": [
            ("7203", "トヨタ自動車"), ("7267", "ホンダ"), ("7201", "日産自動車"),
            ("7269", "スズキ"), ("6902", "デンソー"), ("7259", "アイシン"),
            ("5108", "ブリヂストン"),
        ],
    },
    "防衛": {
        "keywords": ["防衛", "防衛費", "安全保障", "ミサイル", "自衛隊", "軍事",
                     "地政学", "NATO"],
        "stocks": [
            ("7011", "三菱重工業"), ("7013", "IHI"), ("7012", "川崎重工業"),
            ("6203", "豊和工業"), ("6208", "石川製作所"), ("6946", "日本アビオニクス"),
        ],
    },
    "医薬・ヘルスケア": {
        "keywords": ["医薬", "新薬", "治験", "承認", "ワクチン", "がん", "創薬",
                     "バイオ", "厚労省"],
        "stocks": [
            ("4568", "第一三共"), ("4502", "武田薬品工業"), ("4503", "アステラス製薬"),
            ("4519", "中外製薬"), ("4587", "ペプチドリーム"), ("4565", "そーせいグループ"),
        ],
    },
    "不動産・建設": {
        "keywords": ["不動産", "地価", "再開発", "マンション", "建設", "ゼネコン",
                     "オフィスビル", "REIT"],
        "stocks": [
            ("8801", "三井不動産"), ("8802", "三菱地所"), ("3289", "東急不動産HD"),
            ("1801", "大成建設"), ("1812", "鹿島建設"), ("1928", "積水ハウス"),
        ],
    },
    "エネルギー": {
        "keywords": ["原油", "電力", "再生可能エネルギー", "太陽光", "原発", "原子力",
                     "LNG", "脱炭素", "電気料金"],
        "stocks": [
            ("5020", "ENEOSホールディングス"), ("1605", "INPEX"),
            ("9501", "東京電力HD"), ("9503", "関西電力"),
            ("9519", "レノバ"), ("1407", "ウエストHD"),
        ],
    },
    "金融": {
        "keywords": ["日銀", "利上げ", "金利", "為替", "円安", "円高", "銀行",
                     "メガバンク", "金融政策"],
        "stocks": [
            ("8306", "三菱UFJ FG"), ("8316", "三井住友FG"), ("8411", "みずほFG"),
            ("8591", "オリックス"), ("8604", "野村ホールディングス"), ("7186", "コンコルディアFG"),
        ],
    },
    "ゲーム・エンタメ": {
        "keywords": ["ゲーム", "任天堂", "Switch", "プレイステーション", "アニメ",
                     "eスポーツ", "配信"],
        "stocks": [
            ("7974", "任天堂"), ("6758", "ソニーグループ"), ("9684", "スクウェア・エニックスHD"),
            ("3659", "ネクソン"), ("9468", "KADOKAWA"), ("2432", "ディー・エヌ・エー"),
        ],
    },
    "商社・資源": {
        "keywords": ["商社", "資源", "鉄鉱石", "銅", "バフェット", "穀物"],
        "stocks": [
            ("8058", "三菱商事"), ("8031", "三井物産"), ("8001", "伊藤忠商事"),
            ("8002", "丸紅"), ("2768", "双日"),
        ],
    },
    "インバウンド・小売": {
        "keywords": ["インバウンド", "訪日", "観光", "百貨店", "免税", "ホテル", "旅行"],
        "stocks": [
            ("3099", "三越伊勢丹HD"), ("8233", "高島屋"), ("9603", "エイチ・アイ・エス"),
            ("6191", "エアトリ"), ("4661", "オリエンタルランド"), ("9616", "共立メンテナンス"),
        ],
    },
}

# Google News RSS の検索クエリ(広めに市場ニュースを拾う)
NEWS_QUERIES = ["株式市場", "日経平均", "東証 株"]

UA = {"User-Agent": "Mozilla/5.0 (TradeNewsBot; personal paper-trading demo)"}

_CACHE_TTL_MIN = 30


# ============================================================
# データ構造
# ============================================================

@dataclass
class NewsItem:
    title: str
    link: str = ""
    published: str = ""
    source: str = ""


@dataclass
class ThemeResult:
    theme: str
    score: int                      # 見出しヒット数(重複加算)
    headline: str                   # 参考見出し(最新ニュース1件)
    headline_link: str = ""
    stocks: list = field(default_factory=list)   # [(code, name), ...]


# ============================================================
# ニュース取得
# ============================================================

def fetch_google_news(query: str, limit: int = 20, timeout: int = 10) -> list[NewsItem]:
    """Google News RSS から見出しを取得(APIキー不要)"""
    url = ("https://news.google.com/rss/search?q="
           + urllib.parse.quote(query)
           + "&hl=ja&gl=JP&ceid=JP:ja")
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            xml_data = r.read()
        root = ET.fromstring(xml_data)
        items = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            if not title:
                continue
            items.append(NewsItem(
                title=title,
                link=(it.findtext("link") or "").strip(),
                published=(it.findtext("pubDate") or "").strip(),
                source="GoogleNews",
            ))
            if len(items) >= limit:
                break
        return items
    except Exception as e:
        print(f"[trade_news] Google News取得失敗 ({query}): {e}", file=sys.stderr)
        return []


def fetch_yfinance_news(tickers: list[str] | None = None, limit_per: int = 5) -> list[NewsItem]:
    """yfinance のニュース(日経平均・主要銘柄)。失敗しても全体は止めない。"""
    items: list[NewsItem] = []
    try:
        import yfinance as yf
    except ImportError:
        return items
    for t in (tickers or ["^N225", "7203.T", "8035.T"]):
        try:
            for n in (yf.Ticker(t).news or [])[:limit_per]:
                c = n.get("content", n)  # yfinanceのバージョン差を吸収
                title = c.get("title") or ""
                if title:
                    items.append(NewsItem(
                        title=title,
                        link=(c.get("canonicalUrl") or {}).get("url", "") if isinstance(c.get("canonicalUrl"), dict) else c.get("link", ""),
                        published=str(c.get("pubDate") or c.get("providerPublishTime") or ""),
                        source=f"yfinance:{t}",
                    ))
        except Exception:
            continue
    return items


def fetch_all_news() -> list[NewsItem]:
    """全ソースから取得して重複タイトルを除去"""
    items: list[NewsItem] = []
    for q in NEWS_QUERIES:
        items.extend(fetch_google_news(q))
        time.sleep(0.5)  # 連続アクセスを避ける
    items.extend(fetch_yfinance_news())
    seen, uniq = set(), []
    for it in items:
        key = re.sub(r"\s+", "", it.title)[:30]
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq


# ============================================================
# テーマ判定
# ============================================================

def analyze_themes(news: list[NewsItem], top_n: int = 4) -> list[ThemeResult]:
    """見出し群からテーマを判定し、スコア順に上位 top_n 件を返す"""
    results: list[ThemeResult] = []
    for theme, cfg in THEME_MAP.items():
        score = 0
        headline, link, best_hits = "", "", 0
        for item in news:
            hits = sum(1 for kw in cfg["keywords"] if kw.lower() in item.title.lower())
            if hits:
                score += hits
                if hits > best_hits:      # 最も強くヒットした見出しを代表にする
                    best_hits, headline, link = hits, item.title, item.link
        if score > 0:
            results.append(ThemeResult(
                theme=theme, score=score,
                headline=headline, headline_link=link,
                stocks=list(cfg["stocks"]),
            ))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_n]


def get_news_candidates(themes: list[ThemeResult]) -> dict[str, dict]:
    """
    テーマ判定結果 → 銘柄候補の辞書
    返り値: { "3436": {"name": "SUMCO", "themes": ["半導体"], "news_score": 5,
                        "headline": "...", "headline_link": "..."} , ... }
    """
    candidates: dict[str, dict] = {}
    for tr in themes:
        for code, name in tr.stocks:
            c = candidates.setdefault(code, {
                "name": name, "themes": [], "news_score": 0,
                "headline": tr.headline, "headline_link": tr.headline_link,
            })
            if tr.theme not in c["themes"]:
                c["themes"].append(tr.theme)
            c["news_score"] += tr.score
    return candidates


def run(mock: bool = False):
    """取得 → 分析 → 候補化 をまとめて実行。アプリ側はこれを呼ぶだけでOK。"""
    if mock:
        news = [
            NewsItem("半導体大手TSMC、熊本第2工場の建設を前倒し 関連株に買い"),
            NewsItem("生成AI需要でデータセンター投資が加速、電力株にも波及"),
            NewsItem("地価上昇続く 都心再開発で不動産大手が最高益"),
            NewsItem("日銀、追加利上げを見送り 円安進行で銀行株はまちまち"),
            NewsItem("新薬承認で第一三共が年初来高値 がん治療薬に期待"),
        ]
    else:
        news = fetch_all_news()
    themes = analyze_themes(news)
    candidates = get_news_candidates(themes)
    return news, themes, candidates


# ============================================================
# アプリ連携用ラッパー (trade_recommend / server.py 向け)
# ============================================================

def _cache_key(market: str) -> str:
    return f"news_scan_{market}"


def _load_cache(market: str) -> dict | None:
    try:
        import trade_core as tc
        raw = tc._get_setting(_cache_key(market))
        if not raw:
            return None
        data = json.loads(raw)
        at = datetime.strptime(data.get("cached_at", ""), "%Y-%m-%d %H:%M")
        if datetime.now() - at < timedelta(minutes=_CACHE_TTL_MIN):
            return data
    except (ImportError, json.JSONDecodeError, ValueError):
        pass
    return None


def _save_cache(market: str, data: dict):
    try:
        import trade_core as tc
        data["cached_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        tc._set_setting(_cache_key(market), json.dumps(data, ensure_ascii=False))
    except ImportError:
        pass


def get_extended_universe(market: str = "jp") -> dict[str, str]:
    """テーママップ内の全銘柄（中型株含む）"""
    if market != "jp":
        return {}
    universe: dict[str, str] = {}
    for cfg in THEME_MAP.values():
        for code, name in cfg["stocks"]:
            universe[code] = name
    return universe


def news_bonus(news_score: int, has_signal: bool) -> int:
    bonus = min(news_score * 80, 320)
    if has_signal:
        bonus += 120
    return bonus


def _build_context(news: list[NewsItem], themes: list[ThemeResult],
                   candidates: dict[str, dict]) -> dict:
    ticker_map = {}
    for code, info in candidates.items():
        headline = info.get("headline", "")
        ticker_map[code] = {
            "name": info["name"],
            "themes": info.get("themes", []),
            "news_score": info.get("news_score", 0),
            "headline": headline,
            "headline_link": info.get("headline_link", ""),
            "headlines": [headline] if headline else [],
        }

    theme_list = [
        {
            "id": tr.theme,
            "label": tr.theme,
            "score": tr.score,
            "headlines": [tr.headline] if tr.headline else [],
            "headline_link": tr.headline_link,
            "tickers": [code for code, _ in tr.stocks],
        }
        for tr in themes
    ]

    return {
        "headlines": [
            {"title": n.title, "link": n.link, "published": n.published}
            for n in news[:10]
        ],
        "themes": theme_list,
        "ticker_map": ticker_map,
        "active_theme_count": len(themes),
    }


def get_news_context(market: str = "jp", force_refresh: bool = False) -> dict:
    """おすすめ銘柄モジュール向けのニュース分析結果（30分キャッシュ）"""
    if market != "jp":
        return {
            "headlines": [], "themes": [], "ticker_map": {},
            "active_theme_count": 0,
        }

    if not force_refresh:
        cached = _load_cache(market)
        if cached:
            return cached

    news, themes, candidates = run(mock=False)
    result = _build_context(news, themes, candidates)
    _save_cache(market, result)
    return result


if __name__ == "__main__":
    is_mock = "--mock" in sys.argv
    news, themes, candidates = run(mock=is_mock)
    print(f"取得ニュース: {len(news)}件\n")
    print("=== 本日のニューステーマ ===")
    for t in themes:
        print(f"  [{t.theme}] score={t.score}")
        print(f"    参考見出し: {t.headline}")
    print("\n=== ニュース浮上銘柄 ===")
    for code, c in sorted(candidates.items(), key=lambda x: -x[1]["news_score"]):
        print(f"  {code} {c['name']:<14} themes={c['themes']} score={c['news_score']}")
