from confirmation import analyze_confirmation

def base():
    return {'spot':600,'net_gex_usd_per_1pct':-1000,'net_vex_delta_shares_per_vol_point':500,'net_charm_delta_shares_per_day':-30,'net_delta_proxy_shares':100}

def test_compression_not_directional():
    r=analyze_confirmation({'status':'COMPRESSION','volume_ratio':0.7},base())
    assert r['direction']=='UNKNOWN' and r['confirmation_score']==15

def test_expansion_down():
    r=analyze_confirmation({'status':'EXPANSION','price_direction':'DOWN','volume_ratio':1.8},base(),flow_direction='SELL')
    assert r['status']=='EXPANSION DOWN' and r['confirmation_score']==70

def test_breakout_and_one_minute():
    bars=[{'close':x,'closed':True} for x in (100,101,102,103)]
    r=analyze_confirmation({'status':'BREAKOUT WATCH','breakout_direction':'UP','volume_ratio':1.7},base(),one_minute_candles=bars)
    assert r['direction']=='UP' and r['confirmation_score']==65

def test_unavailable_suppresses():
    assert analyze_confirmation({'status':'UNAVAILABLE'},base())['status']=='UNAVAILABLE'

def test_failed_breakout():
    v={'status':'BREAKOUT WATCH','breakout_direction':'DOWN','volume_ratio':1.6}
    r=analyze_confirmation(v,base(),previous_volatility={'status':'BREAKOUT WATCH','breakout_direction':'UP'})
    assert r['status']=='FAILED BREAKOUT' and r['direction']=='UNKNOWN'
