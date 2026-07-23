import asyncio
from app.core import metrics
from app.bot.utils.formatters.live_funnel_formatter import format_live_funnel

class MockReport:
    total_trades = 623
    winning_trades = 100
    losing_trades = 100
    win_rate = 50.0
    net_pnl = 10.0
    gross_profit = 20.0
    gross_loss = 10.0
    total_fees = 1.0
    total_slippage = 1.0

def main():
    metrics.start_metrics_server(8001)
    funnel_metrics = metrics.get_live_funnel_metrics()
    print(format_live_funnel(funnel_metrics, MockReport()))

if __name__ == "__main__":
    main()
