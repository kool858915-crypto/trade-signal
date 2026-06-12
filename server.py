#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
クラウド常時稼働サーバー — スマホのみで全操作

起動:
  python server.py

環境変数:
  PORT          ポート (クラウドは自動設定)
  WEB_PIN       ログインPIN
  NTFY_TOPIC    ntfyトピック名
  DATA_DIR      データ保存先
  MONITOR_INTERVAL  損切り自動監視間隔(秒) デフォルト300
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import trade_core as tc

STATIC_DIR = Path(__file__).parent / "static"
_monitor_stop = threading.Event()


def _get_pin() -> str:
    return tc.CONFIG["web_pin"]


def _check_pin(handler: BaseHTTPRequestHandler) -> bool:
    return handler.headers.get("X-Pin") == _get_pin()


def _json_response(handler: BaseHTTPRequestHandler, data: dict, status: int = 200):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if not length:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def _qs(handler: BaseHTTPRequestHandler) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(handler.path).query).items()}


def _market_style(qs: dict) -> tuple[str, str]:
    market = qs.get("market", "jp")
    style = qs.get("style", "day")
    if market not in tc.MARKETS or style not in tc.STYLES:
        raise ValueError("invalid market or style")
    return market, style


class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _serve_static(self, rel_path: str, content_type: str):
        path = STATIC_DIR / rel_path
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path in ("/", "/index.html"):
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/manifest.json":
            self._serve_static("manifest.json", "application/json")
            return
        if path == "/api/health":
            _json_response(self, {"ok": True, "service": "trade-signal"})
            return
        if path == "/api/monitor":
            _json_response(self, tc.action_monitor_all())
            return

        if not _check_pin(self):
            _json_response(self, {"error": "PINが正しくありません"}, 401)
            return

        qs = _qs(self)
        try:
            market, style = _market_style(qs)
        except ValueError:
            _json_response(self, {"error": "invalid market/style"}, 400)
            return

        if path == "/api/status":
            _json_response(self, tc.action_status(market, style))
        elif path == "/api/scan":
            tickers = qs.get("tickers")
            ticker_list = tickers.split(",") if tickers else None
            _json_response(self, tc.action_scan(market, style, ticker_list))
        elif path == "/api/positions":
            data = tc.action_positions(market, style)
            data["currency"] = tc.MARKETS[market]["currency"]
            _json_response(self, data)
        elif path == "/api/review":
            _json_response(self, tc.action_review(market, style))
        elif path == "/api/validate":
            _json_response(self, tc.action_validate(market, style))
        elif path == "/api/journal":
            _json_response(self, tc.action_journal(market, style))
        else:
            _json_response(self, {"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/monitor":
            _json_response(self, tc.action_monitor_all())
            return

        if not _check_pin(self):
            _json_response(self, {"error": "PINが正しくありません"}, 401)
            return

        qs = _qs(self)
        data = _read_json(self)

        try:
            market, style = _market_style({**qs, **data})
        except ValueError:
            _json_response(self, {"error": "invalid market/style"}, 400)
            return

        if path == "/api/start":
            _json_response(self, tc.action_start(market, style))
        elif path == "/api/end":
            _json_response(self, tc.action_end(market, style))
        elif path == "/api/notify-test":
            _json_response(self, tc.action_notify_test())
        elif path == "/api/buy":
            _json_response(self, tc.action_buy(
                market, style,
                data.get("ticker", ""),
                float(data.get("price", 0)),
                int(data.get("qty", 0)),
                short=bool(data.get("short")),
            ))
        elif path == "/api/sell":
            result = tc.action_sell(
                market, style,
                data.get("ticker", ""),
                float(data.get("price", 0)),
            )
            if result.get("ok"):
                result["currency"] = tc.MARKETS[market]["currency"]
            status = 200 if result.get("ok") else 400
            _json_response(self, result, status)
        else:
            _json_response(self, {"error": "not found"}, 404)


def _monitor_loop():
    interval = tc.CONFIG["monitor_interval_sec"]
    while not _monitor_stop.is_set():
        try:
            if tc.notification_enabled():
                tc.action_monitor_all()
        except Exception:
            pass
        _monitor_stop.wait(interval)


def run(host: str = "0.0.0.0", port: int | None = None):
    port = port or int(os.environ.get("PORT", "8765"))
    monitor = threading.Thread(target=_monitor_loop, daemon=True)
    monitor.start()

    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"\n=== トレードシグナル サーバー起動 ===")
    print(f"  ポート: {port}")
    print(f"  PIN: {_get_pin()}")
    print(f"  ntfy: {tc.CONFIG['ntfy_topic']}")
    print(f"  損切り自動監視: {tc.CONFIG['monitor_interval_sec']}秒ごと")
    print(f"  クラウドにデプロイ後、スマホからURLを開くだけで使えます\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _monitor_stop.set()
        print("\n停止しました。")


if __name__ == "__main__":
    run()
