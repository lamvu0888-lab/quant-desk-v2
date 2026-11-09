# V1.4 Dark Dashboard

Launch with `START_DASHBOARD.cmd`. The dark theme uses high-contrast labels and metric cards. Each main chart section has its own timeframe selector and on-demand explanation bubbles. Timeframe selection aggregates *saved reports*, not historical OHLCV candles. GEX strike charts and the confirmation panel explicitly indicate when a selected timeframe cannot be reconstructed from the available report data. No execution API is included. Live Webull market data still requires implementing `my_webull_adapter.py`.
