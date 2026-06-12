# -*- coding: utf-8 -*-
"""
trade_news.py — ニュース分析モジュール v2(波及銘柄方式・APIキー不要)

考え方:
  ニュースで「AI」が話題 → AIの銘柄そのものではなく、
  「データセンター増設 → 冷却液・空調」「電力需要 → 電線・変圧器」のように
  一歩先で恩恵を受ける【波及銘柄】を提案する。

構造:
  THEME_MAP[テーマ] = {
      keywords: ニュース見出しの判定キーワード,
      derived:  [ {label: 波及先, logic: 連想チェーンの説明, stocks: [...]}, ... ]
  }

単体テスト:  python trade_news.py            (実際にRSSを取得)
オフライン:  python trade_news.py --mock     (サンプル見出しで確認)
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
# テーマ → 波及チェーン → 銘柄マップ(中型株を含む)
#   logic は「なぜその銘柄に波及するか」の連想を書く。理由文にそのまま使う。
# ============================================================

THEME_MAP: dict[str, dict] = {
    "AI": {
        "keywords": ["AI", "人工知能", "生成AI", "ChatGPT", "Claude", "LLM",
                     "エヌビディア", "NVIDIA", "データセンター", "機械学習"],
        "derived": [
            {"label": "冷却・空調設備",
             "logic": "AI需要拡大→データセンター増設→サーバー冷却(液冷・空調)需要",
             "stocks": [("6367", "ダイキン工業"), ("6504", "富士電機"),
                        ("1969", "高砂熱学工業"), ("6458", "新晃工業")]},
            {"label": "電線・電力ケーブル",
             "logic": "データセンター増設→電力供給網の増強→電線需要",
             "stocks": [("5803", "フジクラ"), ("5801", "古河電気工業"),
                        ("5802", "住友電気工業")]},
            {"label": "DC建設・電気工事",
             "logic": "データセンター新設ラッシュ→建設・電気設備工事の受注増",
             "stocks": [("1944", "きんでん"), ("1959", "九電工"),
                        ("1721", "コムシスHD")]},
            {"label": "変圧器・受電設備",
             "logic": "AIサーバーの大電力消費→受変電設備の更新需要",
             "stocks": [("6617", "東光高岳"), ("6622", "ダイヘン"),
                        ("6503", "三菱電機")]},
        ],
    },
    "半導体": {
        "keywords": ["半導体", "チップ", "TSMC", "ファウンドリ", "露光",
                     "後工程", "HBM", "メモリ", "DRAM", "NAND", "ラピダス"],
        "derived": [
            {"label": "シリコンウエハ",
             "logic": "半導体増産→上流のウエハ需要が先行して増える",
             "stocks": [("3436", "SUMCO"), ("4063", "信越化学工業")]},
            {"label": "後工程・検査装置",
             "logic": "チップ高性能化→切断・研磨・検査の工程価値が上昇",
             "stocks": [("6146", "ディスコ"), ("6857", "アドバンテスト"),
                        ("6920", "レーザーテック")]},
            {"label": "半導体材料・薬品",
             "logic": "工場稼働率上昇→フォトレジスト・薬液の消耗品需要",
             "stocks": [("4186", "東京応化工業"), ("4004", "レゾナックHD"),
                        ("5214", "日本電気硝子")]},
            {"label": "搬送・クリーンルーム",
             "logic": "新工場建設(熊本・北海道など)→搬送装置と空調設備",
             "stocks": [("6383", "ダイフク"), ("1969", "高砂熱学工業"),
                        ("6135", "牧野フライス製作所")]},
        ],
    },
    "自動車・EV": {
        "keywords": ["自動車", "EV", "電気自動車", "トヨタ", "ホンダ", "日産",
                     "車載", "自動運転", "ハイブリッド", "関税"],
        "derived": [
            {"label": "車載電池・素材",
             "logic": "EVシフト→電池とニッケル・リチウム等の素材需要",
             "stocks": [("6674", "GSユアサ"), ("5713", "住友金属鉱山"),
                        ("6752", "パナソニックHD")]},
            {"label": "モーター・駆動部品",
             "logic": "EV化→エンジンからモーター・インバーターへ置き換え",
             "stocks": [("6594", "ニデック"), ("6902", "デンソー"),
                        ("7259", "アイシン")]},
            {"label": "充電インフラ",
             "logic": "EV普及→充電スタンド・急速充電器の整備需要",
             "stocks": [("6617", "東光高岳"), ("6644", "大崎電気工業")]},
        ],
    },
    "防衛": {
        "keywords": ["防衛", "防衛費", "安全保障", "ミサイル", "自衛隊", "軍事",
                     "地政学", "NATO"],
        "derived": [
            {"label": "重工・装備品",
             "logic": "防衛費増額→装備品・艦艇・航空機の受注増",
             "stocks": [("7011", "三菱重工業"), ("7013", "IHI"),
                        ("7012", "川崎重工業")]},
            {"label": "電子・通信機器",
             "logic": "防衛のデジタル化→レーダー・通信・電子戦装備",
             "stocks": [("6503", "三菱電機"), ("6946", "日本アビオニクス"),
                        ("7947", "エフピコ")]},
            {"label": "素材(炭素繊維等)",
             "logic": "航空・防衛装備→軽量高強度素材の需要",
             "stocks": [("3402", "東レ"), ("5301", "東海カーボン")]},
        ],
    },
    "医薬・ヘルスケア": {
        "keywords": ["医薬", "新薬", "治験", "承認", "ワクチン", "がん", "創薬",
                     "バイオ", "厚労省"],
        "derived": [
            {"label": "医薬品受託製造(CDMO)",
             "logic": "新薬承認ラッシュ→製造を受託する企業に量産需要",
             "stocks": [("4901", "富士フイルムHD"), ("4581", "大正製薬HD")]},
            {"label": "治験・開発支援",
             "logic": "創薬活発化→治験運営・データ管理の外注増",
             "stocks": [("2309", "シミックHD"), ("2160", "ジーエヌアイグループ")]},
            {"label": "医療機器・検査",
             "logic": "診断・治療の高度化→機器と検査試薬の需要",
             "stocks": [("4543", "テルモ"), ("7733", "オリンパス"),
                        ("6869", "シスメックス")]},
        ],
    },
    "不動産・再開発": {
        "keywords": ["不動産", "地価", "再開発", "マンション", "建設", "ゼネコン",
                     "オフィスビル", "REIT"],
        "derived": [
            {"label": "建設機械",
             "logic": "再開発・着工増→建機の稼働率とレンタル需要",
             "stocks": [("6301", "コマツ"), ("6305", "日立建機"),
                        ("9678", "カナモト")]},
            {"label": "セメント・建材",
             "logic": "着工増→セメント・ガラス・建材の出荷増",
             "stocks": [("5233", "太平洋セメント"), ("5201", "AGC"),
                        ("5938", "LIXIL")]},
            {"label": "電気設備工事",
             "logic": "ビル新築→受変電・照明など電気設備工事の受注",
             "stocks": [("1944", "きんでん"), ("1959", "九電工"),
                        ("1942", "関電工")]},
        ],
    },
    "エネルギー・電力": {
        "keywords": ["原油", "電力", "再生可能エネルギー", "太陽光", "原発",
                     "原子力", "LNG", "脱炭素", "電気料金", "送電"],
        "derived": [
            {"label": "送配電・電線",
             "logic": "電力需要増・再エネ接続→送配電網の増強投資",
             "stocks": [("5803", "フジクラ"), ("5801", "古河電気工業"),
                        ("6617", "東光高岳")]},
            {"label": "原発再稼働関連",
             "logic": "原発再稼働→保守・部材・計測機器の需要",
             "stocks": [("7011", "三菱重工業"), ("7711", "助川電気工業"),
                        ("1963", "日揮HD")]},
            {"label": "電力工事",
             "logic": "送電網・再エネ設備の建設→電気工事会社の受注",
             "stocks": [("1959", "九電工"), ("1942", "関電工"),
                        ("1407", "ウエストHD")]},
        ],
    },
    "金融・金利": {
        "keywords": ["日銀", "利上げ", "金利", "為替", "円安", "円高",
                     "メガバンク", "金融政策"],
        "derived": [
            {"label": "保険(運用利回り改善)",
             "logic": "金利上昇→保険会社の運用利回りが改善",
             "stocks": [("8766", "東京海上HD"), ("8750", "第一生命HD"),
                        ("8725", "MS&ADインシュアランス")]},
            {"label": "地方銀行",
             "logic": "利上げ→貸出金利の改善がメガバンクより大きく効く",
             "stocks": [("7186", "コンコルディアFG"), ("8331", "千葉銀行"),
                        ("8354", "ふくおかFG")]},
        ],
    },
    "インバウンド・観光": {
        "keywords": ["インバウンド", "訪日", "観光", "百貨店", "免税", "ホテル",
                     "旅行"],
        "derived": [
            {"label": "化粧品・日用品",
             "logic": "訪日客増→免税売上で化粧品・ドラッグストアが恩恵",
             "stocks": [("4911", "資生堂"), ("4452", "花王"),
                        ("3088", "マツキヨココカラ&カンパニー")]},
            {"label": "鉄道・交通",
             "logic": "観光移動の増加→新幹線・私鉄の利用増",
             "stocks": [("9022", "JR東海"), ("9020", "JR東日本"),
                        ("9041", "近鉄グループHD")]},
            {"label": "ホテル・レジャー",
             "logic": "宿泊需要逼迫→客室単価の上昇が利益に直結",
             "stocks": [("9616", "共立メンテナンス"), ("4661", "オリエンタルランド"),
                        ("6191", "エアトリ")]},
        ],
    },
    "ゲーム・エンタメ": {
        "keywords": ["ゲーム", "任天堂", "Switch", "プレイステーション", "アニメ",
                     "eスポーツ", "配信"],
        "derived": [
            {"label": "電子部品・半導体",
             "logic": "新型ゲーム機ヒット→搭載される部品・チップの出荷増",
             "stocks": [("6526", "ソシオネクスト"), ("6981", "村田製作所"),
                        ("6963", "ローム")]},
            {"label": "IP・コンテンツ",
             "logic": "ゲーム人気→アニメ化・グッズ化でIP保有企業が潤う",
             "stocks": [("9468", "KADOKAWA"), ("7832", "バンダイナムコHD"),
                        ("7552", "ハピネット")]},
        ],
    },
}

THEME_MAP_US: dict[str, dict] = {
    "AI": {
        "keywords": ["AI", "artificial intelligence", "NVIDIA", "data center",
                     "ChatGPT", "LLM", "machine learning"],
        "derived": [
            {"label": "Cooling / HVAC",
             "logic": "AI demand → data center buildout → cooling infrastructure",
             "stocks": [("VRT", "Vertiv"), ("MOD", "Modine"), ("AAON", "AAON")]},
            {"label": "Power / utilities",
             "logic": "DC power demand → grid & generation beneficiaries",
             "stocks": [("VST", "Vistra"), ("CEG", "Constellation"), ("NRG", "NRG Energy")]},
            {"label": "DC REIT",
             "logic": "Hyperscaler capex → data center real estate",
             "stocks": [("DLR", "Digital Realty"), ("EQIX", "Equinix")]},
        ],
    },
    "Semiconductor": {
        "keywords": ["semiconductor", "chip", "TSMC", "HBM", "foundry", "memory"],
        "derived": [
            {"label": "Equipment",
             "logic": "Fab expansion → lithography & etch tool demand",
             "stocks": [("AMAT", "Applied Materials"), ("LRCX", "Lam Research"),
                        ("KLAC", "KLA")]},
            {"label": "Materials",
             "logic": "Advanced nodes → specialty chemicals & materials",
             "stocks": [("ENTG", "Entegris"), ("MKSI", "MKS Instruments")]},
        ],
    },
    "Defense": {
        "keywords": ["defense", "military", "NATO", "missile", "aerospace"],
        "derived": [
            {"label": "Prime contractors",
             "logic": "Defense budget → platforms & systems orders",
             "stocks": [("LMT", "Lockheed Martin"), ("RTX", "RTX"),
                        ("NOC", "Northrop Grumman"), ("GD", "General Dynamics")]},
        ],
    },
    "Healthcare": {
        "keywords": ["pharma", "biotech", "FDA", "drug", "clinical trial"],
        "derived": [
            {"label": "Life science tools",
             "logic": "R&D boom → instruments & CDMO demand",
             "stocks": [("TMO", "Thermo Fisher"), ("DHR", "Danaher"),
                        ("WST", "West Pharmaceutical")]},
        ],
    },
}

NEWS_QUERIES = ["株式市場", "日経平均", "東証 株"]
NEWS_QUERIES_US = ["stock market", "NASDAQ", "S&P 500", "semiconductor stocks"]
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
    headline: str                   # 参考見出し(最も強くヒットした1件)
    headline_link: str = ""
    derived: list = field(default_factory=list)


# ============================================================
# ニュース取得
# ============================================================

def get_theme_map(market: str = "jp") -> dict:
    return THEME_MAP_US if market == "us" else THEME_MAP


def fetch_google_news(query: str, limit: int = 20, timeout: int = 10,
                      hl: str = "ja", gl: str = "JP", ceid: str = "JP:ja") -> list[NewsItem]:
    """Google News RSS から見出しを取得(APIキー不要)"""
    url = ("https://news.google.com/rss/search?q="
           + urllib.parse.quote(query)
           + f"&hl={hl}&gl={gl}&ceid={ceid}")
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
                    link = ""
                    cu = c.get("canonicalUrl")
                    link = cu.get("url", "") if isinstance(cu, dict) else c.get("link", "")
                    items.append(NewsItem(
                        title=title, link=link,
                        published=str(c.get("pubDate") or c.get("providerPublishTime") or ""),
                        source=f"yfinance:{t}",
                    ))
        except Exception:
            continue
    return items


def fetch_all_news(market: str = "jp") -> list[NewsItem]:
    """全ソースから取得して重複タイトルを除去"""
    items: list[NewsItem] = []
    if market == "us":
        for q in NEWS_QUERIES_US:
            items.extend(fetch_google_news(q, hl="en-US", gl="US", ceid="US:en"))
            time.sleep(0.5)
        items.extend(fetch_yfinance_news(["^GSPC", "NVDA", "AAPL"]))
    else:
        for q in NEWS_QUERIES:
            items.extend(fetch_google_news(q))
            time.sleep(0.5)
        items.extend(fetch_yfinance_news())
    seen, uniq = set(), []
    for it in items:
        key = re.sub(r"\s+", "", it.title)[:30]
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return uniq


# ============================================================
# テーマ判定 → 波及銘柄の抽出
# ============================================================

def analyze_themes(news: list[NewsItem], top_n: int = 4,
                    market: str = "jp") -> list[ThemeResult]:
    """見出し群からテーマを判定し、スコア順に上位 top_n 件を返す"""
    results: list[ThemeResult] = []
    for theme, cfg in get_theme_map(market).items():
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
                derived=cfg["derived"],
            ))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_n]


def get_ripple_candidates(themes: list[ThemeResult]) -> dict[str, dict]:
    """
    テーマ判定結果 → 【波及銘柄】候補の辞書

    返り値の例:
      { "6367": {"name": "ダイキン工業",
                 "chains": ["AI → 冷却・空調設備"],
                 "logic": "AI需要拡大→データセンター増設→サーバー冷却需要",
                 "news_score": 5,
                 "headline": "...", "headline_link": "..."}, ... }
    """
    candidates: dict[str, dict] = {}
    for tr in themes:
        for grp in tr.derived:
            chain = f"{tr.theme} → {grp['label']}"
            for code, name in grp["stocks"]:
                c = candidates.setdefault(code, {
                    "name": name, "chains": [], "logic": grp["logic"],
                    "news_score": 0,
                    "headline": tr.headline, "headline_link": tr.headline_link,
                })
                if chain not in c["chains"]:
                    c["chains"].append(chain)
                c["news_score"] += tr.score
    return candidates


# 後方互換エイリアス
get_news_candidates = get_ripple_candidates


def run(mock: bool = False, market: str = "jp"):
    """取得 → テーマ判定 → 波及銘柄抽出。アプリ側はこれを呼ぶだけでOK。"""
    if mock:
        if market == "us":
            news = [
                NewsItem("AI data center spending hits record highs"),
                NewsItem("NVIDIA beats earnings expectations"),
                NewsItem("Defense budget increase proposed in Congress"),
            ]
        else:
            news = [
                NewsItem("生成AI需要が急拡大 データセンター投資、過去最高を更新へ"),
                NewsItem("エヌビディア決算が市場予想を上回る AI関連株に資金流入"),
                NewsItem("訪日客が単月最高を更新 百貨店の免税売上が好調"),
                NewsItem("日銀、追加利上げを見送り 金利先高観は維持"),
            ]
    else:
        news = fetch_all_news(market)
    themes = analyze_themes(news, market=market)
    candidates = get_ripple_candidates(themes)
    return news, themes, candidates


# ============================================================
# アプリ連携用ラッパー
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
    """波及マップ内の全銘柄（中型株含む）"""
    universe: dict[str, str] = {}
    for cfg in get_theme_map(market).values():
        for grp in cfg.get("derived", []):
            for code, name in grp["stocks"]:
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
            "chains": info.get("chains", []),
            "themes": info.get("chains", []),
            "logic": info.get("logic", ""),
            "news_score": info.get("news_score", 0),
            "headline": headline,
            "headline_link": info.get("headline_link", ""),
            "headlines": [headline] if headline else [],
        }

    theme_list = []
    for tr in themes:
        tickers = []
        derived_labels = []
        for grp in tr.derived:
            derived_labels.append(grp["label"])
            tickers.extend(code for code, _ in grp["stocks"])
        theme_list.append({
            "id": tr.theme,
            "label": tr.theme,
            "theme": tr.theme,
            "score": tr.score,
            "headlines": [tr.headline] if tr.headline else [],
            "headline": tr.headline,
            "headline_link": tr.headline_link,
            "derived_labels": derived_labels,
            "tickers": tickers,
        })

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
    """ニュース分析結果（30分キャッシュ）JP/US 対応"""
    if not force_refresh:
        cached = _load_cache(market)
        if cached:
            return cached

    news, themes, candidates = run(mock=False, market=market)
    result = _build_context(news, themes, candidates)
    result["market"] = market
    for tr in themes:
        for grp in tr.derived:
            grp.setdefault("theme", tr.theme)
    result["theme_chains"] = [
        {
            "theme": tr.theme,
            "score": tr.score,
            "headline": tr.headline,
            "chains": [
                {
                    "label": grp["label"],
                    "logic": grp["logic"],
                    "stocks": [{"code": c, "name": n} for c, n in grp["stocks"]],
                }
                for grp in tr.derived
            ],
        }
        for tr in themes
    ]
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
    print("\n=== 波及銘柄(連想チェーン) ===")
    for code, c in sorted(candidates.items(), key=lambda x: -x[1]["news_score"]):
        print(f"  {code} {c['name']:<14} score={c['news_score']}")
        for ch in c["chains"]:
            print(f"      -> {ch}")
        print(f"      理由: {c['logic']}")
