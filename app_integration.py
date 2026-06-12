# -*- coding: utf-8 -*-
"""
app_integration.py — 既存アプリへの組み込み(画面表示)

既存のFastAPIアプリに2行追加するだけ:

    from app_integration import router as news_router
    app.include_router(news_router)

http.server (server.py) 向け:
    GET  /api/recommendations, /api/lag_report
    POST /api/scan_signals

ホーム画面のHTMLには HOME_NEWS_HTML スニペットを埋め込む。
「おすすめを更新」ボタン → GET /api/recommendations を叩いて描画。
「取引を開始」ボタン → 表示中の codes を既存のデモ取引スキャン処理へ渡す。
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from recommend_news import get_recommendations
from signal_lag import record_live_signal, update_pending, get_lag_report

# 直近の結果をキャッシュ(ニュース取得は1回数秒かかるため)
_cache: dict = {"data": None, "ts": 0}
CACHE_SEC = 600  # 10分


def _load_recommendations(refresh: bool = False) -> dict:
    now = time.time()
    if refresh or _cache["data"] is None or now - _cache["ts"] > CACHE_SEC:
        _cache["data"] = get_recommendations()
        _cache["ts"] = now
    return _cache["data"]


def _enrich_recommendations(data: dict, market: str = "jp") -> dict:
    """出遅れコスト・簡易バックテストをおすすめカードに付与"""
    try:
        from backtest import avg_lag_pct, run_backtest
        for r in data.get("recommendations", []):
            code = r.get("code") or r.get("ticker")
            if not code:
                continue
            lag = avg_lag_pct(code, "EMA9/21", market=market)
            if lag is not None:
                r["avg_lag_pct"] = lag
                r["lag_warning"] = lag >= 5.0
            bt = run_backtest(code, "EMA9/21", "1mo", market=market)
            if bt.get("ok") and bt.get("count"):
                r["backtest"] = {
                    "wins": bt["wins"], "losses": bt["losses"],
                    "win_rate": bt["win_rate"],
                }
    except Exception:
        pass
    return data


def api_recommendations(refresh: bool = False, style: str = "day") -> dict:
    """http.server 向け: おすすめ銘柄 + デモスキャン用コード保存"""
    data = dict(_load_recommendations(refresh))
    data = _enrich_recommendations(data, market="jp")
    codes = [r["code"] for r in data.get("recommendations", [])]
    names = {r["code"]: r.get("name", r["code"]) for r in data.get("recommendations", [])}
    data["codes"] = codes

    try:
        import trade_core as tc
        tc._set_setting(f"recommended_jp_{style}", json.dumps(codes))
        tc._set_setting(
            f"recommended_jp_{style}_at",
            datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        tc.sync_reco_watchlist("jp", codes, names)
    except ImportError:
        pass

    data["ok"] = True
    data["cached"] = not refresh
    return data


def api_lag_report() -> dict:
    """未確定サインの答え合わせ後、集計を返す"""
    update_pending()
    report = get_lag_report()
    report["ok"] = True
    return report


def api_scan_signals(codes: list[str]) -> dict:
    """
    おすすめ銘柄をスキャンし、今日ゴールデンクロスが出ていれば記録。
    SMA5/25 と SMA50/200 の両方をチェック。
    """
    hits = []
    for code in codes:
        code = str(code).strip()
        if not code:
            continue
        for rule in ("EMA9/21", "SMA5/25", "SMA50/200"):
            r = record_live_signal(code, rule=rule)
            if r:
                hits.append(r)
    return {
        "ok": True,
        "signals": hits,
        "message": f"{len(hits)}件のサインを記録しました",
    }


try:
    from fastapi import APIRouter, Body

    router = APIRouter()

    @router.get("/api/recommendations")
    def fastapi_recommendations(refresh: bool = False):
        """「おすすめを更新」ボタンから呼ぶ。?refresh=true で強制再取得。"""
        return _load_recommendations(refresh)

    @router.get("/api/lag_report")
    def fastapi_lag_report():
        """「サイン成績」画面から呼ぶ。"""
        return api_lag_report()

    @router.post("/api/scan_signals")
    def fastapi_scan_signals(codes: list[str] = Body(...)):
        """「取引を開始」から呼ぶ。出遅れコストつきでサイン記録。"""
        return api_scan_signals(codes)

except ImportError:
    router = None


# ============================================================
# ホーム画面に埋め込むHTML+JS(テーマチップ / バッジ / 理由表示)
# ※ static/index.html に同等UIを組み込み済み
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
.chain{font-size:12px;color:#1a56db;font-weight:600;margin-top:6px}
.reason{font-size:12px;color:#666;margin-top:4px}
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
  const res = await fetch('/api/recommendations' + (force ? '?refresh=true' : ''));
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
              b==='波及銘柄' ? 'badge-news' : 'badge-theme'
            }">${b}</span>`).join('')}
          <span class="score">${r.total_score}</span>
        </div>
        ${r.chains && r.chains.length
            ? `<div class="chain">${r.chains.join(' / ')}</div>` : ''}
        <div class="reason">${r.reason}</div>
      </div>`).join('');
}

async function startTrading(){
  const res = await fetch('/api/scan_signals', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(currentCodes)});
  const data = await res.json();
  alert(data.message);
  if (typeof loadLagReport === 'function') loadLagReport();
}

updateRecommendations();
</script>
"""


# ============================================================
# 出遅れコスト計測の組み込み
# ※ static/index.html の成績タブに同等UIを組み込み済み
# ============================================================
LAG_REPORT_HTML = """
<style>
.lag-table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
.lag-table th,.lag-table td{border-bottom:1px solid #eee;padding:6px 8px;text-align:right}
.lag-table th:first-child,.lag-table td:first-child{text-align:left}
.lag-note{font-size:12px;color:#888}
.pos{color:#0a7a3d;font-weight:600}.neg{color:#c81e1e;font-weight:600}
</style>

<h3>サイン成績(出遅れコスト×勝率)</h3>
<table class="lag-table" id="lagTable">
  <tr><th>ルール</th><th>確定</th><th>平均出遅れ</th><th>勝率(20日)</th><th>平均リターン</th></tr>
</table>
<div class="lag-note">
  出遅れコスト = 直近安値からサイン発生日までに既に上がっていた率。<br>
  「早いルールは出遅れが小さいが勝率が低い」という交換条件を実測する表です。
</div>

<script>
async function loadLagReport(){
  const res = await fetch('/api/lag_report');
  const data = await res.json();
  const fmt = (v, suffix='%', sign=false) => v === null ? '–'
    : `<span class="${sign ? (v>=0?'pos':'neg') : ''}">${sign && v>=0?'+':''}${v}${suffix}</span>`;
  document.getElementById('lagTable').innerHTML =
    '<tr><th>ルール</th><th>確定</th><th>平均出遅れ</th><th>勝率(20日)</th><th>平均リターン</th></tr>' +
    data.rules.map(r => `<tr>
      <td>${r.rule}</td>
      <td>${r.settled}件${r.pending ? `(+${r.pending}待ち)` : ''}</td>
      <td>${fmt(r.avg_lag)}</td>
      <td>${fmt(r.win_rate)}</td>
      <td>${fmt(r.avg_fwd20, '%', true)}</td>
    </tr>`).join('');
}
loadLagReport();
</script>
"""
