
import os
import json
import base64
import time
from datetime import datetime, timezone, timedelta

import requests

BASE_URL = "https://api.binance.com"
GITHUB_API = "https://api.github.com"

SYMBOL = "BTCUSDT"
INTERVAL = "15m"
CANDLE_SECONDS = 900
CANDLE_MS = CANDLE_SECONDS * 1000
STATE_PATH = "bot_state.json"
TIMEOUT = 20


def utc_now():
    return datetime.now(timezone.utc)


def get_klines(interval, limit):
    response = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={
            "symbol": SYMBOL,
            "interval": interval,
            "limit": limit,
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return sorted(response.json(), key=lambda row: row[0])


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN of TELEGRAM_CHAT_ID ontbreekt."
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


def github_headers():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN ontbreekt.")

    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def get_state():
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        raise RuntimeError("GITHUB_REPOSITORY ontbreekt.")

    url = f"{GITHUB_API}/repos/{repo}/contents/{STATE_PATH}"
    response = requests.get(
        url, headers=github_headers(), timeout=TIMEOUT
    )

    if response.status_code == 404:
        return None, None

    response.raise_for_status()
    data = response.json()

    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def save_state(state, sha):
    repo = os.environ["GITHUB_REPOSITORY"]
    url = f"{GITHUB_API}/repos/{repo}/contents/{STATE_PATH}"

    encoded = base64.b64encode(
        json.dumps(state, indent=2).encode("utf-8")
    ).decode("ascii")

    payload = {
        "message": "Update BTC Pivot bot state",
        "content": encoded,
    }

    if sha:
        payload["sha"] = sha

    response = requests.put(
        url,
        headers=github_headers(),
        json=payload,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    result = response.json()
    print("Voortgang opgeslagen in GitHub.")
    return result["content"]["sha"]


def get_daily_pivots():
    """Pivot per UTC-dag op basis van de vorige afgesloten dag."""
    rows = get_klines("1d", 30)
    now = utc_now()
    pivots = {}

    for row in rows:
        day_start = datetime.fromtimestamp(
            row[0] / 1000, timezone.utc
        )
        day_end = day_start + timedelta(days=1)

        # Een dag telt pas mee als hij volledig afgesloten is.
        if day_end > now:
            continue

        high = float(row[2])
        low = float(row[3])
        close = float(row[4])

        pivots[day_end.date()] = (high + low + close) / 3

    return pivots


def main():
    # Maximaal 1000 candles per Binance-aanvraag.
    rows = get_klines(INTERVAL, 1000)
    now_ms = int(time.time() * 1000)

    # Alleen volledig gesloten 15-minutencandles.
    closed = [
        row for row in rows
        if int(row[6]) < now_ms
    ]

    if len(closed) < 2:
        raise RuntimeError("Onvoldoende gesloten candles ontvangen.")

    pivots = get_daily_pivots()
    state, state_sha = get_state()

    if state is None:
        # Eerste start: begin nu, zonder oude signalen te versturen.
        state = {
            "last_processed_open_time": int(closed[-1][0]),
            "created_at_utc": utc_now().isoformat(),
        }

        send_telegram(
            "✅ BTC Pivot-bot is gestart!\n\n"
            "Markt: Binance BTCUSDT\n"
            "Tijdframe: 15 minuten\n"
            "Strategie: dagelijkse Pivot-kruisingen\n"
            "SMA200: uitgeschakeld\n"
            "Voortgang wordt opgeslagen in GitHub."
        )

        save_state(state, state_sha)
        print("Eerste start: beginpunt opgeslagen.")
        return

    last_processed = int(state["last_processed_open_time"])

    if last_processed < int(closed[0][0]):
        print(
            "WAARSCHUWING: de bot is langer uit geweest dan de "
            "beschikbare candlehistorie. Niet alle candles zijn "
            "mogelijk terug te halen."
        )

    # Alleen candles verwerken die nog niet zijn verwerkt.
    new_indices = [
        i for i, row in enumerate(closed)
        if int(row[0]) > last_processed
    ]

    if not new_indices:
        print("Geen nieuwe gesloten candles.")
        return

    signals_sent = 0

    for i in new_indices:
        current = closed[i]

        # De vorige candle is nodig om een kruising te herkennen.
        if i == 0:
            print("Vorige candle ontbreekt; candle overgeslagen.")
            continue

        previous = closed[i - 1]

        current_time = datetime.fromtimestamp(
            current[0] / 1000, timezone.utc
        )
        previous_time = datetime.fromtimestamp(
            previous[0] / 1000, timezone.utc
        )

        current_day = current_time.date()
        previous_day = previous_time.date()

        # Pivot is de Pivot van de vorige volledig gesloten UTC-dag.
        pivot = pivots.get(current_day)

        if pivot is None:
            print(f"Geen dagelijkse Pivot beschikbaar voor {current_day}.")
            continue

        # Een dagwisseling kan de Pivot veranderen. Dat is op zichzelf
        # geen echte koerskruising; daarom die overgang overslaan.
        if current_day != previous_day:
            print("Dagwisseling: candle-overgang overgeslagen.")
            continue

        previous_close = float(previous[4])
        current_close = float(current[4])

        signal = None

        if previous_close <= pivot and current_close > pivot:
            signal = "LONG"

        elif previous_close >= pivot and current_close < pivot:
            signal = "SHORT"

        print(
            f"{current_time:%Y-%m-%d %H:%M} UTC | "
            f"Close=${current_close:,.2f} | "
            f"Pivot=${pivot:,.2f} | "
            f"Signaal={signal or 'geen'}"
        )

        if signal:
            emoji = "🟢" if signal == "LONG" else "🔴"

            message = (
                f"{emoji} BTC {signal}-SIGNAAL\n\n"
                f"Markt: Binance {SYMBOL}\n"
                f"Gesloten candle: {current_time:%Y-%m-%d %H:%M} UTC\n"
                f"Sluitprijs: ${current_close:,.2f}\n"
                f"Dagelijkse Pivot: ${pivot:,.2f}\n\n"
                f"Voorwaarde: gesloten candle kruiste de Pivot.\n"
                "Geen SMA200-filter.\n"
                "Dit is een signaal, geen financieel advies."
            )

            send_telegram(message)
            signals_sent += 1
            print(f"Telegram {signal}-melding verzonden.")

        # Voortgang opslaan na elke verwerkte candle.
        # Zo wordt bij een latere fout niet alles opnieuw verwerkt.
        state["last_processed_open_time"] = int(current[0])
        state["updated_at_utc"] = utc_now().isoformat()
        state_sha = save_state(state, state_sha)

    print(f"Aantal verzonden signalen: {signals_sent}")


if __name__ == "__main__":
    main()
