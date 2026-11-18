import asyncio
import socket
import ssl
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxy

HOST = 'paper-api.alpaca.markets'


def hello(host=HOST):
    outgoing = ssl.MemoryBIO()
    stream = ssl.create_default_context().wrap_bio(ssl.MemoryBIO(), outgoing, server_hostname=host)
    with pytest.raises(ssl.SSLWantReadError):
        stream.do_handshake()
    return outgoing.read()


@pytest.mark.parametrize('authority', ['api.alpaca.markets:443', 'evil.example:443', '127.0.0.1:443',
    '169.254.169.254:443', '[::1]:443', 'paper-api.alpaca.markets:80',
    'paper-api.alpaca.markets.evil.example:443', 'paper-api.alpaca.markets.:443',
    'user:secret@paper-api.alpaca.markets:443', 'PAPER-api.alpaca.markets:443',
    'paper-api.alpaca.markets%00.evil:443'])
def test_destination_restrictions(authority):
    with pytest.raises(proxy.Denied):
        proxy.destination(f'CONNECT {authority} HTTP/1.1\r\n\r\n'.encode())


@pytest.mark.parametrize('header', [b'Authorization: secret', b'Proxy-Authorization: secret',
    b'Content-Length: 1', b'Transfer-Encoding: chunked', b'Host: evil.example',
    b'Host: paper-api.alpaca.markets:443\r\nHost: paper-api.alpaca.markets:443', b' x: y'])
def test_header_smuggling_and_credentials_rejected(header):
    with pytest.raises(proxy.Denied):
        proxy.destination(b'CONNECT paper-api.alpaca.markets:443 HTTP/1.1\r\n' + header + b'\r\n\r\n')


def test_only_connect_and_exact_hosts():
    for host in proxy.ALLOWED:
        assert proxy.destination(f'CONNECT {host}:443 HTTP/1.0\r\n\r\n'.encode()) == host
    with pytest.raises(proxy.Denied):
        proxy.destination(b'GET https://paper-api.alpaca.markets/v2/account HTTP/1.1\r\n\r\n')


def records(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in ips]


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.1.2.3', '169.254.169.254', '169.254.170.2',
    '100.64.0.1', '172.16.0.1', '192.168.1.1', '192.0.0.9', '192.0.2.1', '198.18.0.1',
    '198.51.100.1', '203.0.113.1', '224.0.0.1', '240.0.0.1', '0.0.0.0', '::1', '::ffff:8.8.8.8'])
def test_all_dns_answers_must_be_public_ipv4(ip):
    with pytest.raises(proxy.Denied):
        proxy.checked_ips(records('8.8.8.8', ip))


def test_dns_empty_rejected():
    with pytest.raises(proxy.Denied):
        proxy.checked_ips([])


def test_sni_and_fragmented_hello():
    async def run():
        wire = hello()
        body = wire[5:]
        fragmented = b''.join(b'\x16\x03\x03' + struct.pack('!H', len(part)) + part
                              for part in [body[:9], body[9:]])
        reader = asyncio.StreamReader()
        reader.feed_data(fragmented)
        reader.feed_eof()
        assert await proxy.client_hello(reader, HOST) == fragmented
        bad = asyncio.StreamReader()
        bad.feed_data(hello('data.alpaca.markets'))
        with pytest.raises(proxy.Denied):
            await proxy.client_hello(bad, HOST)
    asyncio.run(run())


@pytest.mark.parametrize('wire', [b'GET / HTTP/1.1\r\n', b'\x16\x03\x01\xff\xff',
    b'\x17\x03\x03\x00\x01x', b'\x16\x03\x01\x00\x05\x01\xff\xff\xffx'])
def test_plaintext_or_oversized_tls_denied(wire):
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(wire)
        reader.feed_eof()
        with pytest.raises((proxy.Denied, asyncio.IncompleteReadError)):
            await proxy.client_hello(reader, HOST)
    asyncio.run(run())


