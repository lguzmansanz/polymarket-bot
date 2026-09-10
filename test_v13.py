import copy
from dataclasses import replace
from datetime import timedelta
import json
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
import bot_v13 as b
from report_v13 import report

class FakeAPI:
    def __init__(self):
        self.books = {'yes': {'asks':[(.5,1000)],'bids':[(.499,1000)]}}
        self.rows=[]
    def book(self, token, cfg):
        result=self.books[token]
        if isinstance(result, Exception):
            raise result
        return result
    def markets(self, cfg):
        return self.rows
    def get(self, *a, **kw):
        return self.resolution

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=self.tmp.name+'/test.sqlite3'
        self.cfg=b.Config()
        self.ledger=b.Ledger(self.path,self.cfg)
        self.api=FakeAPI()
        self.bot=b.Bot(self.ledger,self.api,self.cfg)
        self.c=dict(market='m',token='yes',event='e',outcome='Yes',question='Will example happen?',
          end=(b.utc()+timedelta(days=2)).isoformat(),h=.01,d=.02,volume=10000,liquidity=20000,score=1)
    def tearDown(self):
        self.ledger.db.close()
        self.tmp.cleanup()
    def buy(self,c=None):
        c=c or self.c
        self.bot.previous[c['token']] = (time.time()-15,.4995)
        with self.ledger.db:
            result=self.bot.enter(c,self.api.books['yes'])
        self.assertEqual(result,'BUY')
        return self.bot.s['positions'][c['token']]
    def market(self):
        return dict(id='m',active=True,closed=False,acceptingOrders=True,enableOrderBook=True,
          endDate=self.c['end'],question=self.c['question'],liquidityNum=20000,volume24hr=10000,
          oneHourPriceChange=.01,oneDayPriceChange=.02,outcomes='["Yes","No"]',clobTokenIds='["yes","no"]')
    def test_entry_and_roundtrip_accounting(self):
        p=self.buy()
        self.assertLessEqual(p['cost'],10.000001)
        self.assertLess(self.bot.equity(),1000)
        self.bot.close(p,{'bids':[(.6,1000)]},'test_profit')
        self.ledger.db.commit()
        r=report(self.path)
        self.assertEqual(r['completed_trades'],1)
        self.assertEqual(r['win_rate'],1)
        self.assertAlmostEqual(r['total_pnl'],r['realized_pnl'])
    def test_zero_trades_is_not_zero_win_rate(self):
        r=report(self.path)
        self.assertIsNone(r['win_rate'])
        self.assertEqual(r['equity_final'],1000)
    def test_ultrashort_even_if_future(self):
        m=self.market(); m['question']='Bitcoin Up or Down'
        self.assertEqual(b.candidate(m,self.cfg)[1],'ultrashort_family')
    def test_date_and_missing_fields_fail_closed(self):
        for end in ((b.utc()+timedelta(minutes=5)).isoformat(),'bad', (b.utc()+timedelta(days=30)).isoformat()):
            m=self.market(); m['endDate']=end
            self.assertIsNone(b.candidate(m,self.cfg)[0])
        m=self.market(); m.pop('liquidityNum')
        self.assertEqual(b.candidate(m,self.cfg)[1],'liquidity')
        m=self.market(); m['oneHourPriceChange']='nan';m['oneDayPriceChange']=None
        self.assertEqual(b.candidate(m,self.cfg)[1],'missing_changes')
    def test_negative_momentum_selects_no(self):
        m=self.market();m['oneHourPriceChange']=-.01;m['oneDayPriceChange']=-.02
        self.assertEqual(b.candidate(m,self.cfg)[0]['token'],'no')
    def test_spread_and_depth(self):
        self.assertEqual(self.bot.enter(self.c,{'asks':[(.5,100)],'bids':[(.4,100)]}),'spread')
        self.bot.previous['yes']=(time.time()-15,.4995)
        self.assertEqual(self.bot.enter(self.c,{'asks':[(.5,1)],'bids':[(.499,100)]}),'ask_depth')
    def test_max_positions_and_daily_capital_budget(self):
        self.buy()
        c=dict(self.c,market='m2',token='t2',event='e2')
        self.buy(c)
        self.assertLessEqual(sum(p['cost'] for p in self.bot.s['positions'].values()),20)
        c=dict(self.c,market='m3',token='t3',event='e3')
        self.assertEqual(self.bot.enter(c,self.api.books['yes']),'max_positions')
    def test_event_concentration(self):
        self.buy()
        c=dict(self.c,market='m2',token='t2')
        self.assertEqual(self.bot.enter(c,self.api.books['yes']),'event_exposure')
    def test_stop_at_extreme_price_outside_selector(self):
        self.buy()
        self.api.books['yes']={'asks':[(.002,1000)],'bids':[(.001,1000)]}
        self.bot.manage()
        self.assertEqual(len(self.bot.s['positions']),0)
        self.assertEqual(self.ledger.db.execute("SELECT reason FROM trades WHERE side='SELL'").fetchone()[0],'stop_loss')
    def test_expiry_exit_without_candidates(self):
        p=self.buy();p['end']=(b.utc()+timedelta(hours=1)).isoformat()
        self.bot.manage()
        self.assertEqual(len(self.bot.s['positions']),0)
        self.assertEqual(self.ledger.db.execute("SELECT reason FROM trades WHERE side='SELL'").fetchone()[0],'before_expiry')
    def test_partial_exit_and_no_fabricated_liquidity(self):
        p=self.buy();original=p['qty']
        self.bot.close(p,{'bids':[(.3,1)]},'stop_loss')
        self.assertAlmostEqual(p['qty'],original-1)
        self.assertEqual(p['exit_reason'],'stop_loss')
        self.api.books['yes']={'bids':[],'asks':[(.3,1000)]}
        self.assertTrue(self.bot.manage())
        self.assertGreater(p['qty'],0)
        self.ledger.db.commit()
        self.assertEqual(report(self.path)['completed_trades'],0)
    def test_transport_failure_blocks_entries_and_preserves_position(self):
        p=self.buy();old=p['mark']
        self.api.books['yes']=httpx.ConnectError('offline')
        self.assertTrue(self.bot.manage())
        self.assertEqual(p['mark'],old)
        self.assertIn('yes',self.bot.s['positions'])
    def test_kill_latches_and_survives_restart(self):
        self.bot.s['cash']=979
        self.assertTrue(self.bot.kill())
        self.bot.s['cash']=1000
        self.assertTrue(self.bot.kill())
        with self.ledger.db:self.ledger.save()
        other=b.Ledger(self.path,self.cfg)
        try:self.assertTrue(other.state['killed'])
        finally:other.db.close()
    def test_position_survives_restart(self):
        self.buy()
        other=b.Ledger(self.path,self.cfg)
        try:
            self.assertEqual(other.state,self.bot.s)
        finally:other.db.close()
    def test_utc_rollover(self):
        self.bot.s.update(day='2000-01-01',killed=True,cash=900)
        self.bot.cycle()
        self.assertFalse(self.bot.s['killed'])
        self.assertEqual(self.bot.s['day_start'],900)
    def test_confirmed_settlement_only(self):
        p=self.buy()
        self.api.resolution=dict(closed=True,umaResolutionStatus='proposed',clobTokenIds=['yes','no'],outcomePrices=['0','1'])
        self.assertFalse(self.bot.settle(p))
        self.api.resolution['umaResolutionStatus']='resolved'
        self.assertTrue(self.bot.settle(p))
        self.assertEqual(len(self.bot.s['positions']),0)
        self.assertEqual(self.ledger.db.execute("SELECT price FROM trades WHERE side='SETTLE'").fetchone()[0],0)
    def test_legacy_database_rejected(self):
        path=self.tmp.name+'/legacy.sqlite3'
        db=sqlite3.connect(path);db.execute('CREATE TABLE trades(x)');db.close()
        with self.assertRaises(ValueError):b.Ledger(path,self.cfg)
    def test_retry_transport_and_429_then_success(self):
        calls=[]
        def handler(request):
            calls.append(request)
            if len(calls)==1:raise httpx.ConnectError('offline',request=request)
            if len(calls)==2:return httpx.Response(429,headers={'Retry-After':'0'})
            return httpx.Response(200,json={'ok':1})
        api=b.PublicData();api.client.close();api.client=httpx.Client(transport=httpx.MockTransport(handler))
        try:
            with patch('bot_v13.time.sleep') as sleep:
                self.assertEqual(api.get('gamma','/markets'),{'ok':1})
                self.assertEqual(sleep.call_count,2)
                self.assertTrue(all(x.method=='GET' for x in calls))
        finally:api.client.close()
    def test_stale_book_rejected(self):
        api=b.PublicData()
        try:
            api.get=lambda *a,**k:dict(timestamp='0',bids=[],asks=[])
            with self.assertRaisesRegex(ValueError,'stale_book'):api.book('yes',self.cfg)
        finally:api.client.close()

    def test_take_profit_and_kill_liquidation(self):
        p=self.buy()
        self.api.books['yes']={'asks':[(.7,1000)],'bids':[(.69,1000)]}
        self.bot.manage()
        self.assertEqual(self.ledger.db.execute("SELECT reason FROM trades WHERE side='SELL'").fetchone()[0],'take_profit')
        self.assertEqual(len(self.bot.s['positions']),0)
        self.bot.s['cooldown'].clear()
        self.api.books['yes']={'asks':[(.5,1000)],'bids':[(.499,1000)]}
        self.buy()
        self.bot.s['cash']=960
        self.bot.manage()
        self.assertTrue(self.bot.s['killed'])
        self.assertEqual(len(self.bot.s['positions']),0)
        self.assertEqual(self.ledger.db.execute("SELECT reason FROM trades WHERE side='SELL' ORDER BY id DESC LIMIT 1").fetchone()[0],'daily_kill')
    def test_retry_exhaustion_and_nonretryable_404(self):
        api=b.PublicData();api.client.close()
        calls=[]
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout('timeout',request=request)
        api.client=httpx.Client(transport=httpx.MockTransport(handler))
        try:
            with patch('bot_v13.time.sleep'):
                with self.assertRaises(httpx.ReadTimeout):api.get('clob','/book')
            self.assertEqual(len(calls),3)
        finally:api.client.close()
        api.client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(404)))
        try:
            with patch('bot_v13.time.sleep') as sleep:
                with self.assertRaises(httpx.HTTPStatusError):api.get('clob','/book')
                sleep.assert_not_called()
        finally:api.client.close()
    def test_discovery_date_bounds_and_pagination(self):
        api=b.PublicData();calls=[]
        def get(host,path,**params):
            calls.append(params)
            return [{'id':str(params['offset']+i)} for i in range(100)]
        api.get=get
        try:
            self.assertEqual(len(api.markets(self.cfg)),300)
            self.assertEqual([p['offset'] for p in calls],[0,100,200])
            self.assertTrue(all(p['order']=='volume24hr' and p['ascending']=='false' for p in calls))
            self.assertGreater(b.date(calls[0]['end_date_min']), b.utc()+timedelta(hours=5.9))
            self.assertLessEqual(b.date(calls[0]['end_date_max']), b.utc()+timedelta(days=14))
        finally:api.client.close()

if __name__=='__main__':unittest.main(verbosity=2)
