"""Read-only V1.3 report. Win rate counts completed positions, not partial fills."""
import argparse
import json
from pathlib import Path
import sqlite3

def report(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    cfg = json.loads(db.execute("SELECT value FROM v13_meta WHERE key='config'").fetchone()[0])
    state = json.loads(db.execute("SELECT value FROM v13_meta WHERE key='state'").fetchone()[0])
    rows = db.execute('SELECT side,position_id,pnl,fee FROM trades ORDER BY id').fetchall()
    open_ids = {p['id'] for p in state['positions'].values()}
    pnl = {}
    for side, pid, value, fee in rows:
        if side != 'BUY':
            pnl[pid] = pnl.get(pid, 0) + value
    completed = [value for pid, value in pnl.items() if pid not in open_ids]
    final = state['cash'] + sum(p['mark'] for p in state['positions'].values())
    curve = [cfg['starting_cash']] + [r[0] for r in db.execute('SELECT equity FROM equity ORDER BY id')] + [final]
    peak, dd, dd_pct = curve[0], 0, 0
    for eq in curve:
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
        dd_pct = max(dd_pct, (peak - eq) / peak if peak else 0)
    reasons = dict(db.execute('SELECT reason,count(*) FROM candidates GROUP BY reason ORDER BY count(*) DESC'))
    out = dict(mode='PAPER ONLY', starting_equity=cfg['starting_cash'], fills=len(rows),
        entries=sum(r[0]=='BUY' for r in rows), exit_fills=sum(r[0]!='BUY' for r in rows),
        completed_trades=len(completed), winning_trades=sum(p>0 for p in completed),
        win_rate=(sum(p>0 for p in completed)/len(completed) if completed else None),
        realized_pnl=sum(pnl.values()), unrealized_pnl=sum(p['mark']-p['cost'] for p in state['positions'].values()),
        total_pnl=final-cfg['starting_cash'], simulated_fees=sum(r[3] for r in rows),
        max_drawdown_approx=dd, max_drawdown_pct_approx=dd_pct, equity_final=final,
        cash=state['cash'], open_positions=len(open_ids), killed=state['killed'],
        equity_samples=len(curve)-2, stale_samples=db.execute('SELECT count(*) FROM equity WHERE stale=1').fetchone()[0],
        last_mark_times=[p['marked_at'] for p in state['positions'].values()], candidate_reasons=reasons,
        limitations='Fees assumed at 200 bps per fill; book depth and slippage simulated. Stale marks may overstate equity. Drawdown sampled; short run is not profitability evidence.')
    db.close()
    return out

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', default='paper_trading_v13.sqlite3')
    p.add_argument('--output')
    args=p.parse_args()
    text=json.dumps(report(args.db), indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        Path(args.output).write_text(text+'\n')
