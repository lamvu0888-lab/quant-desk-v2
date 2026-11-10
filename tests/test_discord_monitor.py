from unittest.mock import patch, Mock
from discord_monitor import send, send_test

def test_webhook_rejects_non_discord_url():
    try:
        send('http://example.com', {})
        assert False
    except ValueError:
        pass

def test_webhook_no_mentions():
    with patch('discord_monitor.requests.post', return_value=Mock(status_code=204)) as post:
        send('https://discord.com/api/webhooks/123/token', {'symbol':'SPY','status':'GET READY','bias':'BULLISH','score':75,'scope':'0DTE','price':600,'bar':'2026-11-10T15:00:00+00:00','notes':'Test'})
        assert post.call_args.kwargs['json']['allowed_mentions']=={'parse':[]}

def test_send_test_message():
    with patch('discord_monitor.requests.post', return_value=Mock(status_code=204)) as post:
        send_test('https://discord.com/api/webhooks/123/token')
        assert 'test successful' in post.call_args.kwargs['json']['content'].lower()
        assert post.call_args.kwargs['json']['allowed_mentions']=={'parse':[]}
