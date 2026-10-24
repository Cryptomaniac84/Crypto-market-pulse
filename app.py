import os, hmac, sqlite3, time, random, json, io
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
import requests
import streamlit as st
from core import can_buy, quote_is_fresh, new_completed_bars, trigger_on_bars, realized_today, utc_time

st.set_page_config(page_title='Crypto Market Pulse', page_icon='₿', layout='wide', initial_sidebar_state='collapsed')
st.markdown('''<style>
.stApp{background:linear-gradient(155deg,#102941 0%,#071321 52%,#0b1423 100%);color:#eef4fc}
.block-container{padding-top:1.1rem;max-width:1150px}
.stTabs [data-baseweb="tab"]{font-weight:750}
[data-testid="stMetricValue"]{color:#f8fbff}
[data-testid="stMetric"]{background:linear-gradient(130deg,#102941,#101a2c);border:1px solid #345676;border-radius:15px}
.stApp button{border-radius:10px}
@media(max-width:640px){.block-container{padding-left:0.85rem;padding-right:0.85rem}.hero{padding:14px}}
.hero{background:linear-gradient(105deg,#102c47,#10213b 55%,#57300f);padding:22px;border:1px solid #3d5972;border-radius:17px;margin-bottom:20px}
.hero h1{color:#ffc15e;font-size:clamp(27px,6vw,48px);margin:0}
.hero p{color:#c3d5e6;margin:8px 0 0}
div.stButton>button[kind=primary]{background:linear-gradient(100deg,#ffb338,#fa6e1d);color:#071321;border:0;font-weight:800}
[data-testid=stMetric]{background:#112a41;padding:12px;border-radius:12px;border:1px solid #29445c}

@media(max-width:640px){
 .block-container{padding-top:.35rem!important;padding-left:.65rem!important;padding-right:.65rem!important}
 .hero{padding:11px 14px!important;margin-bottom:10px!important;border-radius:12px!important}
 .hero h1{font-size:25px!important;line-height:1.15!important}
 .hero p{font-size:12px!important;line-height:1.4!important;margin-top:5px!important}
 [data-testid="stMetric"]{padding:7px 8px!important;min-width:0!important;border-radius:10px!important}
 [data-testid="stMetricValue"]{font-size:clamp(15px,4vw,23px)!important;overflow-wrap:anywhere!important}
 [data-testid="stMetricLabel"]{font-size:11px!important}
 [data-testid="stImage"] img{max-height:110px!important;object-fit:cover!important;border-radius:9px!important}
 .stTabs [data-baseweb="tab"]{font-size:12px!important;padding:9px 7px!important}
 h2{font-size:22px!important}h3{font-size:18px!important}
 div[data-testid="stAlert"]{padding:9px 11px!important}
}
.compact-summary-title{font-size:11px;font-weight:800;letter-spacing:1.5px;color:#ffc15e;margin-bottom:7px}
</style>''', unsafe_allow_html=True)

pwd = os.getenv('SCANNER_PASSWORD', '')
try: pwd = st.secrets.get('SCANNER_PASSWORD', pwd)
except Exception: pass
if not pwd:
    st.error('Set SCANNER_PASSWORD in Streamlit app secrets before using this app.'); st.stop()
if not st.session_state.get('unlocked', False):
    st.title('₿ Crypto Market Pulse')
    p = st.text_input('Your scanner password', type='password')
    if st.button('Unlock', type='primary'):
        if hmac.compare_digest(p, str(pwd)): st.session_state.unlocked = True; st.rerun()
        else: st.error('Wrong password')
    st.stop()

COINS = {
 'BTC':('Bitcoin','XXBTZUSD','bitcoin'),'ETH':('Ethereum','XETHZUSD','ethereum'),
 'SOL':('Solana','SOLUSD','solana'),'XRP':('XRP','XRPUSD','ripple'),
 'DOGE':('Dogecoin','XDGUSD','dogecoin'),'ADA':('Cardano','ADAUSD','cardano'),
 'LINK':('Chainlink','LINKUSD','chainlink'),'AVAX':('Avalanche','AVAXUSD','avalanche-2'),
 'SUI':('Sui','SUIUSD','sui'),'HBAR':('Hedera','HBARUSD','hedera-hashgraph'),
 'HYPE':('Hyperliquid',None,'hyperliquid'),'ZEC':('Zcash','XZECZUSD','zcash')}
