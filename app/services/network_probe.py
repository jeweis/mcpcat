"""独立子进程中的网络诊断，仅使用标准库，不读取应用配置。"""

import http.client
import socket
import sys
from urllib.parse import urlsplit


def probe(kind: str, target: str, port: int, timeout: float) -> None:
    """检查 DNS、TCP 或 HTTP；输出交给父进程统一脱敏。"""
    socket.setdefaulttimeout(timeout)
    if kind == "dns":
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(target, None)})
        print("\n".join(addresses), flush=True)
    elif kind == "tcp":
        with socket.create_connection((target, port), timeout=timeout) as connection:
            print(f"TCP connected: {connection.getpeername()}", flush=True)
    elif kind == "http":
        url = urlsplit(target)
        factory = (
            http.client.HTTPSConnection
            if url.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = factory(url.hostname, url.port, timeout=timeout)
        try:
            path = url.path or "/"
            if url.query:
                path += "?" + url.query
            connection.request(
                "HEAD", path, headers={"User-Agent": "mcpcat-diagnostics"}
            )
            response = connection.getresponse()
            print(f"HTTP {response.status} {response.reason}", flush=True)
            for name in ("Content-Type", "Location", "Server"):
                if value := response.getheader(name):
                    print(f"{name}: {value}", flush=True)
            if response.status >= 400:
                raise RuntimeError(f"HTTP status {response.status}")
        finally:
            connection.close()


if __name__ == "__main__":
    try:
        probe(sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]))
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
