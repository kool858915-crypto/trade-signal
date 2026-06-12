#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trade_signal.py — 互換用CLIラッパー

※ メインは server.py です。スマホのブラウザから全操作できます。
"""
import argparse
import sys

import trade_core as tc


def add_common_args(sp):
    sp.add_argument("--market", choices=tc.MARKETS.keys(), default="jp")
    sp.add_argument("--style", choices=tc.STYLES.keys(), default="day")


def cmd_scan(args):
    data = tc.action_scan(args.market, args.style, args.tickers)
    ctx = tc.Ctx(args.market, args.style)
    print(f"\n=== {ctx.m['name']} / {ctx.s['name']} スキャン {data['scanned_at']} ===\n")
    for item in data["results"]:
        if "error" in item:
            print(f"[{item['ticker']}] {item['error']}\n")
            continue
        mark = {"買い": "★", "売り": "▼", "様子見": "─"}[item["signal"]]
        print(f"[{item['ticker']}] {mark} {item['signal']}  {ctx.fmt(item['price'])} ({item['time']})")
        for c in item["conds"]:
            print(f"    {'○' if c['ok'] else '×'} {c['name']}")
        if "plan" in item:
            p = item["plan"]
            print(f"    → 損切り {ctx.fmt(p['stop'])} / 利確 {ctx.fmt(p['target'])} / 推奨{p['qty']}")
        print()


def cmd_buy(args):
    tc.action_buy(args.market, args.style, args.ticker, args.price, args.qty,
                  short=args.short, stop=args.stop, target=args.target)
    ctx = tc.Ctx(args.market, args.style)
    print(f"建玉を記録: {args.ticker} @ {ctx.fmt(args.price)}")


def cmd_positions(args):
    data = tc.action_positions(args.market, args.style)
    ctx = tc.Ctx(args.market, args.style)
    if not data["positions"]:
        print("建玉はありません。")
        return
    for p in data["positions"]:
        line = f"[{p['ticker']}] {p['side']} {p['qty']} @ {ctx.fmt(p['entry_price'])}"
        if "current_price" in p:
            line += f"  現値 {ctx.fmt(p['current_price'])}  {ctx.fmt_pnl(p['pnl'])}"
        print(line)


def cmd_sell(args):
    r = tc.action_sell(args.market, args.style, args.ticker, args.price)
    if not r["ok"]:
        print(r["error"])
        return
    ctx = tc.Ctx(args.market, args.style)
    print(f"決済: {args.ticker}  損益 {ctx.fmt_pnl(r['pnl'])}")


def cmd_review(args):
    data = tc.action_review(args.market, args.style)
    ctx = tc.Ctx(args.market, args.style)
    if not data["count"]:
        print(data["message"])
        return
    print(f"合計損益: {ctx.fmt_pnl(data['total_pnl'])}  勝率: {data['win_rate']:.1f}%")


def main():
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    p = argparse.ArgumentParser(description="売買シグナル (CLI互換)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scan")
    sp.add_argument("-t", "--tickers", nargs="+")
    add_common_args(sp)
    sp.set_defaults(func=cmd_scan)

    bp = sub.add_parser("buy")
    bp.add_argument("ticker")
    bp.add_argument("--price", type=float, required=True)
    bp.add_argument("--qty", type=int, required=True)
    bp.add_argument("--stop", type=float)
    bp.add_argument("--target", type=float)
    bp.add_argument("--short", action="store_true")
    add_common_args(bp)
    bp.set_defaults(func=cmd_buy)

    pp = sub.add_parser("positions")
    add_common_args(pp)
    pp.set_defaults(func=cmd_positions)

    lp = sub.add_parser("sell")
    lp.add_argument("ticker")
    lp.add_argument("--price", type=float, required=True)
    add_common_args(lp)
    lp.set_defaults(func=cmd_sell)

    rp = sub.add_parser("review")
    add_common_args(rp)
    rp.set_defaults(func=cmd_review)

    srv = sub.add_parser("serve", help="ローカルサーバー起動 (本番はクラウド推奨)")
    srv.set_defaults(func=lambda a: __import__("server").run())

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
