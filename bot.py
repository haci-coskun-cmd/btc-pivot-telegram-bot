
import os
import requests
from datetime import datetime, timezone, timedelta

BASE_URL = "https://api.exchange.coinbase.com"
PRODUCT = "BTC-USD"
HEADERS = {"User-Agent": "BTC-Pivot-Telegram-Bot"}

def get_candles(granularity, count):
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=granularity * count)

    response = requests.get(
        f"{BASE_URL}/products/{PRODUCT}/candles",
        params={
            "granularity": granularity,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()

    # Coinbase candle format: time, low, high, open, close, volume
    rows = sorted(response.json(), key=lambda row: row[0])
    if not rows:
        raise RuntimeError("Coinbase heeft geen candles teruggegeven.")
    return rows

def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=20,
    )
    response.raise_for_status()

def main():
    daily = get_candles(86400, 3)
    today_utc = datetime.now(timezone.utc).date()

    # Neem de meest recente volledig gesloten dag.
    completed_days = [
        row for row in daily
        if datetime.fromtimestamp(row[0], timezone.utc).date() < today_utc
    ]
    if not completed_days:
        raise RuntimeError("Geen gesloten dagelijkse candle gevonden.")

    previous_day = completed_days[-1]
    day_low, day_high, day_close = (
        float(previous_day[1]),
        float(previous_day[2]),
        float(previous_day[4]),
    )
    pivot = (day_high + day_low + day_close) / 3

    candles = get_candles(900, 230)
    # Negeer de nog lopende 15-minutencandle.
    now_ts = int(datetime.now(timezone.utc).timestamp())
    closed = [row for row in candles if row[0] + 900 <= now_ts]

    if len(closed) < 201:
        raise RuntimeError("Niet genoeg gesloten 15-minutencandles voor SMA200.")

    closes = [float(row[4]) for row in closed]
    previous_close = closes[-2]
    current_close = closes[-1]
    sma200 = sum(closes[-200:]) / 200

    message = None
    if previous_close <= pivot and current_close > pivot and current_close > sma200:
        message = (
            "🟢 BTC LONG-signaal\n"
            f"Prijs: ${current_close:,.2f}\n"
            f"Pivot: ${pivot:,.2f}\n"
            f"SMA200: ${sma200:,.2f}\n"
            "Voorwaarde: gesloten 15m-candle boven pivot en SMA200."
        )
    elif previous_close >= pivot and current_close < pivot and current_close < sma200:
        message = (
            "🔴 BTC SHORT-signaal\n"
            f"Prijs: ${current_close:,.2f}\n"
            f"Pivot: ${pivot:,.2f}\n"
            f"SMA200: ${sma200:,.2f}\n"
            "Voorwaarde: gesloten 15m-candle onder pivot en SMA200."
        )

    if message:
        send_telegram(message)
        print("Telegram-signaal verzonden.")
    else:
        print(
            f"Geen nieuw signaal. BTC=${current_close:,.2f}, "
            f"pivot=${pivot:,.2f}, SMA200=${sma200:,.2f}"
        )

if __name__ == "__main__":
    main()

