"""Markdown summary of the paper account (state/paper_account.json)."""
import datetime as dt
import json
import sys


def main(path: str = "state/paper_account.json") -> int:
    try:
        with open(path) as f:
            s = json.load(f)
    except FileNotFoundError:
        print("No paper account yet.")
        return 0
    marks = s.get("marks", {})
    eq = s["balance"] + sum((marks.get(sym, p["entry"]) - p["entry"]) * p["qty"] * p["side"]
                            for sym, p in s["positions"].items())
    print(f"## Paper account\n\nBalance {s['balance']:.2f}, equity {eq:.2f}\n")
    if s["positions"]:
        print("| Symbol | Side | Qty | Entry | Mark | Stop | PnL |\n|---|---|---|---|---|---|---|")
        for sym, p in s["positions"].items():
            m = marks.get(sym, p["entry"])
            print(f"| {sym} | {'long' if p['side'] == 1 else 'short'} | {p['qty']:g} | {p['entry']:g} | {m:g} | "
                  f"{'' if p.get('stop') is None else format(p['stop'], 'g')} | {(m - p['entry']) * p['qty'] * p['side']:.2f} |")
    closes = [h for h in s.get("history", []) if h.get("action") == "close"]
    if closes:
        print("\n### Last closed trades\n\n| Time (UTC) | Symbol | Price | PnL | Reason |\n|---|---|---|---|---|")
        for h in closes[-15:]:
            t = dt.datetime.fromtimestamp(h["t"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"| {t} | {h['symbol']} | {h['price']:g} | {h.get('pnl', 0):.2f} | {h.get('reason', '')} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
