from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from bots.errors import SafetyHalt
from bots.transport import AlpacaSession, PROXY, configure_transport, proxy_configuration


@pytest.mark.parametrize('url', ['http://paper-api.alpaca.markets/v2/account', 'https://api.alpaca.markets/v2/account',
    'https://evil.example/', 'https://paper-api.alpaca.markets.evil.example/', 'https://127.0.0.1/',
    'https://169.254.169.254/', 'https://paper-api.alpaca.markets:8443/',
    'https://user:secret@paper-api.alpaca.markets/', 'https://paper-api.alpaca.markets/#fragment'])
def test_request_destinations_blocked_before_adapter(url, monkeypatch):
    adapter = Mock(side_effect=AssertionError('must not send'))
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', adapter)
    with pytest.raises(SafetyHalt):
        AlpacaSession(PROXY).get(url, headers={'APCA-API-SECRET-KEY': 'test-secret'})
    adapter.assert_not_called()


@pytest.mark.parametrize('kwargs', [dict(verify=False), dict(verify='/tmp/ca.pem'), dict(cert='/tmp/cert'),
    dict(proxies={'https': 'http://evil:3128'}), dict(headers={'Host': 'evil.example'}),
    dict(headers={'Proxy-Authorization': 'secret'})])
def test_tls_proxy_header_overrides_fail_closed(kwargs, monkeypatch):
    adapter = Mock(side_effect=AssertionError('must not send'))
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', adapter)
    with pytest.raises(SafetyHalt):
        AlpacaSession(PROXY).get('https://paper-api.alpaca.markets/v2/account', **kwargs)
    adapter.assert_not_called()


@pytest.mark.parametrize('target', ['https://evil.example/', 'https://data.alpaca.markets/',
    'https://paper-api.alpaca.markets/v2/next', 'http://paper-api.alpaca.markets/'])
def test_redirects_never_forward_credentials(target, monkeypatch):
    calls = []
    def send(adapter, request, **kwargs):
        calls.append((request, kwargs))
        response = requests.Response()
        response.status_code = 302
        response.headers['Location'] = target
        response._content = b''
        response.request = request
        response.url = request.url
        return response
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    with pytest.raises(SafetyHalt, match='redirect'):
        AlpacaSession(PROXY).get('https://paper-api.alpaca.markets/v2/account',
            headers={'APCA-API-SECRET-KEY': 'test-secret'})
    assert len(calls) == 1
    assert calls[0][1]['verify'] is True
    assert calls[0][1]['proxies'] == {'https': PROXY}


def test_network_error_has_no_credentials_or_fallback(monkeypatch):
    send = Mock(side_effect=requests.exceptions.ProxyError('secret-key-in-untrusted-error'))
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    with pytest.raises(SafetyHalt) as exc:
        AlpacaSession(PROXY).get('https://paper-api.alpaca.markets/v2/account')
    assert 'secret-key' not in str(exc.value)
    assert send.call_count == 1


@pytest.mark.parametrize('key', ['HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY', 'https_proxy',
    'no_proxy', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'SSL_CERT_FILE', 'APCA_API_BASE_URL'])
def test_ambient_configuration_is_rejected(key, monkeypatch):
    monkeypatch.setenv('BOT_CITY_EGRESS_MODE', 'proxy')
    monkeypatch.setenv('BOT_CITY_PROXY_URL', PROXY)
    monkeypatch.setenv(key, 'unreviewed')
    with pytest.raises(SafetyHalt, match='overrides'):
        proxy_configuration()


def test_configuration_default_disabled(monkeypatch):
    monkeypatch.delenv('BOT_CITY_EGRESS_MODE', raising=False)
    with pytest.raises(SafetyHalt):
        proxy_configuration()


def test_sdk_has_explicit_session_and_no_retry():
    original = Mock()
    client = SimpleNamespace(_session=original, _retry=3)
    configure_transport(client, PROXY)
    assert client._retry == 0
    assert isinstance(client._session, AlpacaSession)
    assert not client._session.trust_env
    original.close.assert_called_once()


def test_invalid_startup_never_acquires_lease(monkeypatch):
    import run_bots
    monkeypatch.setattr('sys.argv', ['run_bots.py', '--no-dashboard'])
    monkeypatch.setattr(run_bots, 'load_dotenv', lambda: None)
    monkeypatch.setattr(run_bots, 'configure_logging', lambda: None)
    monkeypatch.setattr(run_bots, 'load_config', lambda path: SimpleNamespace(broker='alpaca', paper=True))
    monkeypatch.delenv('BOT_CITY_EGRESS_MODE', raising=False)
    lease = Mock(side_effect=AssertionError('must not acquire ownership'))
    monkeypatch.setattr(run_bots, 'configured_lease', lease)
    assert run_bots.main() == 2
    lease.assert_not_called()


def test_actual_sdk_clients_use_guarded_transport(monkeypatch):
    import os
    from bots.brokers import AlpacaBroker
    for name in list(os.environ):
        if any(token in name.upper() for token in ('PROXY', 'CA_BUNDLE', 'SSL_CERT', 'APCA_API_BASE', 'APCA_API_DATA', 'ALLOW_LIVE')):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('BOT_CITY_EGRESS_MODE', 'proxy')
    monkeypatch.setenv('BOT_CITY_PROXY_URL', PROXY)
    monkeypatch.setenv('ALPACA_API_KEY', 'not-a-real-key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'not-a-real-secret')
    broker = AlpacaBroker()
    for client in (broker.trading, broker.stocks, broker.opts):
        assert isinstance(client._session, AlpacaSession)
        assert client._retry == 0
        assert client._base_url.value.startswith('https://')


def test_remote_error_body_cannot_leak_into_recovery_or_logs(monkeypatch):
    def send(adapter, request, **kwargs):
        response = requests.Response()
        response.status_code = 500
        response._content = b'upstream echoed APCA-API-SECRET-KEY: sensitive-test-value'
        response.request = request
        response.url = request.url
        return response
    monkeypatch.setattr(requests.adapters.HTTPAdapter, 'send', send)
    with pytest.raises(SafetyHalt) as exc:
        AlpacaSession(PROXY).get('https://paper-api.alpaca.markets/v2/account')
    assert 'sensitive-test-value' not in str(exc.value)
    assert 'HTTP error' in str(exc.value)
