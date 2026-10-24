"""Pure risk and candle logic for Crypto Market Pulse."""
from datetime import datetime, timezone
import pandas as pd

MAX_QUOTE_AGE = 300

def utc_time(value):
    t = pd.Timestamp(value)
    return t.tz_localize('UTC') if t.tzinfo is None else t.tz_convert('UTC')

def quote_is_fresh(timestamp, now=None, limit=MAX_QUOTE_AGE):
    if timestamp is None: return False
    now = now or datetime.now(timezone.utc)
    age = (utc_time(now) - utc_time(timestamp)).total_seconds()
    return 0 <= age <= limit

def can_buy(symbol, positions, amount_aud, cash_aud, daily_realized_aud,
            max_position_aud=50., max_open=3, max_daily_loss_aud=25., fee_aud=0.):
    if positions.get(symbol, 0) > 1e-8: return False, 'An open position already exists for this coin.'
    if len(positions) >= max_open: return False, 'Maximum open positions reached.'
    if daily_realized_aud <= -max_daily_loss_aud: return False, 'Daily realised loss limit reached.'
    if amount_aud <= 0 or amount_aud > max_position_aud + 1e-8: return False, 'Position exceeds the configured A$ limit.'
    if cash_aud + 1e-8 < amount_aud + fee_aud: return False, 'Not enough paper cash.'
    return True, ''

def new_completed_bars(frame, entry_at, checked_at, interval_minutes=15):
    """Never include a candle that started before entry; checked is an independent cursor."""
    bars = frame.iloc[:-1].copy()  # Kraken final row is always current/incomplete
    if bars.empty: return bars
    starts = pd.to_datetime(bars['ts'], utc=True)
    cutoff = max(utc_time(entry_at), utc_time(checked_at))
    return bars.loc[(starts >= cutoff) & (starts + pd.Timedelta(minutes=interval_minutes) <= pd.Timestamp.now(tz='UTC'))]

def trigger_on_bars(bars, stop, target):
    for bar in bars.itertuples():
        if bar.low <= stop: return 'STOP', min(float(bar.open), stop)
        if bar.high >= target: return 'TARGET', max(float(bar.open), target)
    return None

def realized_today(fills, now=None):
    """FIFO realised P/L in AUD, including entry and exit fees."""
    now = utc_time(now or datetime.now(timezone.utc))
    today = now.date()
    lots, realized = {}, 0.
    if fills is None or fills.empty: return 0.
    for r in fills.sort_values('id').itertuples():
        sym = r.symbol
        if r.side == 'BUY':
            lots.setdefault(sym, []).append([float(r.qty), float(r.qty*r.price*r.fx+r.fee_aud)])
        elif r.side == 'SELL':
            remaining, basis = float(r.qty), 0.
            queue = lots.setdefault(sym, [])
            while remaining > 1e-9 and queue:
                q, cost = queue[0]; taken = min(q, remaining)
                basis += cost * taken / q
                if q - taken < 1e-9: queue.pop(0)
                else: queue[0] = [q-taken, cost*(q-taken)/q]
                remaining -= taken
            proceeds = float(r.qty*r.price*r.fx-r.fee_aud)
            if utc_time(r.ts).date() == today: realized += proceeds-basis
    return realized
