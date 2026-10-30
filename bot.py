
import os
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

# ==========================================
# INSTELLINGEN
# ==========================================

BASE_URL = "https://api.exchange.coinbase.com"
PRODUCT = "BTC-USD"

INTERVAL = 900          # 15 minuten
DAILY_INTERVAL = 86400  # 1 dag
MAX_CANDLES = 300

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

STATE_FILE = Path("bot_state.json")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "BTC-Pivot-Telegram-Bot/1.0",
    "Accept": "application/json"
})


# ==========================================
# TIJD
# ==========================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_time(timestamp):
    return datetime.fromtimestamp(
        timestamp, timezone.utc
    ).isoformat().replace("+00:00", "Z")


def latest_closed_candle():
    now = int(utc_now().timestamp())
    return (now // INTERVAL) * INTERVAL - INTERVAL


# ==========================================
# COINBASE DATA
# ==========================================

def get_candles(start, end, granularity):
    url = f"{BASE_URL}/products/{PRODUCT}/candles"

    params = {
        "start": iso_time(start),
        "end": iso_time(end),
        "granularity": granularity
    }

    response = SESSION.get(
        url,
        params=params,
        timeout=20
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Coinbase fout {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()

    if not isinstance(data, list):
        raise RuntimeError("Ongeldig Coinbase candle-antwoord")

    candles = []

    for row in data:
        if len(row) < 6:
            continue

        candles.append({
            "time": int(row[0]),
            "low": float(row[1]),
            "high": float(row[2]),
            "open": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5])
        })

    candles.sort(key=lambda x: x["time"])

    return candles


# ==========================================
# DAGELIJKSE PIVOT
# VORIGE VOLLEDIG GESLOTEN UTC-DAG
# ==========================================

def get_daily_pivot():
    today = utc_now().replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    today_timestamp = int(today.timestamp())
    yesterday_timestamp = today_timestamp - DAILY_INTERVAL

    candles = get_candles(
        yesterday_timestamp,
        today_timestamp,
        DAILY_INTERVAL
    )

    previous_day = [
        candle for candle in candles
        if candle["time"] == yesterday_timestamp
    ]

    if not previous_day:
        raise RuntimeError(
            "Coinbase heeft geen volledige candle "
            "voor de vorige UTC-dag teruggegeven."
        )

    candle = previous_day[0]

    pivot = (
        candle["high"] +
        candle["low"] +
        candle["close"]
    ) / 3

    return pivot


# ==========================================
# TELEGRAM
# ==========================================

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN of TELEGRAM_CHAT_ID ontbreekt."
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = SESSION.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=20
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Telegram fout: {response.text[:500]}"
        )

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram fout: {result}")


# ==========================================
# STATUS OPSLAAN
# ==========================================

def load_state():
    if not STATE_FILE.exists():
        return {}

    with STATE_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_state(state):
    temporary_file = STATE_FILE.with_suffix(".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(state, file, indent=2)

    temporary_file.replace(STATE_FILE)


def commit_state():
    # In GitHub Actions bewaren we de status
    # in de repository, zodat volgende runs
    # dezelfde verwerkte candles herkennen.

    if os.environ.get("GITHUB_ACTIONS") != "true":
        return

    subprocess.run(
        ["git", "config", "user.name", "github-actions[bot]"],
        check=True
    )

    subprocess.run(
        [
            "git", "config", "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com"
        ],
        check=True
    )

    subprocess.run(
        ["git", "add", str(STATE_FILE)],
        check=True
    )

    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"]
    )

    if result.returncode == 0:
        return

    if result.returncode != 1:
        raise RuntimeError("Git kon de status niet controleren.")

    subprocess.run(
        ["git", "commit", "-m", "Update BTC Pivot bot state"],
        check=True
    )

    subprocess.run(
        ["git", "push", "origin", "HEAD"],
        check=True
    )


