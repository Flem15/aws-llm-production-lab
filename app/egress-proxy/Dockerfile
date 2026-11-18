FROM python:3.12-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7
# Standard-library-only proxy; same Python 3.12, smaller supported OS runtime.
RUN apk add --no-cache --upgrade zlib=1.4-r0 krb5-libs=1.22.3-r0

# The proxy has no third-party Python dependencies; do not ship package installers.
RUN python -m pip uninstall -y pip && rm -rf /usr/local/lib/python3.12/ensurepip
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN addgroup -S -g 10001 proxyworker && adduser -S -D -H -u 10001 -G proxyworker proxyworker
COPY proxy.py ./proxy.py
USER proxyworker
EXPOSE 3128
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1',3128),2).close()"]
STOPSIGNAL SIGTERM
CMD ["python", "proxy.py"]
