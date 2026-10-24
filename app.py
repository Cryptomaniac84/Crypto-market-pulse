import os, hmac, sqlite3, time
from datetime import datetime, timezone
import pandas as pd
import requests
import streamlit as st
from pathlib import Path

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
</style>''', unsafe_allow_html=True)

pwd=os.getenv('SCANNER_PASSWORD','')
try:pwd=st.secrets.get('SCANNER_PASSWORD',pwd)
except Exception:pass
if not pwd:
 st.error('Set SCANNER_PASSWORD in Streamlit app secrets before using this app. This protects your paper ledger.');st.stop()
if not st.session_state.get('unlocked',False):
 st.title('₿ Crypto Market Pulse')
 p=st.text_input('Your scanner password',type='password')
 if st.button('Unlock',type='primary'):
  if hmac.compare_digest(p,str(pwd)):st.session_state.unlocked=True;st.rerun()
  else:st.error('Wrong password')
 st.stop()

COINS={
 'BTC':('Bitcoin','XXBTZUSD','bitcoin'),'ETH':('Ethereum','XETHZUSD','ethereum'),
 'SOL':('Solana','SOLUSD','solana'),'XRP':('XRP','XRPUSD','ripple'),
 'DOGE':('Dogecoin','XDGUSD','dogecoin'),'ADA':('Cardano','ADAUSD','cardano'),
 'LINK':('Chainlink','LINKUSD','chainlink'),'AVAX':('Avalanche','AVAXUSD','avalanche-2'),
 'SUI':('Sui','SUIUSD','sui'),'HBAR':('Hedera','HBARUSD','hedera-hashgraph'),
 'HYPE':('Hyperliquid',None,'hyperliquid'),'ZEC':('Zcash','XZECZUSD','zcash')}
DB=os.getenv('DB_PATH','crypto_paper.sqlite3')
EXCHANGES={'Coinbase Advanced':{'maker':0.0009,'taker':0.0010},'CoinSpot Markets':{'maker':0.001,'taker':0.001},'CoinSpot Instant':{'maker':0.01,'taker':0.01}}
# Indicative fee rates only; compare order preview and actual exchange prices before any real trading.


def secret(name):
 try:return str(st.secrets.get(name,'')).strip()
 except Exception:return ''

def telegram_send(message):
 token=secret('TELEGRAM_BOT_TOKEN')
 chat=secret('TELEGRAM_CHAT_ID')
 if not token or not chat:return False,'Telegram is not configured in Streamlit secrets.'
 try:
  r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                  json={'chat_id':chat,'text':message,'disable_web_page_preview':True},timeout=10)
  payload=r.json()
  if r.ok and payload.get('ok'):return True,'Message sent to Telegram.'
  return False,f"Telegram rejected the message (HTTP {r.status_code}): {str(payload.get('description','Unknown error'))[:120]}"
 except Exception as e:return False,f'Telegram request failed: {type(e).__name__}'

def alert_once(key,message):
 # Persistent only as long as Streamlit's local SQLite file survives.
 with connect() as c:
  c.execute('CREATE TABLE IF NOT EXISTS telegram_alerts(key TEXT PRIMARY KEY, sent_at TEXT)')
  if c.execute('SELECT 1 FROM telegram_alerts WHERE key=?',(key,)).fetchone():
   return False,'Already notified for this candle.'
  ok,msg=telegram_send(message)
  if ok:c.execute('INSERT INTO telegram_alerts(key,sent_at) VALUES(?,?)',(key,datetime.now(timezone.utc).isoformat()))
  return ok,msg

def connect():
 c=sqlite3.connect(DB,timeout=15)
 c.execute('CREATE TABLE IF NOT EXISTS fills(id INTEGER PRIMARY KEY AUTOINCREMENT,ts TEXT,symbol TEXT,side TEXT,qty REAL,price REAL,fx REAL,fee_aud REAL,note TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS plans(symbol TEXT PRIMARY KEY,stop REAL,target REAL,checked TEXT,state TEXT)')
 return c

def get_fills():
 with connect() as c:return pd.read_sql_query('SELECT * FROM fills ORDER BY id',c)

def positions():
 out={}
 for r in get_fills().itertuples():out[r.symbol]=out.get(r.symbol,0)+(r.qty if r.side=='BUY' else -r.qty)
 return {k:v for k,v in out.items() if v>1e-8}

def balances(fx):
 cash=500.;cost=0.
 for r in get_fills().itertuples():
  amount=r.qty*r.price*r.fx
  cash+=(-amount if r.side=='BUY' else amount)-r.fee_aud
  cost+=r.fee_aud
 return cash,cost

@st.cache_data(ttl=60,show_spinner=False)
def spot():
 ids=','.join(v[2] for v in COINS.values())
 resp=requests.get('https://api.coingecko.com/api/v3/simple/price',params={'ids':ids,'vs_currencies':'usd,aud','include_last_updated_at':'true'},timeout=12)
 resp.raise_for_status();return resp.json()

@st.cache_data(ttl=120,show_spinner=False)
def candles(symbol,interval=15):
 pair=COINS[symbol][1]
 if pair is None: raise ValueError('No verified Kraken candle pair; HYPE remains price-watch only until a compatible candle feed is connected')
 resp=requests.get('https://api.kraken.com/0/public/OHLC',params={'pair':pair,'interval':interval},timeout=12)
 resp.raise_for_status();payload=resp.json()
 if payload.get('error'):raise ValueError('; '.join(payload['error']))
 data=next(v for k,v in payload['result'].items() if k!='last')
 frame=pd.DataFrame(data,columns=['ts','open','high','low','close','vwap','volume','count'])
 for k in ['open','high','low','close','volume']:frame[k]=pd.to_numeric(frame[k])
 frame['ts']=pd.to_datetime(frame['ts'],unit='s',utc=True)
 return frame

def indicators(symbol,price):
 d=candles(symbol)
 # Kraken includes the unfinished candle; don't score it.
 d=d.iloc[:-1].copy()
 if len(d)<55:raise ValueError('Not enough completed price candles')
 close=d.close;ema20=close.ewm(span=20,adjust=False).mean().iloc[-1]
 delta=close.diff();gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean();loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
 rs=gain.iloc[-1]/max(loss.iloc[-1],1e-10);rsi=100-100/(1+rs)
 vol=float(d.volume.iloc[-1]/max(d.volume.iloc[-21:-1].mean(),1e-9))
 resistance=float(d.high.iloc[-21:-1].max());support=float(d.low.iloc[-11:-1].min())
 score=sum([price>ema20,50<=rsi<=72,vol>=1.3,price>resistance])
 entry=max(price,resistance)*1.001
 stop=min(support,entry*.985)
 if stop<=0 or stop>=entry:stop=entry*.975
 target=entry+2*(entry-stop)
 return {'symbol':symbol,'price':price,'score':score,'rsi':rsi,'vol':vol,'entry':entry,'stop':stop,'target':target,'last':str(d.ts.iloc[-1]),'breakout':price>resistance}

def fmt(x):return f'{x:,.2f}' if x>=1 else f'{x:,.5f}'
def dual(x,fx):return f'US${fmt(x)} (A${fmt(x*fx)})'
def fee_usd(notional_usd,exchange,order_type):return notional_usd*EXCHANGES[exchange][order_type.lower()]


def save_fill(sym,side,qty,price,fx,fee,note):
 with connect() as c:c.execute('INSERT INTO fills(ts,symbol,side,qty,price,fx,fee_aud,note) VALUES(?,?,?,?,?,?,?,?)',(datetime.now(timezone.utc).isoformat(),sym,side,qty,price,fx,fee,note))

def check_plans(fx,exchange,slippage_pct):
 msgs=[];pos=positions()
 with connect() as c:plans=c.execute("SELECT symbol,stop,target,checked FROM plans WHERE state='ACTIVE'").fetchall()
 for sym,stop,target,checked in plans:
  if pos.get(sym,0)<=0:continue
  try:
   bars=candles(sym)
   # only completed candles, after plan was saved/last checked
   bars=bars.iloc[:-1].copy()
   cursor=pd.Timestamp(checked)
   bars=bars[bars.ts+pd.Timedelta(minutes=15)>cursor]
   if bars.empty:msgs.append(f'{sym}: No new completed candles.');continue
   triggered=None
   for r in bars.itertuples():
    # conservative rule: if both touched in one bar, stop wins
    if r.low<=stop:triggered=('STOP',min(float(r.open),stop));break
    if r.high>=target:triggered=('TARGET',target if r.open<target else float(r.open));break
   if triggered:
    kind,px=triggered;qty=pos[sym]
    px=px*(1-slippage_pct/100)
    fee=fee_usd(qty*px,exchange,'taker')*fx
    save_fill(sym,'SELL',qty,px,fx,fee,f'Paper {kind}; {exchange} taker; {slippage_pct:.2f}% slippage (15m historical candle simulation)')
    with connect() as c:c.execute("UPDATE plans SET state='CLOSED',checked=? WHERE symbol=?",(datetime.now(timezone.utc).isoformat(),sym))
    msgs.append(f'{sym}: PAPER {kind} — simulated exit {qty:.6g} at US${fmt(px)}. Not a real order.')
   else:
    with connect() as c:c.execute('UPDATE plans SET checked=? WHERE symbol=?',(str(bars.ts.iloc[-1]+pd.Timedelta(minutes=15)),sym))
    msgs.append(f'{sym}: No stop or target crossed in completed candles.')
  except Exception as e:msgs.append(f'{sym}: Could not check — {e}. No exit recorded.')
 return msgs

# Functional dashboard: only the decorative header image is shown, never a fake dashboard screenshot.
banner=Path(__file__).parent/'crypto_banner.jpg'
st.markdown('<div class="hero"><h1>₿ CRYPTO MARKET PULSE</h1><p>Rebound scanner • Coinbase fee modelling • Paper trading • Telegram alerts • USD (AUD)</p></div>',unsafe_allow_html=True)
if banner.exists():st.image(str(banner),use_container_width=True)
st.caption('PAPER TRADING ONLY • No exchange connection • No background monitoring • Quotes and data can be delayed')
try:
 prices=spot();btc=prices['bitcoin'];fx=btc['aud']/btc['usd']
 if fx<=0:raise ValueError('FX unavailable')
except Exception as e:
 st.error(f'Price service unavailable: {e}. Try again shortly.');st.stop()

cash,fees=balances(fx)
cols=st.columns(3)
cols[0].metric('Paper cash',f'A${cash:,.2f}')
cols[1].metric('Open coins',len(positions()))
cols[2].metric('Trades recorded',len(get_fills()))
st.caption(f'Indicative conversion: US$1 ≈ A${fx:.4f} (derived from BTC spot quotes, not a bank FX rate).')
st.info('👋 New to trading? Start with **Scan crypto**, open a result and read its explanation. You can practise with part of your A$500 — no real crypto is bought.')
st.subheader('⚙️ Trading assumptions')
a,b,c=st.columns(3)
with a: exchange=st.selectbox('Fee schedule',list(EXCHANGES),index=0)
with b: order_type=st.selectbox('Paper order type',['Taker','Maker'],index=0,help='Taker executes immediately; maker rests on the order book. Stop exits always model taker fees.')
with c: slippage_pct=st.number_input('Estimated slippage (%)',min_value=0.0,max_value=5.0,value=0.10,step=0.05)
st.caption(f'{exchange}: maker {EXCHANGES[exchange]["maker"]*100:.2f}% · taker {EXCHANGES[exchange]["taker"]*100:.2f}%. Indicative fees; not connected to the exchange. Paper trades use displayed spot quotes, not exchange execution prices.')

with st.expander('📨 Telegram alert setup and test'):
 st.write('Telegram alerts are sent only when you run a scan or paper-trade action. The Streamlit app does NOT monitor markets in the background.')
 if secret('TELEGRAM_BOT_TOKEN') and secret('TELEGRAM_CHAT_ID'):
  st.success('Telegram credentials detected in Streamlit secrets.')
  if st.button('📨 Send test Telegram alert'):
   ok,msg=telegram_send('🧪 Crypto Market Pulse: Telegram connection working. Paper mode only.')
   (st.success if ok else st.error)(msg)
 else:
  st.warning('Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your crypto app Streamlit secrets. Never put the bot token in GitHub.')
 st.caption('A green research candidate (score 4/4) triggers a Telegram message when you press SCAN. Repeat scans of the same completed candle are deduplicated.')

scan_tab,trade_tab,learn_tab=st.tabs(['🎯 Scan crypto','💼 Paper trading','❓ How it works'])
with scan_tab:
 st.subheader('① Find possible crypto trades')
 st.write('Checks a small list of established coins for upward momentum, increased trading activity and a recent price breakout. This is **not** a prediction or a verified news signal.')
 selected=st.multiselect('Coins to scan',list(COINS),default=['BTC','ETH','SOL','XRP','DOGE','SUI','HBAR','HYPE','ZEC'])
 if st.button('🚀 SCAN CRYPTO FOR ME',type='primary',use_container_width=True):
  results=[];errors=[]
  with st.spinner('Checking completed 15-minute candles…'):
   for sym in selected:
    try:
     coin=prices[COINS[sym][2]]
     age=time.time()-coin.get('last_updated_at',0)
     if age>300:errors.append(f'{sym}: spot quote is older than five minutes; excluded.');continue
     results.append(indicators(sym,float(coin['usd'])))
    except Exception as e:errors.append(f'{sym}: {str(e)[:120]}')
  st.session_state.crypto_results=sorted(results,key=lambda r:(r['score'],r['vol']),reverse=True)
  st.session_state.crypto_errors=errors
  sent=0;alert_errors=[]
  for r in st.session_state.crypto_results:
   if r['score']==4:
    sym=r['symbol']
    message=(f'🟢 Crypto Market Pulse: {sym} research candidate (4/4)\\n'
             f'Price US${fmt(r["price"])} (A${fmt(r["price"]*fx)})\\n'
             f'RSI {r["rsi"]:.1f} • volume {r["vol"]:.2f}x\\n'
             f'Illustrative entry US${fmt(r["entry"])} • stop US${fmt(r["stop"])} • target US${fmt(r["target"])}\\n'
             'Paper research signal only. Not a guaranteed rebound or live order.')
    ok,msg=alert_once(f'signal:{sym}:{r["last"]}',message)
    if ok:sent+=1
    elif 'Already notified' not in msg:alert_errors.append(f'{sym}: {msg}')
  if sent:st.success(f'📨 Sent {sent} Telegram signal alert(s).')
  for err in alert_errors:st.warning(err)
 if 'crypto_results' in st.session_state:
  st.subheader('🤖 One-click automatic paper selection')
  st.caption('Selects the highest-ranked qualifying coin and simulates a BUY with a stop and target. Runs only when pressed — not unattended.')
  paper_budget=st.number_input('Automatic paper-trade budget (USD)',min_value=5.,max_value=100.,value=25.,step=5.,key='auto_budget')
  if st.button('🤖 SELECT BEST & PAPER TRADE',type='primary',use_container_width=True):
   eligible=[r for r in st.session_state.crypto_results if r['score']==4 and r['symbol'] not in positions()]
   if not eligible:st.warning('NO TRADE: No new coin passed all four technical checks.')
   else:
    best=eligible[0];sym=best['symbol'];age=time.time()-prices[COINS[sym][2]].get('last_updated_at',0)
    px=float(prices[COINS[sym][2]]['usd'])
    if age>300:st.error('Quote is stale. Refresh and scan again.')
    elif cash < paper_budget*fx*(1+EXCHANGES[exchange]['taker']):st.error('Not enough paper cash.')
    else:
     fillpx=px*(1+slippage_pct/100)
     qty=paper_budget/fillpx
     fee=fee_usd(paper_budget,exchange,'taker')*fx
     stop=best['stop'];target=best['target']
     if not (0<stop<fillpx<target):st.warning('NO TRADE: The planned stop/target is no longer valid at the current quote.')
     else:
      save_fill(sym,'BUY',qty,fillpx,fx,fee,'One-click ranked paper candidate; NOT live')
      with connect() as c:c.execute('INSERT OR REPLACE INTO plans(symbol,stop,target,checked,state) VALUES(?,?,?,?,?)',(sym,stop,target,datetime.now(timezone.utc).isoformat(),'ACTIVE'))
      st.success(f'PAPER BUY {sym}: {qty:.7f} units at {dual(fillpx,fx)}. Stop {dual(stop,fx)}; target {dual(target,fx)}. Exits are NOT continuously monitored.')
      ok,msg=telegram_send(f'🧪 PAPER BUY {sym}: {qty:.7f} at US${fmt(fillpx)}. Stop US${fmt(stop)}; target US${fmt(target)}. Not a real order.')
      if not ok:st.warning(f'Telegram: {msg}')

      st.rerun()
  candidates=[r for r in st.session_state.crypto_results if r['score']==4]
  st.subheader('🏆 Ranked trade candidates')
  if candidates:
   best=candidates[0]
   st.success(f"Top research candidate: {best['symbol']} — score {best['score']}/4, volume {best['vol']:.2f}×. This is NOT an instruction to buy.")
   st.caption('The scanner ranks only selected coins. A score is a basic chart filter, not a proven profitable strategy.')
  else:
   st.warning('NO TRADE: None of the selected coins meets all four momentum rules. Waiting is a valid decision.')
  for err in st.session_state.get('crypto_errors',[]):st.warning(err)
  for r in st.session_state.crypto_results:
   sym=r['symbol'];label='🟢 Research candidate' if r['score']==4 else '🟠 Watch' if r['score']>=2 else '🔴 Weak setup'
   with st.expander(f"{label} · {sym} · US${fmt(r['price'])} (A${fmt(r['price']*fx)})"):
    st.write(f"**{COINS[sym][0]} — technical score {r['score']}/4.**")
    st.write(f"Momentum (RSI): **{r['rsi']:.1f}** · Volume: **{r['vol']:.2f}×** usual recent 15-minute volume.")
    st.write('A high score means the recent chart matches more of our rules. It does **not** mean the coin is safe to buy.')
    st.write(f"**Illustrative buy level:** US${fmt(r['entry'])} (A${fmt(r['entry']*fx)})")
    st.write(f"**Illustrative stop:** US${fmt(r['stop'])} (A${fmt(r['stop']*fx)})")
    st.write(f"**Illustrative profit target:** US${fmt(r['target'])} (A${fmt(r['target']*fx)})")
    risk=r['entry']-r['stop']
    notional=100.0
    roundtrip=fee_usd(notional,exchange,order_type)+fee_usd(notional,exchange,'taker')+2*notional*slippage_pct/100
    st.caption(f'Example US$100 round-trip estimated trading fees + slippage: US${roundtrip:.2f} (A${roundtrip*fx:.2f}), excluding spread and price movement.')
    st.caption('Target is a mathematical 2:1 illustration, NOT a validated reachable price. Quotes can differ between providers.')
    st.caption(f"Last completed candle started {r['last']} UTC. Kraken historical candles and CoinGecko spot may differ.")
    if COINS[sym][1]:st.link_button('📈 Open TradingView chart',f'https://www.tradingview.com/chart/?symbol=KRAKEN%3A{COINS[sym][1]}')
 st.divider()
 st.subheader('🌐 Global crypto price board')
 board=[]
 for sym,(name,pair,cid) in COINS.items():
  if cid in prices:
   q=prices[cid];board.append({'Coin':sym,'USD':q.get('usd'),'AUD':q.get('aud'),'Updated UTC':datetime.fromtimestamp(q.get('last_updated_at',0),timezone.utc).strftime('%d %b %H:%M')})
 st.dataframe(pd.DataFrame(board),hide_index=True,use_container_width=True)
 if st.button('🔄 Refresh spot prices'):st.cache_data.clear();st.rerun()

with trade_tab:
 st.subheader('② Your practice account — USD first')
 st.warning('Stops and targets are checked **only when you press the button below**. This is NOT automatic overnight protection. No real exchange orders are sent.')
 st.write('Your starting balance is A$500, shown alongside its USD equivalent. You do not need to use the whole balance.')
 with st.form('paper_buy'):
  sym=st.selectbox('Coin',list(COINS))
  side=st.radio('Action',['BUY','SELL'],horizontal=True)
  amount=st.number_input('Trade value (USD, before fees)',min_value=1.,value=25.,step=10.)
  st.caption(f'US${amount:,.2f} (A${amount*fx:,.2f}) before fees. Exchange: {exchange}, {order_type.lower()} order.')
  note=st.text_input('Reason for trade',value='Learning exercise')
  submitted=st.form_submit_button('Save paper trade',type='primary')
 if submitted:
  px=float(prices[COINS[sym][2]]['usd']);qty=amount/px
  fee=fee_usd(amount,exchange,order_type)*fx
  # Conservative paper slippage: pay more to buy and receive less to sell.
  px=px*(1+slippage_pct/100 if side=='BUY' else 1-slippage_pct/100)
  qty=amount/px
  available=positions().get(sym,0)
  if side=='BUY' and cash<amount*fx+fee:st.error('Not enough paper cash.')
  elif side=='SELL' and qty>available+1e-8:st.error(f'You only own {available:.8f} paper {sym}.')
  else:
   save_fill(sym,side,qty,px,fx,fee,note);st.success(f'Simulated {side}: {qty:.7f} {sym} at US${fmt(px)} (A${fmt(px*fx)}).');st.rerun()
 st.divider()
 st.subheader('③ Attach practice stop and target')
 pos=positions()
 if pos:
  with st.form('plan'):
   sym2=st.selectbox('Open position',list(pos))
   px=float(prices[COINS[sym2][2]]['usd'])
   stop=st.number_input('Stop-loss USD (sell if price falls here)',min_value=0.000001,value=float(round(px*.97,6)),format='%.6f')
   target=st.number_input('Profit target USD (sell if price rises here)',min_value=0.000001,value=float(round(px*1.06,6)),format='%.6f')
   save=st.form_submit_button('Save paper stop and target')
  if save:
   if stop>=target:st.error('Stop must be lower than target.')
   else:
    now=datetime.now(timezone.utc).isoformat()
    with connect() as c:c.execute('INSERT OR REPLACE INTO plans(symbol,stop,target,checked,state) VALUES(?,?,?,?,?)',(sym2,stop,target,now,'ACTIVE'))
    st.success('Practice stop/target saved. Remember: it will NOT run until you check.')
 else:st.info('Make a paper BUY first to set a stop and target.')
 if st.button('🔄 CHECK MY PAPER STOPS AND TARGETS NOW',use_container_width=True):
  for msg in check_plans(fx,exchange,slippage_pct):st.info(msg)
  st.caption('After an exit, refresh the page to see the updated cash and positions.')
 with connect() as c:plans=pd.read_sql_query('SELECT * FROM plans',c)
 if not plans.empty:st.dataframe(plans,hide_index=True,use_container_width=True)
 st.divider()
 st.subheader('Your paper trades')
 fills=get_fills()
 if fills.empty:st.info('No paper trades yet.')
 else:
  st.dataframe(fills,hide_index=True,use_container_width=True)
  st.download_button('⬇️ Back up trades (CSV)',fills.to_csv(index=False).encode(),'crypto_paper_trades.csv','text/csv')
  st.caption('Streamlit free hosting can reset this database. Download your CSV regularly; this version does not restore CSV automatically. Changing exchange fee assumptions does not retroactively alter recorded trades.')

with learn_tab:
 st.subheader('Beginner guide')
 st.write('**Scan**: Looks for possible setups using past price movement, not insider information or guaranteed predictions.')
 st.write('**Buy level**: A price you might consider buying at. **Stop-loss**: Your intended exit if price falls. **Target**: Your hoped-for selling price.')
 st.write('**RSI**: A measure of recent buying strength. **Volume**: How much crypto was traded during a period.')
 st.write('**Paper trade**: A pretend transaction that changes only your practice account.')
 st.write('**Important**: Crypto trades around the clock, but this Streamlit app does not. An always-on worker, reliable exchange market data, persistent database and alert service are needed before unattended paper trading can be trusted.')
 st.write('**Safety**: This version is spot-only practice, no leverage, no wallet connections, no exchange API keys, no real orders.')
 st.write('**HYPE and ZEC**: Added to the price board and scan selection. HYPE technical signals require a compatible verified candle feed; Coinbase Australia spot availability must be confirmed before live trading. ZEC candle data may also be unavailable depending on Kraken product support.')
 st.write('**Coinbase**: Coinbase Advanced Intro spot fee assumptions are maker 0.09%, taker 0.10%, based on your fee-tier screenshots. Always verify rates in Coinbase before real trading.')
 st.write('**Market data**: This version uses CoinGecko spot prices (cached 60 seconds) and Kraken historical 15-minute candles. CoinMarketCap API and Coinbase exchange feeds are not connected yet; instant prices and unattended trading are NOT available.')
