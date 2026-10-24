"""Crypto Market Pulse scheduled PAPER trader. No exchange API or live orders.

State is stored in paper_bot_state.json and must be committed by the workflow
between runs. It is independent of Streamlit's ephemeral SQLite practice ledger.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

COINS = {
    'BTC': ('XXBTZUSD', 'bitcoin'), 'ETH': ('XETHZUSD', 'ethereum'),
    'SOL': ('SOLUSD', 'solana'), 'XRP': ('XRPUSD', 'ripple'),
    'DOGE': ('XDGUSD', 'dogecoin'), 'SUI': ('SUIUSD', 'sui'),
    'HBAR': ('HBARUSD', 'hedera-hashgraph'), 'ZEC': ('XZECZUSD', 'zcash'),
}
STATE = Path('paper_bot_state.json')
STARTING_AUD = 500.0
ORDER_AUD = 50.0
MAX_POSITIONS = 3
MAX_DAILY_LOSS_AUD = 25.0
FEE_RATE = 0.001  # 0.10% taker assumption each side
SLIPPAGE_RATE = 0.001  # 0.10% each side; illustrative, not guaranteed
session = requests.Session()
session.headers.update({'User-Agent': 'CryptoMarketPulsePaperBot/1.0'})


def now():
    return datetime.now(timezone.utc).isoformat()


def fmt(value):
    return f'{value:,.2f}' if abs(value) >= 1 else f'{value:,.5f}'


def get_json(url, **kwargs):
    for attempt in range(3):
        try:
            response = session.get(url, timeout=20, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def quotes():
    ids = ','.join(cid for _, cid in COINS.values())
    return get_json('https://api.coingecko.com/api/v3/simple/price',
                    params={'ids': ids, 'vs_currencies': 'usd,aud',
                            'include_last_updated_at': 'true'})


def bars(pair):
    payload = get_json('https://api.kraken.com/0/public/OHLC',
                       params={'pair': pair, 'interval': 15})
    if payload.get('error'):
        raise ValueError('; '.join(payload['error']))
    rows = next(value for key, value in payload['result'].items() if key != 'last')
    data = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close',
                                       'vwap', 'volume', 'count'])
    for col in ('open', 'high', 'low', 'close', 'volume'):
        data[col] = pd.to_numeric(data[col])
    data['ts'] = pd.to_datetime(data.ts, unit='s', utc=True)
    return data.iloc[:-1].copy()  # The final Kraken candle is unfinished.


def indicators(symbol, price, data):
    if len(data) < 55:
        raise ValueError('Not enough completed candles')
    close = data.close
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain.iloc[-1] / max(loss.iloc[-1], 1e-10)
    rsi = 100 - 100 / (1 + rs)
    vol = float(data.volume.iloc[-1] / max(data.volume.iloc[-21:-1].mean(), 1e-9))
    resistance = float(data.high.iloc[-21:-1].max())
    support = float(data.low.iloc[-11:-1].min())
    score = sum((price > ema20, 50 <= rsi <= 72, vol >= 1.3, price > resistance))
    entry = max(price, resistance) * 1.001
    stop = min(support, entry * .985)
    if stop <= 0 or stop >= entry:
        stop = entry * .975
    target = entry + 2 * (entry - stop)
    return {'symbol': symbol, 'score': score, 'rsi': float(rsi),
            'vol': vol, 'entry': entry, 'stop': stop, 'target': target,
            'last': data.ts.iloc[-1].isoformat()}


def load_state():
    if not STATE.exists():
        return {'cash_aud': STARTING_AUD, 'positions': {}, 'trades': [],
                'last_signal': {}, 'last_summary_date': None,
                'created_at': now()}
    data = json.loads(STATE.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not all(k in data for k in ('cash_aud', 'positions', 'trades', 'last_signal')):
        raise ValueError('Invalid paper ledger: refusing to reset capital')
    return data


def save_state(state):
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(STATE)


def telegram(message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        raise ValueError('Missing GitHub Actions Telegram secrets')
    response = session.post(f'https://api.telegram.org/bot{token}/sendMessage',
                            json={'chat_id': chat, 'text': message}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    if not payload.get('ok'):
        raise RuntimeError('Telegram did not confirm message')


def close_position(state, symbol, price, fx, reason, bar_id):
    position = state['positions'][symbol]
    exit_usd = price * (1 - SLIPPAGE_RATE)
    gross_aud = position['qty'] * exit_usd * fx
    fee_aud = gross_aud * FEE_RATE
    proceeds = gross_aud - fee_aud
    pnl = proceeds - position['cost_aud']
    state['cash_aud'] += proceeds
    trade = {'symbol': symbol, 'opened_at': position['opened_at'],
             'closed_at': now(), 'entry_usd': position['entry_usd'],
             'exit_usd': exit_usd, 'cost_aud': position['cost_aud'],
             'proceeds_aud': proceeds, 'pnl_aud': pnl, 'reason': reason,
             'exit_bar': bar_id}
    state['trades'].append(trade)
    del state['positions'][symbol]
    save_state(state)
    telegram(f'🔴 PAPER EXIT {symbol} ({reason})\n'
             f'Exit US${fmt(exit_usd)} (A${fmt(exit_usd * fx)})\n'
             f'Realised P/L A${pnl:+.2f} • Cash A${state["cash_aud"]:.2f}\n'
             'Simulation only — no exchange order.')


def enter_position(state, symbol, signal, price, fx):
    # Market-entry simulation uses current spot, not the illustrative breakout entry.
    entry_usd = price * (1 + SLIPPAGE_RATE)
    budget = min(ORDER_AUD, state['cash_aud'] / (1 + FEE_RATE))
    if budget < 10:
        return
    fee = budget * FEE_RATE
    qty = (budget / fx) / entry_usd
    if not (0 < signal['stop'] < entry_usd < signal['target']):
        print(f'{symbol}: skipped, stop/target invalid at simulated fill')
        return
    state['cash_aud'] -= budget + fee
    state['positions'][symbol] = {
        'qty': qty, 'entry_usd': entry_usd, 'cost_aud': budget + fee,
        'stop_usd': signal['stop'], 'target_usd': signal['target'],
        'opened_at': now(), 'opened_bar': signal['last'],
        'last_checked_bar': signal['last']}
    state['last_signal'][symbol] = signal['last']
    save_state(state)
    telegram(f'🟢 PAPER BUY {symbol} (4/4)\n'
             f'Entry US${fmt(entry_usd)} (A${fmt(entry_usd * fx)})\n'
             f'Amount A${budget:.2f} • Stop US${fmt(signal["stop"])} '
             f'• Target US${fmt(signal["target"])}\n'
             f'RSI {signal["rsi"]:.1f} • Volume {signal["vol"]:.2f}x\n'
             'Simulation only — no exchange order.')


def check_exit(state, symbol, data, fx):
    pos = state['positions'][symbol]
    last_checked = pd.Timestamp(pos['last_checked_bar'])
    new_bars = data[data.ts > last_checked]
    if new_bars.empty:
        return
    for row in new_bars.itertuples():
        # Conservative ambiguity: if both touched in one candle, stop wins.
        if row.low <= pos['stop_usd']:
            close_position(state, symbol, min(float(row.open), pos['stop_usd']),
                           fx, 'STOP', row.ts.isoformat())
            return
        if row.high >= pos['target_usd']:
            close_position(state, symbol, max(float(row.open), pos['target_usd']),
                           fx, 'TARGET', row.ts.isoformat())
            return
    pos['last_checked_bar'] = new_bars.ts.iloc[-1].isoformat()
    save_state(state)


def daily_realised(state):
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(t['pnl_aud'] for t in state['trades'] if t['closed_at'][:10] == today)


def summary(state, spot_data, fx):
    equity = state['cash_aud']
    for sym, position in state['positions'].items():
        quote = spot_data.get(COINS[sym][1], {})
        if quote.get('usd') and time.time() - quote.get('last_updated_at', 0) <= 300:
            equity += position['qty'] * float(quote['usd']) * fx * (1 - SLIPPAGE_RATE) * (1 - FEE_RATE)
        else:
            return None  # Don't publish a misleading equity number.
    realised = sum(t['pnl_aud'] for t in state['trades'])
    wins = sum(t['pnl_aud'] > 0 for t in state['trades'])
    return (f'📊 Crypto Market Pulse — paper daily report (UTC)\n'
            f'Equity A${equity:.2f} • Cash A${state["cash_aud"]:.2f}\n'
            f'Total return A${equity - STARTING_AUD:+.2f} '
            f'({(equity / STARTING_AUD - 1) * 100:+.2f}%)\n'
            f'Realised P/L A${realised:+.2f}\n'
            f'Closed trades {len(state["trades"])} • Wins {wins}\n'
            f'Open positions: {", ".join(state["positions"]) or "none"}\n'
            'Indicative mark-to-market; no real orders.')


def main():
    if not os.getenv('TELEGRAM_BOT_TOKEN') or not os.getenv('TELEGRAM_CHAT_ID'):
        raise ValueError('Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as GitHub repository secrets')
    state = load_state()
    prices = quotes()
    btc = prices.get('bitcoin', {})
    if not btc.get('usd') or not btc.get('aud') or time.time() - btc.get('last_updated_at', 0) > 300:
        raise ValueError('BTC FX quote missing or stale; no paper trades made')
    fx = float(btc['aud']) / float(btc['usd'])
    market = {}
    errors = []
    for symbol, (pair, cid) in COINS.items():
        try:
            q = prices[cid]
            if not q.get('usd') or time.time() - q.get('last_updated_at', 0) > 300:
                raise ValueError('Missing/stale spot quote')
            data = bars(pair)
            market[symbol] = (float(q['usd']), data)
        except Exception as exc:
            errors.append(f'{symbol}: {type(exc).__name__}: {str(exc)[:100]}')
    # Manage exits first. Missing candle data means NO simulated exit.
    for symbol in list(state['positions']):
        if symbol in market:
            check_exit(state, symbol, market[symbol][1], fx)
    if not market:
        raise ValueError('No usable markets: ' + '; '.join(errors))
    # Cap daily realised losses. An overnight or intrabar gap can exceed the cap.
    if daily_realised(state) > -MAX_DAILY_LOSS_AUD:
        candidates = []
        for symbol, (price, data) in market.items():
            try:
                signal = indicators(symbol, price, data)
                print(f'{symbol}: {signal["score"]}/4')
                if (signal['score'] == 4 and symbol not in state['positions']
                        and state['last_signal'].get(symbol) != signal['last']):
                    candidates.append((signal['vol'], symbol, signal, price))
            except Exception as exc:
                errors.append(f'{symbol}: {type(exc).__name__}: {str(exc)[:100]}')
        for _, symbol, signal, price in sorted(candidates, reverse=True):
            if len(state['positions']) >= MAX_POSITIONS:
                break
            enter_position(state, symbol, signal, price, fx)
    else:
        print('Daily realised loss threshold reached; no new entries.')
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get('last_summary_date') != today:
        message = summary(state, prices, fx)
        if message:
            telegram(message)
            state['last_summary_date'] = today
            save_state(state)
    for error in errors:
        print('WARNING:', error)
    print(f'Paper run completed {now()}; cash A${state["cash_aud"]:.2f}; '
          f'open {len(state["positions"])}; closed {len(state["trades"])}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {str(exc)[:180]}')
        sys.exit(1)