DB = os.getenv('DB_PATH','crypto_paper.sqlite3')
EXCHANGES = {'Coinbase Advanced':{'maker':0.0009,'taker':0.0010},'CoinSpot Markets':{'maker':0.001,'taker':0.001},'CoinSpot Instant':{'maker':0.01,'taker':0.01}}

def now_iso(): return datetime.now(timezone.utc).isoformat()

def secret(name):
    try: return str(st.secrets.get(name,os.getenv(name,''))).strip()
    except Exception: return str(os.getenv(name,'')).strip()

@contextmanager
def connect():
    c = sqlite3.connect(DB,timeout=15)
    try:
        c.execute('PRAGMA busy_timeout=15000')
        yield c
        c.commit()
    except Exception:
        c.rollback(); raise
    finally: c.close()

def init_db():
    with connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT,symbol TEXT,side TEXT,qty REAL,price REAL,fx REAL,fee_aud REAL,note TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS plans(symbol TEXT PRIMARY KEY,stop REAL,target REAL,checked TEXT,state TEXT,entry_at TEXT)')
        cols = {r[1] for r in c.execute('PRAGMA table_info(plans)')}
        if 'entry_at' not in cols: c.execute('ALTER TABLE plans ADD COLUMN entry_at TEXT')
        # Old plans cannot be reconstructed reliably: use their original checked timestamp as a conservative boundary.
        c.execute('UPDATE plans SET entry_at=checked WHERE entry_at IS NULL')
        c.execute('CREATE TABLE IF NOT EXISTS telegram_alerts(key TEXT PRIMARY KEY,sent_at TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS fx_cache(id INTEGER PRIMARY KEY CHECK(id=1),rate REAL,asof TEXT,source TEXT)')
init_db()

def get_fills():
    with connect() as c: return pd.read_sql_query('SELECT * FROM fills ORDER BY id',c)

def positions(fills=None):
    out = {}
    for r in (get_fills() if fills is None else fills).itertuples(): out[r.symbol] = out.get(r.symbol,0)+(r.qty if r.side=='BUY' else -r.qty)
    return {k:v for k,v in out.items() if v>1e-8}

def balances(fills=None):
    cash,fees=500.,0.
    for r in (get_fills() if fills is None else fills).itertuples():
        amount=r.qty*r.price*r.fx
        cash += (-amount if r.side=='BUY' else amount)-r.fee_aud
        fees += r.fee_aud
    return cash,fees

def request_json(url,params=None,timeout=12):
    for attempt in range(4):
        try:
            response=requests.get(url,params=params,timeout=timeout)
            if response.status_code in (429,500,502,503,504):
                if attempt==3: response.raise_for_status()
                retry=response.headers.get('Retry-After','')
                try: delay=min(20,max(0,float(retry)))
                except ValueError: delay=min(12,2**attempt)+random.uniform(0,.5)
                time.sleep(delay); continue
            response.raise_for_status()
            return response.json()
        except (requests.Timeout,requests.ConnectionError):
            if attempt==3: raise
            time.sleep(min(12,2**attempt)+random.uniform(0,.5))
    raise RuntimeError('Market data unavailable')

@st.cache_data(ttl=60,show_spinner=False)
def coingecko_spot():
    ids=','.join(v[2] for v in COINS.values())
    return request_json('https://api.coingecko.com/api/v3/simple/price',{'ids':ids,'vs_currencies':'usd,aud','include_last_updated_at':'true'})

@st.cache_data(ttl=45,show_spinner=False)
def kraken_tickers():
    pairs=','.join(pair for _,pair,_ in COINS.values() if pair)
    payload=request_json('https://api.kraken.com/0/public/Ticker',{'pair':pairs})
    if payload.get('error'): raise ValueError('; '.join(payload['error']))
    return payload.get('result',{})

