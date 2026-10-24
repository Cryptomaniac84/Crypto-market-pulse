# Crypto Market Pulse — next-stage upgrade

Changes:
- Adds HYPE (Hyperliquid) and ZEC (Zcash) to the default watchlist and price board.
- Keeps the existing neon crypto banner and USD-first prices with AUD equivalents.
- Ranks technical research candidates; explicitly says NO TRADE when none meets all 4 conditions.
- Adds a one-click paper-trade selector: highest-ranked eligible coin, $25 USD default, configurable $5-$100, records estimated Coinbase/other exchange fees and slippage, attaches a simulated stop and target.
- Uses Coinbase Advanced Intro assumptions: maker 0.09%, taker 0.10%.

IMPORTANT:
- The one-click feature is PAPER TRADING ONLY and requires a button press.
- Simulated stops and targets DO NOT execute automatically while the app is unattended.
- No Coinbase API keys, real orders, continuous monitoring or alerts.
- HYPE price display works through CoinGecko if its API responds, but HYPE technical scoring needs a verified candle feed and may show a warning.
- ZEC technical scoring depends on Kraken OHLC support.
- Coinbase Australia spot trading availability for HYPE/ZEC is NOT verified.
- Data is CoinGecko + Kraken; CoinMarketCap is not connected.
- The free Streamlit instance can lose its local SQLite paper ledger on redeployment/restart. Export CSV frequently.

Install:
Upload app.py, README.md, requirements.txt, crypto_banner.jpg to the ROOT of the existing GitHub crypto-market-pulse repository; commit to main. Do NOT touch the stock scanner repository.
