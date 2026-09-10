"""Public-data-only paper simulator. No wallet, secrets, signed requests or orders."""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import math
from pathlib import Path
import random
import re
import signal
import sqlite3
import time
import uuid
import httpx

LOG = logging.getLogger("paper_v13")
STOP = False

def utc():
    return datetime.now(timezone.utc)

def number(value, default=None):
    try:
        value = float(value)
        return value if math.isfinite(value) else default
    except (ValueError, TypeError):
        return default

def array(value):
    return json.loads(value) if isinstance(value, str) else value

def date(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone_missing")
    return result.astimezone(timezone.utc)

@dataclass(frozen=True)
class Config:
    starting_cash: float = 1000.0
    min_hours: float = 6.0
    max_days: float = 14.0
    exit_hours: float = 2.0
    min_liquidity: float = 10000.0
    min_volume: float = 1000.0
    max_spread: float = 0.02
    max_relative_spread: float = 0.035
    hour_move: float = 0.003
    day_move: float = 0.01
    position_pct: float = 0.02
    max_positions: int = 2
    max_exposure: float = 0.04
    daily_loss: float = 0.02
    stop_loss: float = 0.06
    take_profit: float = 0.12
    slippage_bps: float = 15.0
    fee_bps: float = 200.0  # Scenario assumption, not an exact exchange fee schedule.
    interval: float = 15.0
    pages: int = 3
    candidates: int = 15
    book_max_age: float = 120.0
    cooldown: float = 3600.0

class PublicData:
    HOSTS = {"gamma": "https://gamma-api.polymarket.com", "clob": "https://clob.polymarket.com"}
    def __init__(self):
        self.client = httpx.Client(timeout=5.0, headers={"User-Agent": "polymarket-paper-v13"})

    def get(self, host, path, **params):
        for attempt in range(3):
            try:
                response = self.client.get(self.HOSTS[host] + path, params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (408, 429, 500, 502, 503, 504):
                    raise
                if attempt == 2 or STOP:
                    raise
                delay = min(8.0, 2 ** attempt + random.random())
                if isinstance(exc, httpx.HTTPStatusError):
                    delay = min(10.0, max(delay, number(exc.response.headers.get("Retry-After"), 0)))
                LOG.warning("retry host=%s path=%s error=%s attempt=%s delay=%.2f", host, path, type(exc).__name__, attempt + 1, delay)
                time.sleep(delay)

    def markets(self, cfg):
        now = utc()
        result = {}
        for page in range(cfg.pages):
            rows = self.get("gamma", "/markets", active="true", closed="false", limit=100,
                            offset=page * 100, end_date_min=(now + timedelta(hours=cfg.min_hours)).isoformat(),
                            end_date_max=(now + timedelta(days=cfg.max_days)).isoformat(),
                            order="volume24hr", ascending="false")
            if not isinstance(rows, list):
                raise ValueError("markets_schema")
            for row in rows:
                result[str(row["id"])] = row
            if len(rows) < 100:
                break
        LOG.info("discovery fetched=%d page_cap=%d", len(result), cfg.pages)
        return list(result.values())

    def book(self, token, cfg):
        raw = self.get("clob", "/book", token_id=token)
        stamp = number(raw.get("timestamp"))
        if stamp is None or abs(time.time() - stamp / 1000) > cfg.book_max_age:
            raise ValueError("stale_book")
        parsed = {}
        for side in ("bids", "asks"):
            levels = [(number(x.get("price")), number(x.get("size"))) for x in raw.get(side, [])]
            if any(p is None or q is None or not 0 <= p <= 1 or q <= 0 for p, q in levels):
                raise ValueError("invalid_book")
            parsed[side] = sorted(levels, reverse=side == "bids")
        if parsed["bids"] and parsed["asks"] and parsed["bids"][0][0] >= parsed["asks"][0][0]:
            raise ValueError("crossed_book")
        return parsed


def candidate(m, cfg, now=None):
    now = now or utc()
    if m.get("closed") or m.get("active") is not True or m.get("acceptingOrders") is not True or not m.get("enableOrderBook"):
        return None, "not_tradable"
    try:
        end = date(m.get("endDate"))
    except (ValueError, TypeError):
        return None, "invalid_end_date"
    hours = (end - now).total_seconds() / 3600
    if not cfg.min_hours <= hours <= cfg.max_days * 24:
        return None, "expiry_window"
    label = str(m.get("question", "")) + " " + str(m.get("slug", ""))
    if re.search(r"up.or.down|updown|5m-|15m-|5.minute|15.minute", label, re.I):
        return None, "ultrashort_family"
    for field in ("eventStartTime", "gameStartTime"):
        if m.get(field):
            try:
                if date(m[field]) <= now + timedelta(hours=cfg.min_hours):
                    return None, "event_too_close"
            except ValueError:
                return None, "invalid_event_date"
    liq, vol = number(m.get("liquidityNum")), number(m.get("volume24hr"))
    if liq is None or liq < cfg.min_liquidity:
        return None, "liquidity"
    if vol is None or vol < cfg.min_volume:
        return None, "volume24h"
    h, d = number(m.get("oneHourPriceChange")), number(m.get("oneDayPriceChange"))
    if h is None and d is None:
        return None, "missing_changes"
    direction = h if h is not None and abs(h) >= cfg.hour_move else d
    if direction is None or abs(direction) < (cfg.hour_move if h is not None and abs(h) >= cfg.hour_move else cfg.day_move):
        return None, "no_recent_momentum"
    sign = 1 if direction > 0 else -1
    if h is not None and h * sign < -0.001:
        return None, "hour_reversal"
    if d is not None and d * sign < -0.02:
        return None, "day_conflict"
    try:
        outcomes, tokens = array(m["outcomes"]), array(m["clobTokenIds"])
        if len(outcomes) != 2 or len(tokens) != 2 or set(outcomes) != {"Yes", "No"}:
            return None, "non_binary_yes_no"
        outcome = "Yes" if sign > 0 else "No"
        token = str(tokens[outcomes.index(outcome)])
    except (KeyError, ValueError, TypeError):
        return None, "invalid_tokens"
    events = m.get("events") or []
    event = str(events[0].get("id", m["id"])) if events else str(m["id"])
    score = math.log1p(vol) + 100 * abs(h or 0) + 10 * abs(d or 0)
    return dict(market=str(m["id"]), token=token, event=event, outcome=outcome,
                question=m.get("question", ""), end=end.isoformat(), h=h, d=d,
                volume=vol, liquidity=liq, score=score), "eligible"


def sweep(levels, quantity, factor):
    remaining, value = quantity, 0.0
    for price, size in levels:
        used = min(remaining, size)
        value += used * min(1.0, max(0.0, price * factor))
        remaining -= used
        if remaining <= 1e-9:
            break
    return quantity - remaining, value

class Ledger:
    def __init__(self, path, cfg):
        self.db = sqlite3.connect(path)
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and "v13_meta" not in tables:
            raise ValueError("Refusing legacy database; use a separate V1.3 SQLite file")
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS v13_meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, ts TEXT, position_id TEXT, side TEXT,
          market TEXT, token TEXT, quantity REAL, price REAL, fee REAL, pnl REAL, reason TEXT);
        CREATE TABLE IF NOT EXISTS equity(id INTEGER PRIMARY KEY, ts TEXT, cash REAL, equity REAL, positions INTEGER, stale INTEGER);
        CREATE TABLE IF NOT EXISTS candidates(id INTEGER PRIMARY KEY, ts TEXT, market TEXT, reason TEXT, data TEXT);
        ''')
        saved = self.db.execute("SELECT value FROM v13_meta WHERE key='state'").fetchone()
        self.state = json.loads(saved[0]) if saved else dict(cash=cfg.starting_cash, positions={}, day=str(utc().date()), day_start=cfg.starting_cash, killed=False, cooldown={})
        old_cfg = self.db.execute("SELECT value FROM v13_meta WHERE key='config'").fetchone()
        if old_cfg and json.loads(old_cfg[0]) != asdict(cfg):
            raise ValueError("Configuration changed; use a new V1.3 database for a comparable experiment")
        self.db.execute("INSERT OR IGNORE INTO v13_meta VALUES ('config',?)", (json.dumps(asdict(cfg)),))
        self.save()
        self.db.commit()

    def save(self):
        self.db.execute("INSERT OR REPLACE INTO v13_meta VALUES ('state',?)", (json.dumps(self.state),))

    def log_candidate(self, market, reason, data):
        self.db.execute("INSERT INTO candidates(ts,market,reason,data) VALUES (?,?,?,?)", (utc().isoformat(), str(market), reason, json.dumps(data)))
        LOG.debug("candidate market=%s reason=%s data=%s", market, reason, json.dumps(data))

    def trade(self, p, side, qty, value, fee, pnl, reason):
        self.db.execute("INSERT INTO trades(ts,position_id,side,market,token,quantity,price,fee,pnl,reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (utc().isoformat(), p["id"], side, p["market"], p["token"], qty, value / qty, fee, pnl, reason))
        self.save()
        LOG.info("%s market=%s qty=%.4f price=%.5f fee=%.4f pnl=%.4f reason=%s", side, p["market"], qty, value / qty, fee, pnl, reason)

class Bot:
    def __init__(self, ledger, api, cfg):
        self.ledger, self.api, self.cfg = ledger, api, cfg
        self.s = ledger.state
        self.previous = {}

    def equity(self):
        return self.s["cash"] + sum(p["mark"] for p in self.s["positions"].values())

    def kill(self):
        # Day rollover is done before refreshing marks, so an overnight gap counts.
        if self.equity() <= self.s["day_start"] * (1 - self.cfg.daily_loss):
            self.s["killed"] = True
        return self.s["killed"]

    def close(self, p, book, reason):
        p["exit_reason"] = reason
        qty, gross = sweep(book["bids"], p["qty"], 1 - self.cfg.slippage_bps / 10000)
        if qty <= 1e-9:
            LOG.warning("exit_pending market=%s reason=%s no_bid_depth", p["market"], reason)
            return
        fee = gross * self.cfg.fee_bps / 10000
        cost = p["cost"] * qty / p["qty"]
        p["qty"] -= qty
        p["cost"] -= cost
        self.s["cash"] += gross - fee
        p["mark"] = 0.0  # All available bids were consumed if exit was partial.
        if p["qty"] <= 1e-8:
            del self.s["positions"][p["token"]]
            self.s["cooldown"][p["market"]] = time.time() + self.cfg.cooldown
        self.ledger.trade(p, "SELL", qty, gross, fee, gross - fee - cost, reason)

    def settle(self, p):
        m = self.api.get("gamma", "/markets/" + p["market"])
        if not m.get("closed") or m.get("umaResolutionStatus") != "resolved":
            return False
        tokens, prices = array(m.get("clobTokenIds")), array(m.get("outcomePrices"))
        if not isinstance(tokens, list) or not isinstance(prices, list) or len(tokens) != len(prices):
            return False
        if p["token"] not in tokens:
            return False
        values = [number(v) for v in prices]
        if len(values) != 2 or any(v not in (0.0, 1.0) for v in values) or sum(values) != 1:
            return False
        qty = p["qty"]
        gross = qty * values[tokens.index(p["token"])]
        self.s["cash"] += gross
        del self.s["positions"][p["token"]]
        self.s["cooldown"][p["market"]] = time.time() + self.cfg.cooldown
        self.ledger.trade(p, "SETTLE", qty, gross, 0, gross - p["cost"], "confirmed_resolution")
        return True

    def manage(self):
        stale, books = False, {}
        for token, p in list(self.s["positions"].items()):
            try:
                if date(p["end"]) <= utc() and self.settle(p):
                    continue
                b = self.api.book(token, self.cfg)
                books[token] = b
                _, gross = sweep(b["bids"], p["qty"], 1 - self.cfg.slippage_bps / 10000)
                p["mark"] = gross * (1 - self.cfg.fee_bps / 10000)
                p["marked_at"] = utc().isoformat()
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                stale = True
                LOG.warning("position_stale market=%s error=%s", p["market"], type(exc).__name__)
        killed = self.kill()
        for token, p in list(self.s["positions"].items()):
            ret = p["mark"] / p["cost"] - 1
            reason = p.get("exit_reason")
            if killed:
                reason = "daily_kill"
            elif (date(p["end"]) - utc()).total_seconds() <= self.cfg.exit_hours * 3600:
                reason = "before_expiry"
            elif ret <= -self.cfg.stop_loss:
                reason = "stop_loss"
            elif ret >= self.cfg.take_profit:
                reason = "take_profit"
            if reason:
                p["exit_reason"] = reason
                if token in books:
                    self.close(p, books[token], reason)
        self.kill()
        return stale or any(p.get("exit_reason") for p in self.s["positions"].values())

    def enter(self, c, book):
        cfg = self.cfg
        if self.kill():
            return "daily_kill"
        if len(self.s["positions"]) >= cfg.max_positions:
            return "max_positions"
        if any(p["event"] == c["event"] for p in self.s["positions"].values()):
            return "event_exposure"
        if time.time() < self.s["cooldown"].get(c["market"], 0):
            return "cooldown"
        if not book["asks"] or not book["bids"]:
            return "empty_book"
        ask, bid = book["asks"][0][0], book["bids"][0][0]
        spread = ask - bid
        c.update(bid=bid, ask=ask, spread=spread)
        if not 0.10 <= ask <= 0.85:
            return "price_range"
        if spread > cfg.max_spread or spread / ask > cfg.max_relative_spread:
            return "spread"
        old = self.previous.get(c["token"])
        mid = (bid + ask) / 2
        self.previous[c["token"]] = (time.time(), mid)
        if not old or not 5 <= time.time() - old[0] <= 180:
            return "quote_warmup"
        if mid < old[1] - 0.001:
            return "quote_reversal"
        eq = self.equity()
        exposure = sum(p["cost"] for p in self.s["positions"].values())
        # Remaining daily loss budget caps the full capital at risk, not just the nominal stop.
        loss_budget = max(0.0, eq - self.s["day_start"] * (1 - cfg.daily_loss) - exposure)
        budget = min(self.s["cash"], eq * cfg.position_pct, eq * cfg.max_exposure - exposure, loss_budget / max(1, cfg.max_positions - len(self.s["positions"])))
        if budget < 2:
            return "risk_budget"
        unit = min(1.0, ask * (1 + cfg.slippage_bps / 10000))
        qty = budget / (unit * (1 + cfg.fee_bps / 10000))
        # Fill only within the allowed impact band; never invent unavailable shares.
        levels = [(p, q) for p, q in book["asks"] if p <= ask * 1.005]
        filled, gross = sweep(levels, qty, 1 + cfg.slippage_bps / 10000)
        if filled < qty - 1e-8:
            return "ask_depth"
        fee = gross * cfg.fee_bps / 10000
        if gross + fee > budget + 1e-8:
            return "impact_budget"
        sell_qty, exit_gross = sweep(book["bids"], qty, 1 - cfg.slippage_bps / 10000)
        mark = exit_gross * (1 - cfg.fee_bps / 10000)
        if sell_qty < qty - 1e-8 or 1 - mark / (gross + fee) > cfg.take_profit / 2:
            return "roundtrip_cost_or_depth"
        p = dict(c, id=uuid.uuid4().hex, qty=qty, cost=gross + fee, mark=mark,
                 opened=utc().isoformat(), marked_at=utc().isoformat())
        self.s["positions"][c["token"]] = p
        self.s["cash"] -= gross + fee
        self.ledger.trade(p, "BUY", qty, gross, fee, 0, "recent_momentum_confirmed")
        return "BUY"

    def cycle(self):
        if self.s["day"] != str(utc().date()):
            self.s.update(day=str(utc().date()), day_start=self.equity(), killed=False)
        reasons = Counter()
        with self.ledger.db:
            stale = self.manage()
            self.ledger.save()
        if not stale and not self.kill():
            candidates = []
            for m in self.api.markets(self.cfg):
                c, why = candidate(m, self.cfg)
                reasons[why] += 1
                self.ledger.log_candidate(m.get("id"), why, c or {k: m.get(k) for k in ("endDate", "volume24hr", "liquidityNum", "oneHourPriceChange", "oneDayPriceChange")})
                if c:
                    candidates.append(c)
            for c in sorted(candidates, key=lambda x: x["score"], reverse=True)[:self.cfg.candidates]:
                if STOP:
                    break
                try:
                    with self.ledger.db:
                        if c["token"] in self.s["positions"]:
                            why = "held"
                        else:
                            why = self.enter(c, self.api.book(c["token"], self.cfg))
                        self.ledger.log_candidate(c["market"], why, c)
                        self.ledger.save()
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    why = "book_error_" + type(exc).__name__
                    c["error"] = str(exc)[:200]
                    LOG.warning("candidate_error market=%s error=%s detail=%s", c["market"], type(exc).__name__, str(exc)[:200])
                    self.ledger.log_candidate(c["market"], why, c)
                reasons[why] += 1
        with self.ledger.db:
            self.ledger.save()
            self.ledger.db.execute("INSERT INTO equity(ts,cash,equity,positions,stale) VALUES (?,?,?,?,?)",
                (utc().isoformat(), self.s["cash"], self.equity(), len(self.s["positions"]), int(stale)))
        LOG.info("cycle equity=%.4f cash=%.4f positions=%d killed=%s stale=%s reasons=%s", self.equity(), self.s["cash"], len(self.s["positions"]), self.s["killed"], stale, dict(reasons))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="paper_trading_v13.sqlite3")
    parser.add_argument("--minutes", type=float, default=0, help="0 runs until interrupted")
    parser.add_argument("--log", default="paper_v13.log")
    args = parser.parse_args()
    if args.minutes < 0 or not math.isfinite(args.minutes):
        parser.error("minutes must be finite and nonnegative")
    cfg = Config()
    LOG.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    stream.setFormatter(formatter)
    file = RotatingFileHandler(args.log, maxBytes=5_000_000, backupCount=3)
    file.setFormatter(formatter)
    LOG.addHandler(stream)
    LOG.addHandler(file)
    def stop(*_):
        global STOP
        STOP = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    # An OS-held lock prevents two processes from spending the same paper balance.
    lock = open(str(Path(args.db).resolve()) + ".lock", "a+")
    import fcntl  # Codespaces/Linux runner
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another V1.3 process owns this database")
    ledger = Ledger(args.db, cfg)
    api = PublicData()
    bot = Bot(ledger, api, cfg)
    LOG.info("PAPER ONLY V1.3 config=%s db=%s", asdict(cfg), args.db)
    with ledger.db:
        ledger.db.execute("INSERT INTO equity(ts,cash,equity,positions,stale) VALUES (?,?,?,?,?)", (utc().isoformat(), bot.s["cash"], bot.equity(), len(bot.s["positions"]), 1 if bot.s["positions"] else 0))
    started = time.monotonic()
    cycles, errors = 0, 0
    try:
        while not STOP and (not args.minutes or time.monotonic() - started < args.minutes * 60):
            tick = time.monotonic()
            try:
                bot.cycle()
                cycles += 1
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                errors += 1
                LOG.exception("cycle_error type=%s", type(exc).__name__)
                ledger.db.commit()
            while not STOP and time.monotonic() - tick < cfg.interval:
                if args.minutes and time.monotonic() - started >= args.minutes * 60:
                    break
                time.sleep(0.2)
    finally:
        ledger.db.commit()
        ledger.db.close()
        api.client.close()
        lock.close()
        LOG.info("STOPPED cycles=%d errors=%d elapsed=%.1f state_preserved=True", cycles, errors, time.monotonic() - started)
    if cycles == 0:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