def quote_for(sym,prices,tickers):
    """Kraken price preferred; CoinGecko fallback only if timestamp is fresh."""
    pair=COINS[sym][1]
    if pair:
        # Kraken keys may be canonicalised; exact known aliases and Kraken-returned pairs are not guaranteed identical.
        aliases={'BTC':['XXBTZUSD','XBTUSD'],'ETH':['XETHZUSD','ETHUSD'],'DOGE':['XXDGZUSD','XDGUSD'],'ZEC':['XZECZUSD','ZECUSD']}
        for key in aliases.get(sym,[pair]):
            if key in tickers:
                value=float(tickers[key]['c'][0]); return value,datetime.now(timezone.utc),'Kraken ticker'
    q=prices.get(COINS[sym][2],{})
    if 'usd' in q and quote_is_fresh(q.get('last_updated_at')):
        return float(q['usd']),pd.Timestamp(q['last_updated_at'],unit='s',tz='UTC'),'CoinGecko fallback'
    raise ValueError('No fresh verified quote available')

@st.cache_data(ttl=120,show_spinner=False)
def candles(symbol,interval=15):
    pair=COINS[symbol][1]
    if pair is None: raise ValueError('No verified candle feed; price board only')
    payload=request_json('https://api.kraken.com/0/public/OHLC',{'pair':pair,'interval':interval})
    if payload.get('error'): raise ValueError('; '.join(payload['error']))
    data=next(v for k,v in payload['result'].items() if k!='last')
    frame=pd.DataFrame(data,columns=['ts','open','high','low','close','vwap','volume','count'])
    for k in ['open','high','low','close','volume']:frame[k]=pd.to_numeric(frame[k])
    frame['ts']=pd.to_datetime(frame['ts'],unit='s',utc=True)
    return frame

def indicators(symbol,price):
    d=candles(symbol).iloc[:-1].copy()
    if len(d)<55: raise ValueError('Not enough completed candles')
    close=d.close; ema20=close.ewm(span=20,adjust=False).mean().iloc[-1]
    delta=close.diff();gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    rs=gain.iloc[-1]/max(loss.iloc[-1],1e-10);rsi=100-100/(1+rs)
    vol=float(d.volume.iloc[-1]/max(d.volume.iloc[-21:-1].mean(),1e-9))
    resistance=float(d.high.iloc[-21:-1].max());support=float(d.low.iloc[-11:-1].min())
    # Live quotes and the most recent completed 15-minute close may legitimately diverge, but not arbitrarily.
    last_close=float(close.iloc[-1])
    if abs(price/last_close-1)>.05: raise ValueError('Spot/candle discrepancy exceeds 5%; signal rejected')
    score=sum([price>ema20,50<=rsi<=72,vol>=1.3,price>resistance])
    entry=max(price,resistance)*1.001;stop=min(support,entry*.985)
    if stop<=0 or stop>=entry: stop=entry*.975
    target=entry+2*(entry-stop)
    return dict(symbol=symbol,price=price,score=score,rsi=rsi,vol=vol,entry=entry,stop=stop,target=target,last=str(d.ts.iloc[-1]),breakout=price>resistance)

def fmt(x): return f'{x:,.2f}' if abs(x)>=1 else f'{x:,.5f}'
def dual(x,fx): return f'A${fmt(x*fx)} (US${fmt(x)})'
def fee_usd(amount,exchange,kind): return amount*EXCHANGES[exchange][kind.lower()]

def fx_rate():
    # Frankfurter is ECB reference data: typically refreshed on working days, not a live dealing rate.
    try:
        data=request_json('https://api.frankfurter.dev/v1/latest',{'base':'USD','symbols':'AUD'})
        rate=float(data['rates']['AUD']); date=data['date']
        if not .5<rate<3: raise ValueError('FX out of bounds')
        # Date is reference date (not publication timestamp); allow weekends, but cap age at 5 calendar days.
        if (datetime.now(timezone.utc).date()-datetime.fromisoformat(date).date()).days>5: raise ValueError('FX reference too old')
        with connect() as c:c.execute('INSERT OR REPLACE INTO fx_cache(id,rate,asof,source) VALUES(1,?,?,?)',(rate,date,'Frankfurter/ECB'))
        return rate,date,'Frankfurter/ECB'
    except Exception:
        with connect() as c: row=c.execute('SELECT rate,asof,source FROM fx_cache WHERE id=1').fetchone()
        if row and (datetime.now(timezone.utc).date()-datetime.fromisoformat(row[1]).date()).days<=5: return row
        raise ValueError('AUD/USD reference rate unavailable or stale; AUD-dependent trading disabled')

