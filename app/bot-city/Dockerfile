FROM python:3.12-alpine@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7

# Fixed packages in the supported Alpine release; Python stays on 3.12.
RUN apk add --no-cache --upgrade zlib=1.4-r0 krb5-libs=1.22.3-r0 \
    libcrypto3=3.5.9-r1 libssl3=3.5.9-r1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BOT_CITY_MODE=paper-alpaca

WORKDIR /app

RUN addgroup -S -g 10001 botcity && \
    adduser -S -D -u 10001 -G botcity botcity

COPY requirements.txt requirements-runtime.lock ./
RUN pip install --only-binary=:all: --no-cache-dir -r requirements.txt -c requirements-runtime.lock

# Package installation is complete; omit installers from the runtime.
RUN python -m pip check && python -m pip uninstall -y pip && rm -rf /usr/local/lib/python3.12/ensurepip

COPY . .

RUN mkdir -p /app/state /app/dashboard && \
    chown -R botcity:botcity /app

USER botcity

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "healthcheck.py"]

STOPSIGNAL SIGTERM

CMD ["python", "run_bots.py", "--no-dashboard"]
