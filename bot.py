from __future__ import annotations

import os
import signal
import sqlite3
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from dotenv import load_dotenv
from polymarket import PublicClient

load_dotenv()


def env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


STARTING_CASH = env_float("PAPER_STARTING_CASH", 1000.0)
SCAN_SECONDS = env_int("SCAN_SECONDS", 15)
MAX_MARKETS = env_int("MAX_MARKETS", 25)
MIN_LIQUIDITY = env_float("MIN_LIQUIDITY", 10000.0)
MAX_POSITION_PCT = env_float("MAX_POSITION_PCT", 0.05)
MAX_DAILY_LOSS_PCT = env_float("MAX_DAILY_LOSS_PCT", 0.03)
MOMENTUM_WINDOW = env_int("MOMENTUM_WINDOW", 8)
ENTRY_MOVE = env_float("ENTRY_MOVE", 0.018)
EXIT_MOVE = env_float("EXIT_MOVE", 0.010)
SLIPPAGE_BPS = env_float("PAPER_SLIPPAGE_BPS", 15.0)
DB_PATH = os.getenv("DB_PATH", "paper_trading.sqlite3")

RUNNING = True


def stop(*_):
    global RUNNING
    RUNNING = False


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)


@dataclass
class Position:
    token_id: str
    market_id: str
    question: str
    shares: float
    entry_price: float
    last_price: float


