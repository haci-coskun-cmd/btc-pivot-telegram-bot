import os
import requests
from datetime import datetime, timezone

BINANCE = "https://api.binance.com"
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
INTERVAL = "15m"
MA_PERIOD = 200
NEAR_PIVOT_PERCENT = 0.001  # 0.10%

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_klines(interval, limit):
    response = requests.get(
        f"{BINANCE}/api/v3/klines",
        params={"symbol": SYMBOL, "interval": interval, "limit": limit},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()

def closed_candles(rows):
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return [row for row in rows if int(row[6]) < now_ms]

def get_daily_pivot():
    rows = get_klines("1d", 4)
    closed = closed_candles(rows)
    if not closed:
        raise RuntimeError("Geen gesloten dagelijkse candle ontvangen.")
    # Laatste volledig gesloten dag
    candle = closed[-1]
    high, low, close = float(candle[2]), float(candle[3]), float(candle[4])
    pivot = (high + low + close) / 3
    return pivot, high, low, close

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Vul TELEGRAM_BOT_TOKEN en TELEGRAM_CHAT_ID in bij GitHub Secrets.")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    response = requests.post(
        url,
        data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
        timeout=20,
    )
    response.raise_for_status()

def main():
    daily_pivot, day_high, day_low, day_close = get_daily_pivot()

    rows = get_klines(INTERVAL, MA_PERIOD + 3)
    closed = closed_candles(rows)
    if len(closed) < MA_PERIOD + 1:
        raise RuntimeError("Niet genoeg gesloten 15-minutencandles voor MA200.")

    # Gebruik alleen gesloten candles; vergelijk de laatste twee gesloten candles.
    closes = [float(row[4]) for row in closed]
    previous_close = closes[-2]
    current_close = closes[-1]
    ma200 = sum(closes[-MA_PERIOD:]) / MA_PERIOD

    fib_range = day_high - day_low
    fib_382 = day_low + fib_range * 0.382
    fib_618 = day_low + fib_range * 0.618

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    near_pivot = abs(current_close - daily_pivot) / daily_pivot <= NEAR_PIVOT_PERCENT

    signal = None
    if previous_close <= daily_pivot and current_close > daily_pivot and current_close > ma200:
        signal = "🟢 LONG-signaal"
    elif previous_close >= daily_pivot and current_close < daily_pivot and current_close < ma200:
        signal = "🔴 SHORT-signaal"

    if signal:
        message = (
            f"{signal} — {SYMBOL}\n"
            f"Tijd: {now}\n"
            f"15m slotkoers: {current_close:.2f}\n"
            f"Dagpivot: {daily_pivot:.2f}\n"
            f"SMA200 (15m): {ma200:.2f}\n"
            f"Vorige dag H/L/C: {day_high:.2f} / {day_low:.2f} / {day_close:.2f}\n"
            f"Fibonacci 0.382: {fib_382:.2f}\n"
            f"Fibonacci 0.618: {fib_618:.2f}\n\n"
            "Dit is alleen een melding, geen automatische order of financieel advies."
        )
        send_telegram(message)
    elif near_pivot:
        send_telegram(
            f"📍 BTC dicht bij dagpivot\n"
            f"Tijd: {now}\n"
            f"Koers: {current_close:.2f}\n"
            f"Dagpivot: {daily_pivot:.2f}\n"
            f"SMA200 (15m): {ma200:.2f}\n"
            "Dit is alleen een melding, geen automatische order."
        )
    else:
        print(
            f"Geen signaal. {now} | koers={current_close:.2f} "
            f"pivot={daily_pivot:.2f} SMA200={ma200:.2f}"
        )

if __name__ == "__main__":
    main()
