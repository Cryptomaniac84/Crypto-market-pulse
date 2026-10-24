import unittest
from datetime import datetime, timezone, timedelta
import pandas as pd
from core import new_completed_bars, quote_is_fresh, can_buy, realized_today

class TradingRegressionTests(unittest.TestCase):
    def test_candle_started_before_entry_is_ignored(self):
        now=pd.Timestamp.now(tz='UTC').floor('15min')
        bars=pd.DataFrame({'ts':[now-pd.Timedelta(minutes=30),now-pd.Timedelta(minutes=15),now],
                           'open':[100,100,100],'low':[50,99,99],'high':[110,101,101]})
        entry=now-pd.Timedelta(minutes=20)
        out=new_completed_bars(bars,entry,entry)
        self.assertEqual(len(out),1)
        self.assertEqual(float(out.iloc[0]['low']),99)
    def test_stale_quotes(self):
        now=datetime.now(timezone.utc)
        self.assertTrue(quote_is_fresh(now-timedelta(seconds=299),now))
        self.assertFalse(quote_is_fresh(now-timedelta(seconds=301),now))
        self.assertFalse(quote_is_fresh(now+timedelta(seconds=10),now))
    def test_duplicate_buy(self):
        ok,reason=can_buy('BTC',{'BTC':.001},25,500,0)
        self.assertFalse(ok);self.assertIn('already',reason)
    def test_daily_loss_limit(self):
        ok,reason=can_buy('BTC',{},25,500,-25)
        self.assertFalse(ok);self.assertIn('loss',reason)
    def test_position_size_limit(self):
        self.assertFalse(can_buy('BTC',{},51,500,0)[0])
    def test_fifo_realized_loss(self):
        now=datetime.now(timezone.utc).isoformat()
        rows=pd.DataFrame([[1,now,'BTC','BUY',1,100,1,1,''],[2,now,'BTC','SELL',1,80,1,1,'']],columns=['id','ts','symbol','side','qty','price','fx','fee_aud','note'])
        self.assertAlmostEqual(realized_today(rows),-22)
if __name__=='__main__':unittest.main()