class Ledger:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots(
              ts TEXT, market_id TEXT, token_id TEXT, question TEXT,
              midpoint REAL, spread REAL, last_price REAL
            );
            CREATE TABLE IF NOT EXISTS trades(
              ts TEXT, market_id TEXT, token_id TEXT, question TEXT,
              side TEXT, price REAL, shares REAL, notional REAL,
              realized_pnl REAL, reason TEXT
            );
            CREATE TABLE IF NOT EXISTS equity(
              ts TEXT, cash REAL, market_value REAL, equity REAL
            );
            """
        )
        self.db.commit()

    def snapshot(self, *row):
        self.db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)", row)
        self.db.commit()

    def trade(self, *row):
        self.db.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        self.db.commit()

    def equity(self, *row):
        self.db.execute("INSERT INTO equity VALUES (?,?,?,?)", row)
        self.db.commit()


class PaperBroker:
    def __init__(self, ledger: Ledger):
        self.cash = STARTING_CASH
        self.positions: dict[str, Position] = {}
        self.ledger = ledger
        self.day_start_equity = STARTING_CASH
        self.day_key = datetime.now(timezone.utc).date()

    def equity(self) -> float:
        return self.cash + sum(p.shares * p.last_price for p in self.positions.values())

    def daily_loss_hit(self) -> bool:
        today = datetime.now(timezone.utc).date()
        if today != self.day_key:
            self.day_key = today
            self.day_start_equity = self.equity()
        return self.equity() <= self.day_start_equity * (1 - MAX_DAILY_LOSS_PCT)

    def mark(self, token_id: str, price: float):
        if token_id in self.positions:
            self.positions[token_id].last_price = price

    def buy(self, market_id: str, token_id: str, question: str, price: float, reason: str):
        if token_id in self.positions or self.daily_loss_hit():
            return
        eq = self.equity()
        budget = min(self.cash, eq * MAX_POSITION_PCT)
        if budget < 2:
            return
        fill = min(0.999, price * (1 + SLIPPAGE_BPS / 10000))
        shares = budget / fill
        self.cash -= budget
        self.positions[token_id] = Position(token_id, market_id, question, shares, fill, fill)
        ts = now()
        self.ledger.trade(ts, market_id, token_id, question, "BUY", fill, shares, budget, 0.0, reason)
        print(f"BUY  {question[:70]} | {shares:.2f} @ {fill:.4f} | ${budget:.2f}")

    def sell(self, token_id: str, price: float, reason: str):
        p = self.positions.get(token_id)
        if not p:
            return
        fill = max(0.001, price * (1 - SLIPPAGE_BPS / 10000))
        proceeds = p.shares * fill
        cost = p.shares * p.entry_price
        pnl = proceeds - cost
        self.cash += proceeds
        self.ledger.trade(now(), p.market_id, token_id, p.question, "SELL", fill, p.shares, proceeds, pnl, reason)
        print(f"SELL {p.question[:70]} | P&L ${pnl:+.2f}")
        del self.positions[token_id]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_float(v, default=0.0) -> float:
    if v is None:
        return default
    if isinstance(v, Decimal):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def market_liquidity(m) -> float:
    for attr in ("liquidity_num", "liquidity", "volume_num"):
        value = getattr(m, attr, None)
        x = as_float(value, -1)
        if x >= 0:
            return x
    return 0.0


def main():
    ledger = Ledger(DB_PATH)
    broker = PaperBroker(ledger)
    history: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=MOMENTUM_WINDOW))

    print("Polymarket Bot v1 — PAPER MODE ONLY")
    print(f"Starting equity: ${STARTING_CASH:.2f} | max position {MAX_POSITION_PCT:.1%} | daily stop {MAX_DAILY_LOSS_PCT:.1%}")

    with PublicClient() as client:
        while RUNNING:
            cycle_started = time.time()
            try:
                page = client.list_markets(closed=False, page_size=max(MAX_MARKETS * 2, 30)).first_page()
                markets = sorted(page.items, key=market_liquidity, reverse=True)
                markets = [m for m in markets if market_liquidity(m) >= MIN_LIQUIDITY][:MAX_MARKETS]

                for m in markets:
                    try:
                        token_id = m.outcomes.yes.token_id
                        if token_id is None:
                            continue
                        midpoint = as_float(client.get_midpoint(token_id=token_id), -1)
                        if not (0.01 < midpoint < 0.99):
                            continue
                        spread = as_float(client.get_spread(token_id=token_id), 0)
                        last = client.get_last_trade_price(token_id=token_id)
                        last_price = as_float(getattr(last, "price", None), midpoint)
                        question = m.question or m.slug or str(m.id)
                        market_id = str(m.id)

                        ledger.snapshot(now(), market_id, token_id, question, midpoint, spread, last_price)
                        broker.mark(token_id, midpoint)
                        h = history[token_id]
                        h.append(midpoint)
                        if len(h) < MOMENTUM_WINDOW:
                            continue

                        move = h[-1] - h[0]
                        # Strategy v1: only act when the move is large enough and spread is not excessive.
                        if token_id not in broker.positions:
                            if move >= ENTRY_MOVE and spread <= 0.04 and midpoint <= 0.90:
                                broker.buy(market_id, token_id, question, midpoint, f"momentum {move:+.4f}, spread {spread:.4f}")
                        else:
                            p = broker.positions[token_id]
                            ret = midpoint / p.entry_price - 1
                            # Exit on momentum reversal, take-profit, or stop-loss.
                            if move <= -EXIT_MOVE:
                                broker.sell(token_id, midpoint, f"momentum reversal {move:+.4f}")
                            elif ret >= 0.06:
                                broker.sell(token_id, midpoint, f"take profit {ret:+.2%}")
                            elif ret <= -0.035:
                                broker.sell(token_id, midpoint, f"stop loss {ret:+.2%}")
                    except Exception as e:
                        print(f"market error: {type(e).__name__}: {e}")

                mv = sum(p.shares * p.last_price for p in broker.positions.values())
                eq = broker.cash + mv
                ledger.equity(now(), broker.cash, mv, eq)
                print(f"equity=${eq:.2f} cash=${broker.cash:.2f} positions={len(broker.positions)}")

                if broker.daily_loss_hit():
                    print("KILL SWITCH: daily loss limit reached. No new entries until UTC day changes.")
            except Exception as e:
                print(f"cycle error: {type(e).__name__}: {e}")

            sleep_for = max(1, SCAN_SECONDS - (time.time() - cycle_started))
            time.sleep(sleep_for)

    print("Stopped cleanly.")


if __name__ == "__main__":
    main()
