"""Sanitized local connection check; never prints keys, signed headers or tokens."""
from datetime import datetime,timezone
import json
import os
import time
from data_utils import DataError,BudgetPause,safe_error
from live_engine import data_quality
from my_webull_adapter import WebullSDKAdapter
from settings import Settings
from rate_control import RequestBudget


def main():
    settings=Settings.load();ticker=os.getenv('DEFAULT_TICKER','SPY').strip().upper()
    try:
        adapter=WebullSDKAdapter(settings);budget=RequestBudget(settings.max_requests)
        q=adapter.fetch_quote(ticker,budget)
        while True:
            try: h=adapter.fetch_history(ticker,budget);break
            except BudgetPause as exc: time.sleep(min(2,exc.seconds))
        state={'quote':q,'history':h,'running':True,'mode':'SANDBOX' if 'sandbox' in settings.endpoint else 'PRODUCTION'}
        quality=data_quality(state,settings)
        print(json.dumps({'ticker':ticker,'endpoint':settings.endpoint,'price':q['last_price'],
              'price_time_utc':q['price_time_utc'],'quote_time_utc':q['quote_time_utc'],
              'stock_delay_minutes':quality['delay_minutes'],'delay_evidence':quality['delay_source'],
              'closed_1m_bars':len(h['candles']),'last_bar_utc':h['last_bar_utc'],
              'live_forecast_ready':quality['forecast_ready'],'pause_reasons':quality['reasons']},indent=2))
        print('Stock/candle connection check completed. Full option permissions are checked in the dashboard.')
        return 0
    except Exception as exc:
        print('Connection check failed: '+safe_error(exc));return 2


if __name__=='__main__': raise SystemExit(main())
