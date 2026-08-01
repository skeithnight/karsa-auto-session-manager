from app.bot.utils.formatters import format_entry_alert
from decimal import Decimal

try:
    print(format_entry_alert("AKEUSDT", "buy", Decimal("0.0021869"), Decimal("2721.48"), Decimal("0.00")))
except Exception as e:
    print(f"Error: {e}")
