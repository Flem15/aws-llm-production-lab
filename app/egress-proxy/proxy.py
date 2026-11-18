"""IPv4-only CONNECT proxy: exact destinations, pinned DNS, matching TLS SNI.

No TLS interception, application credentials, or destination configuration.
"""
import asyncio
import ipaddress
import json
import signal
import socket
import struct

ALLOWED = frozenset({'paper-api.alpaca.markets', 'data.alpaca.markets'})
DENIED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
    '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24', '192.0.2.0/24',
    '192.88.99.0/24', '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24',
    '203.0.113.0/24', '224.0.0.0/4', '240.0.0.0/4'))
HEADER_LIMIT = 8192
HELLO_LIMIT = 65536
IDLE_TIMEOUT = 30
LIFETIME = 300
MAX_CONNECTIONS = 64


class Denied(Exception):
    pass


def event(name):
    # Never log user input, exception strings, addresses, headers or payloads.
    print(json.dumps({'event': name}), flush=True)


def destination(raw):
    if len(raw) > HEADER_LIMIT or not raw.endswith(b'\r\n\r\n'):
        raise Denied()
    lines = raw[:-4].split(b'\r\n')
    parts = lines[0].split(b' ')
    if len(parts) != 3 or parts[0] != b'CONNECT' or parts[2] not in (b'HTTP/1.0', b'HTTP/1.1'):
        raise Denied()
    authority = parts[1].decode('ascii')
    if authority not in {host + ':443' for host in ALLOWED}:
        raise Denied()
    seen = set()
    for line in lines[1:]:
        key, sep, value = line.partition(b':')
        key = key.lower()
        if not sep or key in seen or key not in {b'host', b'proxy-connection'}:
            raise Denied()  # Includes credentials, bodies, transfer encoding and smuggling.
        seen.add(key)
        if key == b'host' and value.strip() != parts[1]:
            raise Denied()
        if key == b'proxy-connection' and value.strip().lower() != b'keep-alive':
            raise Denied()
    return authority[:-4]


def checked_ips(records):
    addresses = set()
    for family, kind, proto, canon, sockaddr in records:
        ip = ipaddress.ip_address(sockaddr[0])
        if (family != socket.AF_INET or ip.version != 4 or not ip.is_global or
                any(ip in network for network in DENIED_NETWORKS)):
            raise Denied()
        addresses.add(str(ip))
    if not addresses:
        raise Denied()
    return sorted(addresses)


async def resolve_ipv4(host):
    records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
        host, 443, family=socket.AF_INET, type=socket.SOCK_STREAM), 5)
    return checked_ips(records)


async def connect_ipv4(ip):
    # Pass only the already validated numeric address, never the hostname again.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await asyncio.wait_for(asyncio.get_running_loop().sock_connect(sock, (ip, 443)), 5)
        if sock.getpeername()[0] != ip:
            raise Denied()
        return await asyncio.open_connection(sock=sock)
    except BaseException:
        sock.close()
        raise


def hello_name(body):
    """Strict bounded ClientHello parser; reject ECH and duplicate extensions."""
    def take(data, at, length):
        if at + length > len(data):
            raise Denied()
        return data[at:at + length], at + length
    def vector(data, at, width):
        size, at = take(data, at, width)
        return take(data, at, int.from_bytes(size, 'big'))
    version, at = take(body, 0, 2)
    if version != b'\x03\x03':
        raise Denied()
    _, at = take(body, at, 32)
    _, at = vector(body, at, 1)
    suites, at = vector(body, at, 2)
    compression, at = vector(body, at, 1)
    if not suites or len(suites) % 2 or compression != b'\x00':
        raise Denied()
    extensions, at = vector(body, at, 2)
    if at != len(body):
        raise Denied()
    at, seen, name = 0, set(), None
    while at < len(extensions):
        kind, at = take(extensions, at, 2)
        value, at = vector(extensions, at, 2)
        if kind in seen or kind == b'\xfe\x0d':
            raise Denied()
        seen.add(kind)
        if kind == b'\x00\x00':
            names, end = vector(value, 0, 2)
            if end != len(value) or not names or names[0] != 0:
                raise Denied()
            raw, end = vector(names, 1, 2)
            if end != len(names):
                raise Denied()
            name = raw.decode('ascii')
    if name not in ALLOWED:
        raise Denied()
    return name


async def client_hello(reader, host):
    wire, handshake = bytearray(), bytearray()
    while len(wire) < HELLO_LIMIT:
        header = await asyncio.wait_for(reader.readexactly(5), 5)
        kind, major, minor, length = struct.unpack('!BBBH', header)
        if kind != 22 or major != 3 or minor not in (1, 3) or not 0 < length <= 16384:
            raise Denied()
        record = await asyncio.wait_for(reader.readexactly(length), 5)
        wire.extend(header + record)
        handshake.extend(record)
        if len(wire) > HELLO_LIMIT or (handshake and handshake[0] != 1):
            raise Denied()
        if len(handshake) >= 4:
            size = int.from_bytes(handshake[1:4], 'big')
            if size > HELLO_LIMIT - 4:
                raise Denied()
            if len(handshake) >= size + 4:
                if len(handshake) != size + 4 or hello_name(bytes(handshake[4:])) != host:
                    raise Denied()
                return bytes(wire)
    raise Denied()


async def close(writer):
    if writer:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except (Exception, asyncio.CancelledError):
            pass


class Proxy:
    def __init__(self):
        self.tasks = set()
        self.stopping = False

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        if self.stopping or len(self.tasks) >= MAX_CONNECTIONS:
            writer.write(b'HTTP/1.1 503 Unavailable\r\nConnection: close\r\n\r\n')
            await close(writer)
            event('proxy_denied')
            return
        self.tasks.add(task)
        upstream = None
        established = False
        try:
            async with asyncio.timeout(LIFETIME):
                raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 5)
                host = destination(raw)
                addresses = await resolve_ipv4(host)
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
                established = True
                hello = await client_hello(reader, host)
                remote, upstream = await connect_ipv4(addresses[0])
                upstream.write(hello)
                await upstream.drain()
                event('proxy_allowed')
                async def pump(source, target):
                    while True:
                        data = await asyncio.wait_for(source.read(16384), IDLE_TIMEOUT)
                        if not data:
                            return
                        target.write(data)
                        await asyncio.wait_for(target.drain(), IDLE_TIMEOUT)
                tasks = [asyncio.create_task(pump(reader, upstream)), asyncio.create_task(pump(remote, writer))]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for result in done:
                        result.result()
                finally:
                    for stream in tasks:
                        stream.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.IncompleteReadError as exc:
            if exc.partial or established:
                event('proxy_denied')
        except (Denied, UnicodeError, ValueError, asyncio.LimitOverrunError):
            event('proxy_denied')
            if not established:
                writer.write(b'HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n')
        except Exception:
            event('proxy_upstream_error')
            if not established:
                writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
        finally:
            await close(upstream)
            await close(writer)
            self.tasks.discard(task)

    async def stop(self):
        self.stopping = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    proxy = Proxy()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    server = await asyncio.start_server(proxy.handle, '0.0.0.0', 3128, limit=HEADER_LIMIT)
    event('proxy_started')
    await stop.wait()
    server.close()
    await proxy.stop()
    await server.wait_closed()
    event('proxy_stopped')


if __name__ == '__main__':
    asyncio.run(main())
