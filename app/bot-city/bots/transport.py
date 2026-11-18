"""Explicit Alpaca-only HTTPS sessions; no ambient proxy/CA/redirect overrides."""
import os
from urllib.parse import urlsplit

import requests
from .errors import SafetyHalt

ALLOWED = frozenset({'paper-api.alpaca.markets', 'data.alpaca.markets'})
PROXY = 'http://alpaca-egress.bot-city.internal:3128'


def proxy_configuration():
    mode = os.getenv('BOT_CITY_EGRESS_MODE', 'disabled')
    if mode != 'proxy' or os.getenv('BOT_CITY_PROXY_URL') != PROXY:
        raise SafetyHalt('reviewed Bot City proxy configuration is required')
    forbidden = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy',
                 'NO_PROXY', 'no_proxy', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'SSL_CERT_FILE',
                 'SSL_CERT_DIR', 'APCA_API_BASE_URL', 'APCA_API_DATA_URL')
    if any(os.getenv(key) for key in forbidden):
        raise SafetyHalt('ambient proxy, bypass, endpoint or TLS overrides are forbidden')
    return PROXY


class AlpacaSession(requests.Session):
    def __init__(self, proxy):
        super().__init__()
        if proxy != PROXY:
            raise SafetyHalt('unapproved proxy')
        self.trust_env = False
        self.proxy_url = proxy

    def send(self, request, **kwargs):
        try:
            url = urlsplit(request.url)
            if (url.scheme != 'https' or url.hostname not in ALLOWED or url.port not in (None, 443)
                    or url.username is not None or url.password is not None or url.fragment
                    or '\\' in request.url or any(ord(c) < 33 for c in request.url)):
                raise ValueError()
            if any(name.lower() in {'host', 'proxy-authorization', 'proxy-connection'} for name in request.headers):
                raise ValueError()
            if kwargs.get('verify', True) is not True or kwargs.get('cert'):
                raise ValueError()
            if kwargs.get('proxies') not in (None, {}, {'https': self.proxy_url}):
                raise ValueError()
        except (ValueError, TypeError):
            raise SafetyHalt('outbound request violates transport policy') from None
        kwargs.update(proxies={'https': self.proxy_url}, verify=True, cert=None,
                      allow_redirects=False, timeout=(5, 15))
        try:
            response = super().send(request, **kwargs)
        except requests.RequestException:
            # SDK exceptions may contain URLs/headers; never copy them into logs.
            raise SafetyHalt('Alpaca transport failed; no direct fallback') from None
        if 300 <= response.status_code < 400:
            response.close()
            raise SafetyHalt('Alpaca redirect rejected')
        if response.status_code >= 400:
            response.close()
            raise SafetyHalt('Alpaca HTTP error; reconciliation required')
        return response


def configure_transport(client, proxy):
    old = client._session
    client._session = AlpacaSession(proxy)
    # Retrying a submission on 429/5xx can create an uncertain operation. The
    # durable broker path reconciles its stable ID instead of SDK-level retries.
    client._retry = 0
    old.close()
