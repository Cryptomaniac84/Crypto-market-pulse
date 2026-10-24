# Crypto Market Pulse — desktop artwork restored

This is a clean five-file deployment package for the existing `Cryptomaniac84/crypto-market-pulse` Streamlit app.

## Files (upload all FIVE to repository root)
- `app.py`: app and trading features
- `crypto_desktop_art.png`: FULL original desktop design artwork
- `crypto_banner.jpg`: compact banner
- `requirements.txt`: dependencies
- `README.md`: instructions

## Update steps
Open the **crypto-market-pulse** GitHub repository (not the stock scanner), Add file > Upload files, select all five files, then Commit directly to `main`. Streamlit should redeploy.

The app now shows a text title, compact banner, and the original full artwork in a right-hand desktop column. On mobile, columns stack so the artwork appears below the header. The artwork is bundled locally rather than depending on an external image URL.

## Trading limitations
This remains a PAPER TRADING ONLY app, not an automated 24/7 trading bot. The one-click feature needs manual action. Stop and target checks also require manual action. HYPE is price-watch only until a verified candle feed is integrated; ZEC data depends on Kraken support. Coinbase market integration, real exchange trading and live notifications are not implemented. Free Streamlit storage may reset paper trade records: export CSV backups regularly. Coinbase Intro spot fee assumptions are maker 0.09%, taker 0.10%; confirm before live use.