def telegram_send(message):
    token=secret('TELEGRAM_BOT_TOKEN');chat=secret('TELEGRAM_CHAT_ID')
    if not token or not chat:return False,'Telegram is not configured.'
    try:
        r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':message,'disable_web_page_preview':True},timeout=10)
        payload=r.json()
        if r.ok and payload.get('ok'):return True,'Message sent.'
        return False,f'Telegram rejected message (HTTP {r.status_code})'
    except Exception as e:return False,f'Telegram failed: {type(e).__name__}'

def alert_once(key,message):
    with connect() as c:
        if c.execute('SELECT 1 FROM telegram_alerts WHERE key=?',(key,)).fetchone():return False,'Already notified'
        ok,msg=telegram_send(message)
        if ok:c.execute('INSERT INTO telegram_alerts(key,sent_at) VALUES(?,?)',(key,now_iso()))
        return ok,msg

def flash(kind,message): st.session_state.setdefault('flash_messages',[]).append((kind,message))

def save_fill(sym,side,qty,price,fx,fee,note,plan=None):
    """Atomic fill and optional plan; recheck duplicate BUY inside transaction."""
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if side=='BUY':
            held=c.execute("SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END),0) FROM fills WHERE symbol=?",(sym,)).fetchone()[0]
            if held>1e-8:raise ValueError('An open position already exists for this coin.')
        stamp=now_iso()
        c.execute('INSERT INTO fills(ts,symbol,side,qty,price,fx,fee_aud,note) VALUES(?,?,?,?,?,?,?,?)',(stamp,sym,side,qty,price,fx,fee,note))
        if plan:
            c.execute('INSERT INTO plans(symbol,stop,target,checked,state,entry_at) VALUES(?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET stop=excluded.stop,target=excluded.target,checked=excluded.checked,state=excluded.state,entry_at=excluded.entry_at',(sym,plan[0],plan[1],stamp,'ACTIVE',stamp))
        elif side=='SELL':
            held=c.execute("SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END),0) FROM fills WHERE symbol=?",(sym,)).fetchone()[0]
            if held<=1e-8:c.execute("UPDATE plans SET state='CLOSED' WHERE symbol=?",(sym,))

def check_plans(fx,exchange,slippage_pct,quotes):
    msgs=[];pos=positions()
    with connect() as c:plans=c.execute("SELECT symbol,stop,target,checked,entry_at FROM plans WHERE state='ACTIVE'").fetchall()
    for sym,stop,target,checked,entry_at in plans:
        if pos.get(sym,0)<=0:continue
        try:
            px,quote_at,source=quotes[sym]
            if not quote_is_fresh(quote_at): raise ValueError('Stale quote: no exit simulated')
            bars=new_completed_bars(candles(sym),entry_at,checked)
            if bars.empty:msgs.append(f'{sym}: No completed post-entry candles.');continue
            triggered=trigger_on_bars(bars,stop,target)
            if triggered:
                kind,exit_px=triggered;qty=pos[sym]
                exit_px*=1-slippage_pct/100
                fee=fee_usd(qty*exit_px,exchange,'taker')*fx
                save_fill(sym,'SELL',qty,exit_px,fx,fee,f'Paper {kind}; historical 15m candle, {slippage_pct:.2f}% slippage')
                with connect() as c:c.execute("UPDATE plans SET state='CLOSED',checked=? WHERE symbol=?",(now_iso(),sym))
                msgs.append(f'{sym}: PAPER {kind} — {dual(exit_px,fx)}. Not a real order.')
            else:
                # Cursor points to next candle start, not its end, avoiding a skipped candle.
                cursor=str(bars.ts.iloc[-1]+pd.Timedelta(minutes=15))
                with connect() as c:c.execute('UPDATE plans SET checked=? WHERE symbol=?',(cursor,sym))
                msgs.append(f'{sym}: No stop or target crossed in completed post-entry candles.')
        except Exception as e:msgs.append(f'{sym}: Could not check — {e}. No exit recorded.')
    return msgs

