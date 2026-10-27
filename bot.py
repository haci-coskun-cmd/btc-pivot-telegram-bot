
import os
import requests
from datetime import datetime, timezone, timedelta

BASE_URL = "https://api.exchange.coinbase.com"
PRODUCT = "BTC-USD"
HEADERS = {"User-Agent": "BTC-Pivot-Telegram-Bot/1.0"}
TIMEOUT = 20


def get_candles(granularity, count):
    """Haal marktdata op en zet candles op tijdsvolgorde."""
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
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    # Coinbase: tijd, laag, hoog, open, close, volume
    rows = sorted(response.json(), key=lambda row: row[0])

    if not rows:
        raise RuntimeError("Geen candles ontvangen van Coinbase.")

    return rows


def send_telegram(message):
    """Verstuur een Telegram-bericht via de opgeslagen GitHub-secrets."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN of TELEGRAM_CHAT_ID ontbreekt in GitHub Secrets."
        )

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram heeft het bericht niet bevestigd.")


def get_daily_pivot():
    """Bereken de Pivot met de laatste volledig gesloten UTC-dag."""
    rows = get_candles(86400, 5)
    today = datetime.now(timezone.utc).date()

    completed_days = [
        row for row in rows
        if datetime.fromtimestamp(row[0], timezone.utc).date() < today
    ]

    if not completed_days:
        raise RuntimeError("Geen volledig gesloten dagcandle gevonden.")

    previous_day = completed_days[-1]

    day_low = float(previous_day[1])
    day_high = float(previous_day[2])
    day_close = float(previous_day[4])

    pivot = (day_high + day_low + day_close) / 3

    return pivot, day_high, day_low, day_close


def main():
    pivot, day_high, day_low, day_close = get_daily_pivot()

    # 900 seconden = 15 minuten.
    rows = get_candles(900, 230)
    now_ts = int(datetime.now(timezone.utc).timestamp())

    # Gebruik alleen volledig gesloten 15-minutencandles.
    closed = [
        row for row in rows
        if row[0] + 900 <= now_ts
    ]

    if len(closed) < 200:
        raise RuntimeError(
            f"Onvoldoende gesloten candles voor SMA200: {len(closed)}."
        )

    closes = [float(row[4]) for row in closed]

    previous_close = closes[-2]
    current_close = closes[-1]
    sma200 = sum(closes[-200:]) / 200

    signal = None

    # LONG: candle kruist boven Pivot en sluit boven SMA200.
    if (
        previous_close <= pivot
        and current_close > pivot
        and current_close > sma200
    ):
        signal = "LONG"

    # SHORT: candle kruist onder Pivot en sluit onder SMA200.
    elif (
        previous_close >= pivot
        and current_close < pivot
        and current_close < sma200
    ):
        signal = "SHORT"

    print(f"BTC/USD: ${current_close:,.2f}")
    print(f"Pivot: ${pivot:,.2f}")
    print(f"SMA200: ${sma200:,.2f}")
    print(f"Signaal: {signal or 'geen nieuw signaal'}")

    if signal:
        emoji = "🟢" if signal == "LONG" else "🔴"

        message = (
            f"{emoji} BTC {signal}-SIGNAAL\n\n"
            f"Markt: BTC/USD (Coinbase)\n"
            f"Laatste gesloten 15m-slot: "
            f"{datetime.fromtimestamp(closed[-1][0], timezone.utc):%Y-%m-%d %H:%M} UTC\n"
            f"Sluitprijs: ${current_close:,.2f}\n"
            f"Dagelijkse Pivot: ${pivot:,.2f}\n"
            f"SMA200 (15m): ${sma200:,.2f}\n\n"
            "Voorwaarden: Pivot-kruising en bevestiging door SMA200.\n"
            "Dit is een signaal, geen financieel advies."
        )

        send_telegram(message)
        print("Telegram-signaal verzonden.")


if __name__ == "__main__":
    main()
