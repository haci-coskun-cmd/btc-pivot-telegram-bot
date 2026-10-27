
# BTC Pivot Telegram Bot

Deze bot controleert BTC/USDT met 15-minutencandles.

## Strategie
- Dagelijkse pivot: (High + Low + Close) / 3 van de vorige dag.
- SMA200 op de 15-minutengrafiek.
- Telegram-meldingen bij mogelijke LONG- en SHORT-signalen.

## Instellen
Voeg in GitHub bij Settings → Secrets and variables → Actions deze secrets toe:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

De bot plaatst alleen meldingen en voert geen transacties uit.

Let op: GitHub Actions kan geplande uitvoeringen vertragen. Dit is geen realtime handelsdienst.
