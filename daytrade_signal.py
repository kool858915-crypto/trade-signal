#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daytrade_signal.py — 互換用ラッパー

※ メインは server.py です。クラウドにデプロイしてスマホのみで操作してください。
  python server.py  (ローカルテスト用)

このファイルは従来コマンドとの互換のために残しています。
"""
import sys

import trade_core as tc


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n推奨: python server.py をクラウドにデプロイ → スマホのブラウザで開く")
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd in ("serve", "start-server"):
        from server import run
        port = 8765
        for i, a in enumerate(args):
            if a == "--port" and i + 1 < len(args):
                port = int(args[i + 1])
        run(port=port)
        return

    if cmd in ("start", "取引を開始します"):
        print(tc.action_start()["message"])
    elif cmd in ("end", "終わります"):
        print(tc.action_end()["message"])
    elif cmd == "notify-test":
        r = tc.action_notify_test()
        print(r["message"])
    elif cmd == "scan":
        tickers = None
        if "-t" in args:
            tickers = args[args.index("-t") + 1:]
        data = tc.action_scan("jp", "day", tickers)
        for item in data["results"]:
            print(item)
    else:
        print(f"不明なコマンド: {cmd}")
        print("メイン操作は server.py (スマホWeb) をご利用ください。")


if __name__ == "__main__":
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    main()
