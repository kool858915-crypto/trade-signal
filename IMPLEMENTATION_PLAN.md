# IMPLEMENTATION_PLAN.md — デモ取引アプリ 機能拡張 実装計画書

> 各 Phase は【前提調査】→【実装】→【受け入れ条件の確認】の順で進める。

## 全体ルール

1. 既存デモ取引ループを壊さない
2. DBスキーマ変更は追加のみ
3. 構文チェック・手動テスト後に次 Phase へ
4. ntfy は `notifier.py` 規約に従う
5. ポーリング増加は `market_hours.py` 内側
6. UIは既存PWAデザインを踏襲

---

## Phase 1: シグナル履歴タブ ✅

- `conditions_json` / `demo_position_id`
- `GET /api/signal_history`
- PWA「履歴」タブ

## Phase 2: 市場時間の自動制御 ✅

- `market_hours.py`
- 閉場中スキャン抑制・15分スリープ
- 寄付/大引け前 priority 向上

## Phase 3: デモ vs 手動の成績分離 ✅

- `GET /api/performance?mode=demo|manual|all`
- 成績タブ 比較|デモ|手動
- シグナル追随率

## Phase 4: 通知の種類分け ✅

- `notifier.py`（[本番]/[デモ]/[注意]/[週報]）
- `demo_notify_enabled` / `alerts_only`

## Phase 5: 出遅れコストと実シグナル統一 ✅

- `signal_detect.py` 共通検出
- `EMA9/21` を signal_lag に追加
- おすすめカードに `avg_lag_pct`

## Phase 6: ニュース画面の強化 ✅

- PWA「ニュース」タブ
- テーマ→波及チェーン図（CSS）
- ホームテーマチップ→ニュース遷移

## Phase 7: おすすめ→ウォッチリスト自動連携 ✅

- `watchlist` テーブル
- `sync_reco_watchlist()` on おすすめ更新
- 手動銘柄は削除しない

## Phase 8: 設定画面 ✅

- `GET/POST /api/settings`
- PWA「設定」タブ（通知・連携・週報）
- ウォッチリスト手動追加

## Phase 9: 条件別勝率ダッシュボード ✅

- `GET /api/condition_stats`
- 成績タブ内「条件別分析」
- `score_history` テーブル

## Phase 10: 週次レポート通知 ✅

- `weekly_report.py`
- 日曜18:00 JST（監視ループ内）
- 設定でON/OFF

## Phase 11: バックテスト簡易版 ✅

- `backtest.py`
- `GET /api/backtest?code=&rule=EMA9/21&period=1mo`
- おすすめカードに直近成績表示

## Phase 12: 米国株への波及銘柄拡張 ✅

- `THEME_MAP_US` in `trade_news.py`
- `/api/news` US対応
- 英語RSS + yfinance US

---

## 実装順序（完了）

```
Phase 1 → 3 → 2 → 4 → 8 → 5 → 7 → 6 → 9 → 10 → 11 → 12  ✅ 全完了
```
