import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()
db_path = os.getenv("DB_PATH", "paper_trading.sqlite3")
con = sqlite3.connect(db_path)

trades = con.execute("SELECT COUNT(*), COALESCE(SUM(realized_pnl),0) FROM trades WHERE side='SELL'").fetchone()
win = con.execute("SELECT COUNT(*) FROM trades WHERE side='SELL' AND realized_pnl>0").fetchone()[0]
loss = con.execute("SELECT COUNT(*) FROM trades WHERE side='SELL' AND realized_pnl<=0").fetchone()[0]
last_eq = con.execute("SELECT equity FROM equity ORDER BY ts DESC LIMIT 1").fetchone()

n = trades[0]
print(f"Closed trades: {n}")
print(f"Wins/Losses: {win}/{loss}")
print(f"Win rate: {(win/n*100 if n else 0):.1f}%")
print(f"Realized P&L: ${trades[1]:+.2f}")
print(f"Latest equity: ${(last_eq[0] if last_eq else 0):.2f}")