banner=Path(__file__).parent/'crypto_banner.jpg'
st.markdown('<div class="hero"><h1>₿ CRYPTO MARKET PULSE</h1><p>Rebound scanner • Fee modelling • Paper trading • Telegram alerts • AUD (USD)</p></div>',unsafe_allow_html=True)
if banner.exists():st.image(str(banner),use_container_width=True)
st.caption('PAPER TRADING ONLY • No exchange trading connection • No background monitoring • Data may be delayed')
try: fx,fx_date,fx_source=fx_rate()
except Exception as e:st.error(str(e));st.stop()
try: prices=coingecko_spot()
except Exception as e:prices={};st.warning(f'CoinGecko price board unavailable: {type(e).__name__}; using Kraken where available.')
try: tickers=kraken_tickers()
except Exception as e:tickers={};st.warning(f'Kraken ticker unavailable: {type(e).__name__}; fresh CoinGecko fallback only.')
quotes={}
for s in COINS:
    try: quotes[s]=quote_for(s,prices,tickers)
    except Exception:pass
fills=get_fills();pos=positions(fills);cash,fees=balances(fills)
st.markdown('<div class="compact-summary-title">PAPER PORTFOLIO</div>', unsafe_allow_html=True)
a,b,c=st.columns(3)
a.metric('Cash',f'A${cash:,.2f}');b.metric('Positions',len(pos));c.metric('Trades',len(fills))
st.caption(f'USD→AUD reference: A${fx:.4f} per US$1 · {fx_source} · reference date {fx_date} (not live FX).')
st.caption('Practice only · A$500 starting balance · No real orders')
with st.expander('⚙️ Trading assumptions and risk limits', expanded=False):
    st.caption('These settings apply to new simulated trades.')
    a,b,c=st.columns(3)
    with a: exchange=st.selectbox('Fee schedule',list(EXCHANGES))
    with b: order_type=st.selectbox('Paper order type',['Taker','Maker'])
    with c: slippage_pct=st.number_input('Estimated slippage (%)',min_value=0.,max_value=5.,value=.10,step=.05)
    a,b,c=st.columns(3)
    with a: max_position=st.number_input('Max position (AUD)',min_value=1.,value=50.,step=5.)
    with b: max_open=st.number_input('Max open positions',min_value=1,max_value=20,value=3,step=1)
    with c: max_daily_loss=st.number_input('Max daily realised loss (AUD)',min_value=1.,value=25.,step=5.)
    daily=realized_today(fills)
    st.caption(f'Today’s realised P/L (UTC): A${daily:,.2f}. Fee assumptions are illustrative and not verified current schedules.')
with st.expander('📨 Telegram alert setup and test'):
    st.write('Alerts send only on manual scan/trade actions. No background monitoring.')
    if secret('TELEGRAM_BOT_TOKEN') and secret('TELEGRAM_CHAT_ID'):
        st.success('Telegram credentials detected.')
        if st.button('📨 Send test Telegram alert'):
            ok,msg=telegram_send('🧪 Crypto Market Pulse: Telegram connection working. Paper mode only.')
            (st.success if ok else st.error)(msg)
    else:st.warning('Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in secrets.')

