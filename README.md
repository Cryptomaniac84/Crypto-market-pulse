# Crypto Market Pulse — paper trading only

Run from this directory:

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v
streamlit run app.py
```

Set `SCANNER_PASSWORD` in environment variables or Streamlit secrets. Optional: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and `DB_PATH`.

**Limitations:** Manual scans and manual stop checks only; no background service. Kraken ticker has no server-provided timestamp: a successful fresh HTTP response is treated as the quote time. Historical 15-minute OHLC bars cannot determine the exact intrabar stop/target ordering. Kraken market pair support and current exchange fee schedules were not independently verified. Frankfurter/ECB FX rates are dated reference rates, not live trading rates. Local SQLite can disappear on ephemeral hosting; export regularly. For persistent deployment use durable storage.
