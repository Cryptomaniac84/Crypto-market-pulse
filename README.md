# Crypto Market Pulse — Telegram alerts + clean dashboard

## What's changed
- Removes the non-functional full desktop screenshot (which duplicated fake balances, signals and buttons).
- Keeps the real Streamlit dashboard and decorative crypto banner, mobile and desktop responsive.
- Adds a Telegram setup/test panel.
- Sends alerts for 4/4 research candidates when **SCAN CRYPTO FOR ME** is pressed.
- Deduplicates research alerts by coin and last completed 15-minute candle, using the local SQLite database.
- Sends Telegram messages for one-click paper buys.
- Retains HYPE and ZEC, with HYPE price-watch only pending a verified candle source.
- All trades remain PAPER ONLY. No continuous background worker, no automatic overnight exits, no real Coinbase integration.

## GitHub upload
Upload all 4 files to the ROOT of **Cryptomaniac84/crypto-market-pulse** (not the Trump scanner): `app.py`, `crypto_banner.jpg`, `requirements.txt`, `README.md`.
You can leave `crypto_desktop_art.png` in GitHub, but it is no longer used.

## Telegram setup
1. Open Telegram and message `@BotFather`; send `/newbot`, follow prompts and copy the token.
2. Start a chat with your new bot and send `/start` (or any message).
3. In a private browser, open `https://api.telegram.org/botYOUR_TOKEN/getUpdates` (replace YOUR_TOKEN locally). Find `message.chat.id` in the JSON response. Do not share the token or this URL with anyone. If no result, send the bot another message and refresh.
4. In the **crypto** Streamlit Cloud app, choose Manage app > Settings > Secrets. Add:

TELEGRAM_BOT_TOKEN = "YOUR_PRIVATE_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

Retain your existing SCANNER_PASSWORD entry. Save secrets and reboot if needed.
5. In the crypto app, expand 'Telegram alert setup and test' and press 'Send test Telegram alert'.

Do not commit credentials to GitHub. If exposed, use BotFather to revoke the token.

## Limitations
Alerts require a user-triggered scan or paper buy. They do not run every 15 minutes on their own; for unattended scanning, implement a separate hosted scheduler/worker and durable data storage. No alert guarantees delivery. Free Streamlit local SQLite can reset on redeploy.
