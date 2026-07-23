"""Macro Filter — fetches macro data and asserts kill switch conditions."""
import asyncio
import time
import pandas as pd
import yfinance as yf
from loguru import logger

class MacroFilter:
    """Monitors macroeconomic indicators to trigger risk-off mode."""

    def __init__(self, check_interval_seconds: int = 900):
        self.check_interval = check_interval_seconds
        self.last_check = 0
        self.is_kill_switch_active = False
        self.reason = ""

    async def update(self) -> None:
        """Periodically update macro status."""
        now = time.time()
        if now - self.last_check < self.check_interval:
            return

        try:
            # Run yfinance in a thread to avoid blocking asyncio
            data = await asyncio.to_thread(self._fetch_macro)

            btc_drop = data.get("BTC_DROP_PCT", 0.0)
            dxy_pump = data.get("DXY_PUMP_PCT", 0.0)

            btc_dumping = btc_drop > 2.0
            dxy_pumping = dxy_pump > 0.5

            if btc_dumping or dxy_pumping:
                self.is_kill_switch_active = True
                self.reason = f"Macro Risk: BTC Drop={btc_drop:.2f}%, DXY Pump={dxy_pump:.2f}%"
                logger.warning(self.reason)
            else:
                if self.is_kill_switch_active:
                    logger.info("Macro Risk cleared.")
                self.is_kill_switch_active = False
                self.reason = ""

            self.last_check = now
        except Exception as e:
            logger.error(f"MacroFilter failed to update: {e}")

    def _fetch_macro(self) -> dict[str, float]:
        """Fetch data from yfinance synchronously."""
        result = {}
        
        try:
            btc = yf.download(tickers="BTC-USD", period="2d", interval="1h", progress=False)
            if not btc.empty and len(btc) >= 2:
                # pandas 2.0+ handles this gracefully. 
                # yfinance returns MultiIndex columns if multiple tickers, but we request one at a time here.
                close_col = btc['Close']
                if isinstance(close_col, pd.DataFrame): # MultiIndex workaround
                    close_col = close_col.iloc[:, 0]
                    
                last_close = float(close_col.iloc[-1])
                prev_close = float(close_col.iloc[-2])
                
                drop_pct = ((prev_close - last_close) / prev_close) * 100
                result["BTC_DROP_PCT"] = drop_pct
        except Exception as e:
            logger.debug(f"Failed to fetch BTC macro data: {e}")

        try:
            dxy = yf.download(tickers="DX-Y.NYB", period="2d", interval="1h", progress=False)
            if not dxy.empty and len(dxy) >= 2:
                close_col = dxy['Close']
                if isinstance(close_col, pd.DataFrame):
                    close_col = close_col.iloc[:, 0]
                    
                last_close = float(close_col.iloc[-1])
                prev_close = float(close_col.iloc[-2])
                
                pump_pct = ((last_close - prev_close) / prev_close) * 100
                result["DXY_PUMP_PCT"] = pump_pct
        except Exception as e:
            logger.debug(f"Failed to fetch DXY macro data: {e}")

        return result
