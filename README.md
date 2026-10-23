# Crypto Market Pulse — paper-trading prototype

Separate Streamlit app from Global Market Pulse. Uses public CoinGecko spot prices and Kraken 15-minute candles. No API keys needed for market data. Does not run continuously, does not place exchange orders, and does not send push alerts.

## Deploy
1. Create a **new private GitHub repository**, e.g. `crypto-market-pulse`.
2. Upload `app.py` and `requirements.txt`.
3. Create a new app in Streamlit Community Cloud, pointing to `app.py` in the new repository.
4. In the Streamlit app settings > Secrets, add `SCANNER_PASSWORD = "choose-a-strong-private-password"`.
5. Open your new crypto app and scan. Keep the existing stocks app untouched.

Paper fills and stop plans are stored in a local SQLite database that may be wiped by free hosting. Back up trades with CSV. This is a prototype and has not been live integration tested against the public endpoints.