scan_tab,trade_tab,learn_tab=st.tabs(['🎯 Scan crypto','💼 Paper trading','❓ How it works'])
with scan_tab:
    st.subheader('① Find possible crypto trades')
    st.write('Checks momentum, trading activity and breakouts. Not a prediction or news signal.')
    selected=st.multiselect('Coins to scan',list(COINS),default=['BTC','ETH','SOL','XRP','DOGE','SUI','HBAR','ZEC'])
    if 'HYPE' in selected:st.info('HYPE: no verified candle feed; price-watch only.')
    if st.button('🚀 SCAN CRYPTO FOR ME',type='primary',use_container_width=True):
        results=[];errors=[]
        with st.spinner('Checking completed 15-minute candles…'):
            for sym in selected:
                if not COINS[sym][1]:errors.append(f'{sym}: No verified candle feed.');continue
                try:
                    if sym not in quotes:raise ValueError('No fresh quote')
                    px,ts,source=quotes[sym]
                    if not quote_is_fresh(ts):raise ValueError('Quote older than five minutes')
                    results.append(indicators(sym,px))
                except Exception as e:errors.append(f'{sym}: {str(e)[:140]}')
                time.sleep(.15)
        st.session_state.crypto_results=sorted(results,key=lambda r:(r['score'],r['vol']),reverse=True)
        st.session_state.crypto_errors=errors
        sent=0
        for r in results:
            if r['score']==4:
                sym=r['symbol']
                message=(f'🟢 Crypto Market Pulse: {sym} research candidate (4/4)\n'
                         f'Price {dual(r["price"],fx)}\n'
                         f'RSI {r["rsi"]:.1f} • volume {r["vol"]:.2f}x\n'
                         f'Illustrative entry {dual(r["entry"],fx)} • stop {dual(r["stop"],fx)} • target {dual(r["target"],fx)}\n'
                         'Paper research only. Not a live order.')
                ok,msg=alert_once(f'signal:{sym}:{r["last"]}',message)
                if ok:sent+=1
                elif 'Already notified' not in msg:st.warning(f'{sym}: {msg}')
        if sent:st.success(f'📨 Sent {sent} Telegram signal alert(s).')
    if 'crypto_results' in st.session_state:
        st.subheader('🤖 One-click automatic paper selection')
        st.caption('Runs only when pressed, not unattended.')
        paper_budget=st.number_input('Automatic paper-trade budget (AUD)',min_value=1.,max_value=float(max_position),value=min(25.,float(max_position)),step=5.)
        if st.button('🤖 SELECT BEST & PAPER TRADE',type='primary',use_container_width=True):
            eligible=[r for r in st.session_state.crypto_results if r['score']==4 and r['symbol'] not in pos]
            if not eligible:st.warning('NO TRADE: No new coin passed all four checks.')
            else:
                best=eligible[0];sym=best['symbol']
                try:
                    px,ts,_=quotes[sym]
                    if not quote_is_fresh(ts):raise ValueError('Stale quote; refresh first')
                    fee=fee_usd(paper_budget/fx,exchange,'taker')*fx
                    allowed,reason=can_buy(sym,pos,paper_budget,cash,daily,max_position,max_open,max_daily_loss,fee)
                    if not allowed:raise ValueError(reason)
                    fillpx=px*(1+slippage_pct/100);qty=(paper_budget/fx)/fillpx
                    if not (0<best['stop']<fillpx<best['target']):raise ValueError('Illustrative stop/target no longer valid')
                    save_fill(sym,'BUY',qty,fillpx,fx,fee,'One-click ranked paper candidate',plan=(best['stop'],best['target']))
                    st.success(f'PAPER BUY {sym}: {qty:.7f} at {dual(fillpx,fx)}. Exits are checked manually.')
                    ok,msg=telegram_send(f'🧪 PAPER BUY {sym}: {qty:.7f} at {dual(fillpx,fx)}. Not a real order.')
                    if not ok:st.warning(f'Telegram: {msg}')
                    st.caption('Refresh the page to update the portfolio summary.')
                except Exception as e:st.error(f'NO TRADE: {e}')
        candidates=[r for r in st.session_state.crypto_results if r['score']==4]
        st.subheader('🏆 Ranked trade candidates')
        if candidates:st.success(f"Top research candidate: {candidates[0]['symbol']} — score 4/4. Not a buy instruction.")
        else:st.warning('NO TRADE: No selected coin meets all four momentum rules.')
        for err in st.session_state.get('crypto_errors',[]):st.warning(err)
        for r in st.session_state.crypto_results:
            sym=r['symbol'];label='🟢 Research candidate' if r['score']==4 else '🟠 Watch' if r['score']>=2 else '🔴 Weak setup'
            with st.expander(f"{label} · {sym} · {dual(r['price'],fx)}"):
                st.write(f"**{COINS[sym][0]} — technical score {r['score']}/4.**")
                st.write(f"RSI: **{r['rsi']:.1f}** · Volume: **{r['vol']:.2f}×**")
                st.write(f"**Illustrative entry:** {dual(r['entry'],fx)}")
                st.write(f"**Illustrative stop:** {dual(r['stop'],fx)}")
                st.write(f"**Illustrative target:** {dual(r['target'],fx)}")
                n=100.;buyfee=fee_usd(n,exchange,order_type);sellfee=fee_usd(n,exchange,'taker')
                risk=(r['entry']-r['stop'])/r['entry']*n+buyfee+sellfee+2*n*slippage_pct/100
                reward=(r['target']-r['entry'])/r['entry']*n-buyfee-sellfee-2*n*slippage_pct/100
                st.caption(f'Illustrative net reward/risk after modelled fees and slippage: {reward/risk:.2f}:1' if risk>0 and reward>0 else 'Illustrative net reward/risk: unfavourable after costs')
                st.caption(f'Example A${n*fx:.2f} (US${n:.2f}) round-trip fee and slippage assumptions; spread excluded. Levels are illustrative, not validated.')
                st.caption(f"Last completed candle started {r['last']} UTC. Signal rejects >5% quote/candle discrepancy.")
                if COINS[sym][1]:st.link_button('📈 Open TradingView chart',f'https://www.tradingview.com/chart/?symbol=KRAKEN%3A{COINS[sym][1]}')
    st.divider();st.subheader('🌐 Global crypto price board')
    board=[]
    for sym in COINS:
        if sym in quotes:
            px,ts,source=quotes[sym]
            board.append({'Coin':sym,'AUD':px*fx,'USD':px,'Source':source,'Quote age (sec)':round((datetime.now(timezone.utc)-utc_time(ts)).total_seconds())})
        else:board.append({'Coin':sym,'AUD':None,'USD':None,'Source':'No fresh quote','Quote age (sec)':None})
    st.dataframe(pd.DataFrame(board),hide_index=True,use_container_width=True)
    if st.button('🔄 Refresh spot prices'):
        coingecko_spot.clear();kraken_tickers.clear();st.rerun()

