# IMPLEMENTATION_PLAN.md — デモ取引アプリ 機能拡張 実装計画書

> このファイルはClaude Code用の作業指示書。リポジトリ直下に置き、
> 「IMPLEMENTATION_PLAN.md の Phase 1 を実装して」のように指示して使う。
> 各タスクは必ず【前提調査】→【実装】→【受け入れ条件の確認】の順で進めること。

---

## 全体ルール(全Phaseに適用)

1. **既存のデモ取引ループを壊さない。** 変更前に必ず該当モジュールを読み、
   既存のテーブル・関数のシグネチャを確認してから差分で実装する。
2. **DBスキーマ変更は追加のみ**(ALTER TABLE ADD COLUMN / CREATE TABLE IF NOT EXISTS)。
   既存カラムの削除・リネームは禁止。マイグレーションは起動時に自動実行される形にする。
3. 各Phase完了時に `python -m pytest`(無ければ手動テストコマンド)と
   構文チェックを通し、**動作確認してから次のPhaseに進む**。
4. ntfy通知のタグ・優先度は Phase 4 で定義する規約に全体で従う。
5. Render無料枠を意識し、ポーリング間隔・外部API呼び出し回数を増やす変更は
   必ず市場時間制御(Phase 2)の内側に置く。
6. UIはPWAの既存デザイン(色・部品)を踏襲。新規タブは既存タブの実装をコピーして改修。

---

## Phase 1: シグナル履歴タブ 【最優先】 ✅ 実装済み

### 前提調査
- `signal_logs` テーブルのスキーマを確認(カラム名・型・既存データ)
- シグナル生成箇所を grep(`signal_logs` への INSERT を全て列挙)
- 条件内訳(RSI・出来高・クロス種別など)が現状どこまで保存されているか確認

### 実装
1. `conditions_json TEXT` / `demo_position_id INTEGER` カラムを追加
2. API: `GET /api/signal_history?limit=50&market=jp`
3. PWAに「履歴」タブを追加

### 受け入れ条件
- [x] 過去のシグナルが新しい順に表示される
- [x] 各シグナルの「なぜ通知が来たか」(RSI値・出来高倍率など)が見える
- [x] デモエントリーに繋がったシグナルが区別できる

---

## Phase 2: 市場時間の自動制御 ✅ 実装済み

### 実装
1. `market_hours.py`（東証/NYSE、祝日、phase 判定）
2. `action_monitor_all` / 監視ループで閉場中スキップ・15分スリープ
3. 寄付直後・大引け前のシグナル通知 priority 4

### 受け入れ条件
- [x] 土日・祝日・昼休みに自動スキャンが走らない
- [x] 開場中は通常間隔で再開
- [x] 閉場中は monitor_sleep_sec=900

---

## Phase 3: デモ vs 手動の成績分離 ✅ 実装済み

### 実装
1. `GET /api/performance?mode=demo|manual|all`
2. 成績タブ「比較 | デモ | 手動」切替
3. シグナル追随率（followed_signal ベース）

### 受け入れ条件
- [x] デモと手動の勝率・損益・PFが別表示
- [x] 比較ビューで横並び表示

---

## Phase 4: 通知の種類分け ✅ 実装済み

### 実装
1. `notifier.py` に種別・tags・priority 定義
2. 全送信箇所を kind 指定に置換
3. `demo_notify_enabled` / `alerts_only` 設定キー（Phase 8 で UI 化）

### 受け入れ条件
- [x] [本番]/[デモ]/[注意] プレフィックスで区別
- [x] デモ通知OFF・注意のみモードの分岐あり

---

## Phase 5〜12

（未実装。Phase 8 設定タブで demo_notify / alerts_only を UI 化予定）

## 実装順序

```
Phase 1(履歴) → Phase 3(成績分離) → Phase 2(市場時間)
  → Phase 4(通知規約) → Phase 8(設定)
  → Phase 5(lag統一) → Phase 7(ウォッチ連携) → Phase 6(ニュース画面)
  → Phase 9(条件別勝率) → Phase 10(週報) → Phase 11(バックテスト) → Phase 12(US)
```