# ==========================================
# 15 MINUTEN CANDLES OPHALEN
# GEMISTE CANDLES VERWERKEN
# ==========================================

def get_new_candles(last_processed, latest_closed):
    result = []

    start = last_processed

    while start <= latest_closed:
        end = min(
            start + MAX_CANDLES * INTERVAL,
            latest_closed + INTERVAL
        )

        candles = get_candles(
            start,
            end,
            INTERVAL
        )

        result.extend(candles)

        start = end

        time.sleep(0.25)

    unique = {}

    for candle in result:
        if (
            candle["time"] > last_processed
            and candle["time"] <= latest_closed
        ):
            unique[candle["time"]] = candle

    return sorted(
        unique.values(),
        key=lambda x: x["time"]
    )


# ==========================================
# HOOFDFUNCTIE
# ==========================================

def main():

    print("BTC Pivot Telegram Bot gestart")

    state = load_state()

    pivot = get_daily_pivot()

    print(f"Coinbase: {PRODUCT}")
    print(f"Dagelijkse Pivot: {pivot:.2f}")

    latest_closed = latest_closed_candle()

    # Eerste uitvoering:
    # controleer alleen de meest recente
    # volledig gesloten candle.

    if "last_processed" not in state:
        state["last_processed"] = (
            latest_closed - INTERVAL
        )

    last_processed = int(state["last_processed"])

    if last_processed > latest_closed:
        raise RuntimeError(
            "Opgeslagen candle-status ligt in de toekomst."
        )

    candles = get_new_candles(
        last_processed,
        latest_closed
    )

    print(f"Nieuwe candles gevonden: {len(candles)}")

    # Haal ook de laatst verwerkte candle op
    # om de vorige slotkoers te kunnen vergelijken.

    previous_candles = get_candles(
        last_processed - INTERVAL,
        last_processed + INTERVAL,
        INTERVAL
    )

    previous = next(
        (
            candle for candle in previous_candles
            if candle["time"] == last_processed
        ),
        None
    )

    if candles and previous is None:
        raise RuntimeError(
            "Vorige candle ontbreekt. "
            "Status wordt niet vooruitgeschoven."
        )

    for candle in candles:

        current_time = candle["time"]

        # Alleen opeenvolgende gesloten candles
        # mogen een kruissignaal veroorzaken.

        consecutive = (
            previous is not None
            and current_time == previous["time"] + INTERVAL
        )

        if consecutive:

            previous_close = previous["close"]
            current_close = candle["close"]

            signal = None

            # LONG: van onder naar boven door Pivot

            if (
                previous_close <= pivot
                and current_close > pivot
            ):
                signal = "LONG"

            # SHORT: van boven naar beneden door Pivot

            elif (
                previous_close >= pivot
                and current_close < pivot
            ):
                signal = "SHORT"

            if signal:

                candle_time = datetime.fromtimestamp(
                    current_time,
                    timezone.utc
                ).strftime("%d-%m-%Y %H:%M UTC")

                message = (
                    f"BTC PIVOT {signal}\n\n"
                    f"Exchange: Coinbase\n"
                    f"Pair: BTC-USD\n"
                    f"Timeframe: 15 minuten\n\n"
                    f"Pivot: ${pivot:,.2f}\n"
                    f"Vorige close: ${previous_close:,.2f}\n"
                    f"Huidige close: ${current_close:,.2f}\n\n"
                    f"Candle: {candle_time}"
                )

                print(message)

                send_telegram(message)

        # Ook candles zonder signaal markeren
        # als verwerkt om dubbele verwerking te voorkomen.

        state["last_processed"] = current_time
        save_state(state)

        previous = candle

    commit_state()

    print("Bot succesvol uitgevoerd.")
    print(
        f"Laatst verwerkte candle: "
        f"{state['last_processed']}"
    )


# ==========================================
# START
# ==========================================

if __name__ == "__main__":
    main()