with trade_tab:
    st.subheader('💼 Paper trading')
    st.caption(f'Cash A${cash:,.2f} · {len(pos)} open position(s) · {len(fills)} recorded trade(s)')
    st.warning('Stops and targets are checked ONLY when you press the button. No automatic overnight protection.')
    with st.form('paper_buy'):
        sym=st.selectbox('Coin',list(COINS));side=st.radio('Action',['BUY','SELL'],horizontal=True)
        amount=st.number_input('Trade value (AUD, before fees)',min_value=1.,value=25.,step=10.)
        st.caption(f'A${amount:,.2f} (US${amount/fx:,.2f}) before fees. {exchange}, {order_type.lower()} order.')
        note=st.text_input('Reason for trade',value='Learning exercise')
        submitted=st.form_submit_button('Save paper trade',type='primary')
    if submitted:
        try:
            if sym not in quotes:raise ValueError('No fresh quote available')
            px,ts,_=quotes[sym]
            if not quote_is_fresh(ts):raise ValueError('Quote older than five minutes')
            fee=fee_usd(amount/fx,exchange,order_type)*fx
            if side=='BUY':
                allowed,reason=can_buy(sym,pos,amount,cash,daily,max_position,max_open,max_daily_loss,fee)
                if not allowed:raise ValueError(reason)
            fillpx=px*(1+slippage_pct/100 if side=='BUY' else 1-slippage_pct/100)
            qty=(amount/fx)/fillpx
            if side=='SELL':
                available=pos.get(sym,0)
                if available<=0:raise ValueError('No open position to sell')
                if qty>available+1e-8:raise ValueError(f'You only hold {available:.8f} paper {sym}')
            save_fill(sym,side,qty,fillpx,fx,fee,note)
            st.success(f'Simulated {side}: {qty:.7f} {sym} at {dual(fillpx,fx)}. Your updated balance will appear on the next refresh.')
        except Exception as e:st.error(str(e))
    st.divider();st.subheader('🛡️ Stop loss and profit target')
    if pos:
        with st.form('plan'):
            sym2=st.selectbox('Open position',list(pos))
            px=quotes[sym2][0] if sym2 in quotes else 1.
            stop=st.number_input('Stop-loss USD',min_value=.000001,value=max(.000001,float(round(px*.97,6))),format='%.6f')
            target=st.number_input('Profit target USD',min_value=.000001,value=max(.000001,float(round(px*1.06,6))),format='%.6f')
            save=st.form_submit_button('Save paper stop and target')
        if save:
            if sym2 not in quotes or not quote_is_fresh(quotes[sym2][1]):st.error('Fresh quote required to save plan.')
            elif not (0<stop<quotes[sym2][0]<target):st.error('Stop must be below current price and target above it.')
            else:
                now=now_iso()
                with connect() as c:
                    # A replacement plan must never backtest the candle already in progress.
                    c.execute('INSERT INTO plans(symbol,stop,target,checked,state,entry_at) VALUES(?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET stop=excluded.stop,target=excluded.target,checked=excluded.checked,state=excluded.state,entry_at=excluded.entry_at',(sym2,stop,target,now,'ACTIVE',now))
                st.success('Plan saved. Only future completed candles will be evaluated.')
    else:st.info('Make a paper BUY first to set a stop and target.')
    if st.button('🔄 CHECK MY PAPER STOPS AND TARGETS NOW',use_container_width=True):
        for msg in check_plans(fx,exchange,slippage_pct,quotes):st.info(msg)
        st.caption('Results are shown here without jumping to the top. Refresh to update the portfolio summary after an exit.')
    with connect() as c:plans=pd.read_sql_query('SELECT * FROM plans',c)
    if not plans.empty:
        with st.expander('View saved stop/target plans'):
            st.dataframe(plans,hide_index=True,use_container_width=True)
    st.divider();st.subheader('Your paper trades')
    fills=get_fills()
    if fills.empty:st.info('No paper trades yet.')
    else:
        with st.expander('View full transaction ledger'):
            st.dataframe(fills,hide_index=True,use_container_width=True)
    st.download_button('⬇️ Back up trades (CSV)',fills.to_csv(index=False).encode(),'crypto_paper_trades.csv','text/csv')
    with st.expander('⬆️ Restore trades from CSV backup'):
        st.warning('Restoring replaces the entire paper-trade ledger and clears plans. Back up your current ledger first.')
        upload=st.file_uploader('Select exported crypto_paper_trades.csv',type=['csv'])
        confirm=st.checkbox('I understand that restore replaces my current paper trades')
        if st.button('Restore validated CSV',disabled=upload is None or not confirm):
            try:
                incoming=pd.read_csv(upload)
                required=['id','ts','symbol','side','qty','price','fx','fee_aud','note']
                if list(incoming.columns)!=required:raise ValueError('CSV columns do not match the backup format')
                if incoming['id'].isna().any() or incoming['id'].duplicated().any():raise ValueError('Duplicate or missing fill IDs')
                if incoming[required[:-1]].isna().any().any():raise ValueError('Missing required values')
                if not incoming['side'].isin(['BUY','SELL']).all() or not incoming['symbol'].isin(COINS).all():raise ValueError('Invalid trade side or symbol')
                for field in ['id','qty','price','fx','fee_aud']:incoming[field]=pd.to_numeric(incoming[field],errors='raise')
                if not (incoming['id']>0).all() or not (incoming['id']%1==0).all():raise ValueError('Invalid IDs')
                if not (incoming[['qty','price','fx']]>0).all().all() or not (incoming['fee_aud']>=0).all():raise ValueError('Invalid trade amounts')
                for stamp in incoming['ts']:utc_time(stamp)
                test_pos={}
                for r in incoming.sort_values('id').itertuples():
                    test_pos[r.symbol]=test_pos.get(r.symbol,0)+(r.qty if r.side=='BUY' else -r.qty)
                    if test_pos[r.symbol]<-1e-8:raise ValueError('CSV contains sales larger than holdings')
                with connect() as c:
                    c.execute('BEGIN IMMEDIATE');c.execute('DELETE FROM plans');c.execute('DELETE FROM fills')
                    c.executemany('INSERT INTO fills(id,ts,symbol,side,qty,price,fx,fee_aud,note) VALUES(?,?,?,?,?,?,?,?,?)',incoming[required].itertuples(index=False,name=None))
                flash('success',f'Restored {len(incoming)} trades. Plans were cleared; recreate them for open positions.')
                st.rerun()
            except Exception as e:st.error(f'Restore cancelled; existing ledger unchanged: {e}')
    st.caption('Free hosting can reset local SQLite. Download backups frequently; restore does not recreate plans.')

with learn_tab:
    st.subheader('Beginner guide')
    st.write('**Scan:** Searches historical price patterns, not guaranteed predictions.')
    st.write('**Stop/target:** Illustrative levels. The manual checker uses only completed 15-minute candles starting after the plan was saved. If both are touched, the stop wins conservatively.')
    st.write('**Paper trade:** A simulated transaction only. No exchange connection or background execution.')
    st.write('**HYPE:** Price-watch only until a verified candle feed is configured.')
    st.write('**FX:** AUD conversion uses dated Frankfurter/ECB reference rates, not live bank rates.')
    st.write('**Market data:** Kraken ticker preferred where the returned symbol matches a verified key; CoinGecko fresh-price fallback. Kraken 15-minute candles are used for signals.')
    st.write('**Fees:** Illustrative assumptions, not verified current fee schedules. Spread is excluded.')
    st.write('**Safety:** Spot-only paper practice; no leverage, wallets, exchange trading keys or real orders.')
