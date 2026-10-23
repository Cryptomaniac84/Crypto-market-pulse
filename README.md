# Crypto Market Pulse — dashboard upgrade

## What's new
- New crypto hero banner and mobile-friendly dark styling.
- Coinbase Advanced Intro spot fee assumptions: 0.09% maker / 0.10% taker, based on account screenshots.
- Compare simulated fees with CoinSpot Markets (0.10%) and CoinSpot Instant (1.00%).
- USD-first paper order inputs with AUD equivalents and editable slippage assumption.
- Fee estimates automatically included in simulated buy/sell orders and simulated stop/target exits.

## Deployment
Replace the files in the **crypto-market-pulse** GitHub repository only:
`app.py`, `README.md`, `requirements.txt`, and add `crypto_banner.jpg`.
Keep your existing Streamlit `SCANNER_PASSWORD` secret. Streamlit should redeploy from `main`.

## Limitations
Paper trading only. No exchange orders, background monitoring, alerts or automatic overnight exits.
Market data remains CoinGecko public spot quotes (60-second cache) and Kraken 15-minute candles.
CoinMarketCap and Coinbase live market APIs are NOT connected. Exchange fees are assumptions and may change.
The SQLite paper ledger on free hosting is not durable: export CSV backups regularly.