def test_dns_is_resolved_once_and_numeric_ip_is_pinned(monkeypatch, capsys):
    async def run():
        calls, connected = [], []
        loop = asyncio.get_running_loop()
        async def lookup(*args, **kwargs):
            calls.append(args)
            return records('8.8.8.8') if len(calls) == 1 else records('127.0.0.1')
        monkeypatch.setattr(loop, 'getaddrinfo', lookup)
        async def connect(ip):
            connected.append(ip)
            raise OSError('untrusted exception secret-token')
        monkeypatch.setattr(proxy, 'connect_ipv4', connect)
        service = proxy.Proxy()
        server = await asyncio.start_server(service.handle, '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            writer.write(f'CONNECT {HOST}:443 HTTP/1.1\r\n\r\n'.encode())
            await writer.drain()
            assert b'200' in await reader.readuntil(b'\r\n\r\n')
            writer.write(hello())
            await writer.drain()
            assert await reader.read() == b''
            await proxy.close(writer)
            assert len(calls) == 1 and connected == ['8.8.8.8']
        finally:
            server.close()
            await server.wait_closed()
            await service.stop()
    asyncio.run(run())
    assert 'secret-token' not in capsys.readouterr().out


def test_changed_dns_on_next_connection_rejected(monkeypatch):
    async def run():
        answers = iter([records('8.8.8.8'), records('169.254.169.254')])
        async def lookup(*args, **kwargs):
            return next(answers)
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', lookup)
        assert await proxy.resolve_ipv4(HOST) == ['8.8.8.8']
        with pytest.raises(proxy.Denied):
            await proxy.resolve_ipv4(HOST)
    asyncio.run(run())


@pytest.fixture
def tls_context(tmp_path):
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-subj', f'/CN={HOST}', '-addext', f'subjectAltName=DNS:{HOST}',
        '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return context, cert


def test_real_tls_tunnel_keeps_credentials_out_of_proxy_logs(monkeypatch, tls_context, capsys):
    async def run():
        received = []
        async def origin(reader, writer):
            try:
                received.append(await reader.readuntil(b'\r\n\r\n'))
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
                await writer.drain()
            finally:
                await proxy.close(writer)
        backend = await asyncio.start_server(origin, '127.0.0.1', 0, ssl=tls_context[0])
        async def resolve(host):
            return ['8.8.8.8']
        async def connect(ip):
            assert ip == '8.8.8.8'
            return await asyncio.open_connection('127.0.0.1', backend.sockets[0].getsockname()[1])
        monkeypatch.setattr(proxy, 'resolve_ipv4', resolve)
        monkeypatch.setattr(proxy, 'connect_ipv4', connect)
        service = proxy.Proxy()
        server = await asyncio.start_server(service.handle, '127.0.0.1', 0)
        writer = None
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            writer.write(f'CONNECT {HOST}:443 HTTP/1.0\r\n\r\n'.encode())
            await writer.drain()
            await reader.readuntil(b'\r\n\r\n')
            await writer.start_tls(ssl.create_default_context(cafile=str(tls_context[1])), server_hostname=HOST)
            writer.write(b'GET /v2/account HTTP/1.1\r\nHost: paper-api.alpaca.markets\r\nAPCA-API-SECRET-KEY: test-secret\r\n\r\n')
            await writer.drain()
            assert b'200 OK' in await reader.readuntil(b'\r\n\r\n')
            assert b'test-secret' in received[0]
        finally:
            await proxy.close(writer)
            server.close(); backend.close()
            await server.wait_closed(); await backend.wait_closed()
            await service.stop()
    asyncio.run(run())
    assert 'test-secret' not in capsys.readouterr().out


def test_tls_untrusted_certificate_fails_before_credentials(monkeypatch, tls_context):
    async def run():
        received = []
        async def origin(reader, writer):
            received.append(await reader.read(1000))
            await proxy.close(writer)
        server = await asyncio.start_server(origin, '127.0.0.1', 0, ssl=tls_context[0])
        try:
            with pytest.raises(ssl.SSLCertVerificationError):
                await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1],
                    ssl=ssl.create_default_context(), server_hostname=HOST)
            assert not received
            with pytest.raises(ssl.SSLCertVerificationError):
                await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1],
                    ssl=ssl.create_default_context(cafile=str(tls_context[1])), server_hostname='wrong.example')
            assert not received
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())


def test_shutdown_cancels_stalled_connections():
    async def run():
        service = proxy.Proxy()
        server = await asyncio.start_server(service.handle, '127.0.0.1', 0)
        reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
        writer.write(b'CONNECT ')  # Partial request must not prevent termination.
        await writer.drain()
        await asyncio.sleep(0)
        server.close()
        await asyncio.wait_for(service.stop(), 3)
        await server.wait_closed()
        try:
            assert await asyncio.wait_for(reader.read(), 1) in (b'', b'HTTP/1.1 503 Unavailable\r\nConnection: close\r\n\r\n')
        except ConnectionResetError:
            pass  # Closing a socket with unread input may produce RST instead of EOF.
        assert not service.tasks
        await proxy.close(writer)
    asyncio.run(run())


def test_process_sigterm_waits_for_exit():
    import time
    process = subprocess.Popen([sys.executable, str(Path(proxy.__file__))],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail('proxy exited before startup')
            try:
                with socket.create_connection(('127.0.0.1', 3128), timeout=.1):
                    break
            except OSError:
                time.sleep(.05)
        else:
            pytest.fail('proxy did not start')
    finally:
        process.terminate()
        try:
            output, errors = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            output, errors = process.communicate(timeout=5)
            pytest.fail('proxy required SIGKILL')
    assert process.returncode == 0
    assert 'proxy_stopped' in output
    assert not errors


def test_capacity_limit_rejects_instead_of_queueing(monkeypatch):
    async def run():
        monkeypatch.setattr(proxy, 'MAX_CONNECTIONS', 0)
        service = proxy.Proxy()
        server = await asyncio.start_server(service.handle, '127.0.0.1', 0)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
            assert b'503' in await reader.read()
            await proxy.close(writer)
            assert not service.tasks
        finally:
            server.close()
            await server.wait_closed()
            await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize('case', ['no-sni', 'duplicate', 'ech'])
def test_ambiguous_or_encrypted_client_hello_denied(case):
    name = HOST.encode()
    names = b'\x00' + len(name).to_bytes(2, 'big') + name
    value = len(names).to_bytes(2, 'big') + names
    sni = b'\x00\x00' + len(value).to_bytes(2, 'big') + value
    extensions = {'no-sni': b'', 'duplicate': sni + sni, 'ech': sni + b'\xfe\x0d\x00\x00'}[case]
    body = b'\x03\x03' + bytes(32) + b'\x00\x00\x02\x13\x01\x01\x00' + len(extensions).to_bytes(2, 'big') + extensions
    with pytest.raises(proxy.Denied):
        proxy.hello_name(body)
