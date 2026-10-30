
import os
import json
import requests
from datetime import datetime, timezone, timedelta

BASE_URL = "https://api.exchange.coinbase.com"
PRODUCT = "BTC-USD"
GRANULARITY = 900
CANDLE_LIMIT = 300
STATE_FILE = "bot_state.json"
TIMEOUT = 20

HEADERS = {
    "User-Agent": "BTC-Pivot-Telegram-Bot/2.0"
}


def get_candles():
    """Haal maximaal 300 candles van Coinbase op."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=GRANULARITY * CANDLE_LIMIT)

    response = requests.get(
        f"{BASE_URL}/products/{PRODUCT}/candles",
        params={
            "granularity": GRANULARITY,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    rows = sorted(response.json(), key=lambda row: row[0])

    if not rows:
        raise RuntimeError("Coinbase heeft geen candles teruggegeven.")

    now_ts = int(datetime.now(timezone.utc).timestamp())

    # Alleen volledig gesloten candles gebruiken.
    closed = [
        row for row in rows
        if row[0] + GRANULARITY <= now_ts
    ]

    if len(closed) < 200:
        raise RuntimeError(
            f"Onvoldoende gesloten candles: {len(closed)}"
        )

    return closed


def get_daily_pivot():
    """Pivot van de vorige volledig gesloten UTC-dag."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=5)

    response = requests.get(
        f"{BASE_URL}/products/{PRODUCT}/candles",
        params={
            "granularity": 86400,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    rows = sorted(response.json(), key=lambda row: row[0])

    today = end.date()

    completed = [
        row for row in rows
        if datetime.fromtimestamp(
            row[0], timezone.utc
        ).date() < today
    ]

    if not completed:
        raise RuntimeError("Geen gesloten dagcandle gevonden.")

    previous_day = completed[-1]

    low = float(previous_day[1])
    high = float(previous_day[2])
    close = float(previous_day[4])

    pivot = (high + low + close) / 3

    return pivot, high, low, close


def load_state():
    """Lees de laatst verwerkte candle."""
    if not os.path.exists(STATE_FILE):
        return None

    try:
        with open(STATE_FILE, "r") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return None


def save_state(timestamp):
    """Bewaar de laatst verwerkte candle."""
    state = {
        "last_processed_candle": int(timestamp)
    }

    with open(STATE_FILE, "w") as file:
        json.dump(state, file, indent=2)


def send_telegram(message):
    """Verstuur een Telegram-bericht."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Telegram Secrets ontbreken."
        )

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message
        },
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError("Telegram heeft het bericht niet bevestigd.")


def check_signal(previous_close, current_close, sma200, pivot):
    """Controleer Pivot-kruising met SMA200-filter."""

    # LONG: van onder naar boven de Pivot.
    if (
        previous_close <= pivot
        and current_close > pivot
        and current_close > sma200
    ):
        return "LONG"

    # SHORT: van boven naar onder de Pivot.
    if (
        previous_close >= pivot
        and current_close < pivot
        and current_close < sma200
    ):
        return "SHORT"

    return None


def main():
    print("BTC Pivot Telegram Bot gestart")
    print("Coinbase: BTC-USD")

    pivot, day_high, day_low, day_close = get_daily_pivot()

    print(f"Dagelijkse Pivot: {pivot:.2f}")

    candles = get_candles()
    closes = [float(row[4]) for row in candles]

    state = load_state()

    latest_timestamp = candles[-1][0]

    # Eerste start: geen oude signalen versturen.
    if state is None:
        save_state(latest_timestamp)

        print("Eerste start: beginpositie opgeslagen.")
        print("Wachten op nieuwe gesloten candles.")
        return

    last_processed = int(
        state.get("last_processed_candle", 0)
    )

    new_indices = [
        i for i, candle in enumerate(candles)
        if candle[0] > last_processed
    ]

    print(f"Nieuwe candles gevonden: {len(new_indices)}")

    if not new_indices:
        print("Geen nieuwe candles.")
        return

    processed_timestamp = last_processed

    for i in new_indices:

        # Voor SMA200 zijn 199 eerdere candles nodig.
        if i < 199:
            print("Onvoldoende historische data voor SMA200.")
            continue

        previous_close = closes[i - 1]
        current_close = closes[i]

        sma200 = sum(closes[i - 199:i + 1]) / 200

        signal = check_signal(
            previous_close,
            current_close,
            sma200,
            pivot
        )

        candle_timestamp = candles[i][0]

        candle_time = datetime.fromtimestamp(
            candle_timestamp,
            timezone.utc
        ).strftime("%Y-%m-%d %H:%M UTC")

        print(
            f"{candle_time} | "
            f"BTC: ${current_close:,.2f} | "
            f"Pivot: ${pivot:,.2f} | "
            f"SMA200: ${sma200:,.2f} | "
            f"Signaal: {signal or 'Geen'}"
        )

        if signal:
            emoji = "🟢" if signal == "LONG" else "🔴"

            message = (
                f"{emoji} BTC {signal}-SIGNAAL\n\n"
                f"Markt: BTC/USD (Coinbase)\n"
                f"Tijdframe: 15 minuten\n"
                f"Candle: {candle_time}\n\n"
                f"Sluitprijs: ${current_close:,.2f}\n"
                f"Dagelijkse Pivot: ${pivot:,.2f}\n"
                f"SMA200: ${sma200:,.2f}\n\n"
                f"Voorwaarden: Pivot-kruising + SMA200-filter.\n"
                f"Dit is een signaal, geen financieel advies."
            )

            # Pas na bevestiging door Telegram verdergaan.
            send_telegram(message)

            print(f"Telegram {signal} verzonden.")

        processed_timestamp = candle_timestamp

    # Alleen opslaan nadat alle nieuwe candles verwerkt zijn.
    save_state(processed_timestamp)

    print("Bot succesvol uitgevoerd.")
    print(f"Laatst verwerkte candle: {processed_timestamp}")


if __name__ == "__main__":
    main()
