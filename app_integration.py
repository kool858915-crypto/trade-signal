# -*- coding: utf-8 -*-
"""
app_integration.py — 既存アプリへの組み込み(画面表示)

http.server (server.py) 向け:
    from app_integration import api_recommendations
    GET /api/recommendations → api_recommendations(refresh=...)

FastAPI アプリ向け(任意):
    from app_integration import router as news_router
    app.include_router(news_router)

ホーム画面のHTMLには HOME_NEWS_HTML スニペットを参考に埋め込む。
「おすすめを更新」→ GET /api/recommendations
「取引を開始」→ 表示中の codes を既存デモ取引スキャンへ渡す
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from recommend_news import get_recommendations

# 直近の結果をキャッシュ(ニュース取得は1回数秒かかるため)
_cache: dict = {"data": None, "ts": 0}
CACHE_SEC = 600  # 10分


def api_recommendations(refresh: bool = False, style: str = "day") -> dict:
    """「おすすめを更新」ボタンから呼ぶ。refresh=True で強制再取得。"""
    now = time.time()
    if refresh or _cache["data"] is None or now - _cache["ts"] > CACHE_SEC:
        _cache["data"] = get_recommendations()
        _cache["ts"] = now

    data = dict(_cache["data"])
    codes = [r["code"] for r in data.get("recommendations", [])]
    data["codes"] = codes

    # デモスキャン用に銘柄リストを保存
    try:
        import trade_core as tc
        tc._set_setting(f"recommended_jp_{style}", json.dumps(codes))
        tc._set_setting(
            f"recommended_jp_{style}_at",
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
    except ImportError:
        pass

    data["ok"] = True
    data["cached"] = not refresh and (now - _cache["ts"] < CACHE_SEC)
    return data


def get_cached_codes() -> list[str]:
    """直近キャッシュの銘柄コード一覧"""
    if _cache["data"]:
        return [r["code"] for r in _cache["data"].get("recommendations", [])]
    return []


# FastAPI があればルーターを提供(なければスキップ)
try:
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/api/recommendations")
    def fastapi_recommendations(refresh: bool = False):
        return api_recommendations(refresh=refresh)

except ImportError:
    router = None


# ============================================================
# ホーム画面に埋め込むHTML+JS(テーマチップ / バッジ / 理由表示)
# ※ static/index.html に同等のUIを組み込み済み
# ============================================================
HOME_NEWS_HTML = """
<style>
.theme-chips{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}
.chip{background:#e8f0fe;color:#1a56db;border-radius:999px;
      padding:4px 14px;font-size:13px;font-weight:600}
.headline{font-size:13px;color:#555;margin:4px 0 12px}
.stock-card{border:1px solid #ddd;border-radius:10px;padding:12px;margin:8px 0}
.stock-head{display:flex;align-items:center;gap:8px}
.stock-name{font-weight:700;font-size:15px}
.badge{font-size:11px;border-radius:4px;padding:2px 8px;font-weight:600}
.badge-news{background:#fde8e8;color:#c81e1e}
.badge-theme{background:#fdf6b2;color:#8e4b10}
.score{margin-left:auto;font-weight:700;color:#1a56db}
.reason{font-size:12px;color:#666;margin-top:6px}
</style>

<h3>本日のニューステーマ</h3>
<div class="theme-chips" id="themeChips"></div>
<div class="headline" id="newsHeadline"></div>

<h3>おすすめ銘柄</h3>
<div id="stockList"></div>
<button onclick="updateRecommendations(true)">おすすめを更新</button>
<button onclick="startTrading()">取引を開始</button>

<script>
let currentCodes = [];

async function updateRecommendations(force=false){
  const res = await fetch('/api/recommendations' + (force ? '?refresh=true' : ''), {
    headers: {'X-Pin': localStorage.getItem('trade_pin') || ''}
  });
  const data = await res.json();

  document.getElementById('themeChips').innerHTML =
    data.themes.map(t => `<span class="chip">${t.theme}</span>`).join('');

  if (data.themes.length){
    document.getElementById('newsHeadline').textContent =
      '参考見出し: ' + data.themes[0].headline;
  }

  currentCodes = data.recommendations.map(r => r.code);
  document.getElementById('stockList').innerHTML =
    data.recommendations.map(r => `
      <div class="stock-card">
        <div class="stock-head">
          <span class="stock-name">${r.code} ${r.name}</span>
          ${r.badges.map(b => `<span class="badge ${
              b==='ニュース関連' ? 'badge-news' : 'badge-theme'
            }">${b}</span>`).join('')}
          <span class="score">${r.total_score}</span>
        </div>
        <div class="reason">${r.reason}</div>
      </div>`).join('');
}

async function startTrading(){
  await fetch('/api/start?market=jp&style=day', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Pin': localStorage.getItem('trade_pin') || ''
    },
    body: JSON.stringify({codes: currentCodes})
  });
}

updateRecommendations();
</script>
"""
