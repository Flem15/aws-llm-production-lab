#!/usr/bin/env python3

import collections
import concurrent.futures
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request


def request_once(
    url: str,
) -> int:
    request = (
        urllib.request.Request(
            url,
            method="GET",
        )
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=10,
        ) as response:
            response.read()
            return (
                response.status
            )

    except urllib.error.HTTPError as error:
        error.read()
        return error.code

    except Exception:
        return 0


def run_burst(
    url: str,
    request_count: int,
) -> collections.Counter:
    with (
        concurrent.futures
        .ThreadPoolExecutor(
            max_workers=
                min(
                    request_count,
                    100,
                )
        )
        as executor
    ):
        statuses = list(
            executor.map(
                lambda _:
                    request_once(
                        url,
                    ),
                range(
                    request_count,
                ),
            )
        )

    return (
        collections.Counter(
            statuses,
        )
    )


def main() -> int:
    endpoint = (
        os.environ
        .get(
            "API_ENDPOINT",
            "",
        )
        .rstrip("/")
    )

    if not endpoint.startswith(
        "https://"
    ):
        raise RuntimeError(
            "API_ENDPOINT must be "
            "an HTTPS URL"
        )

    health_url = (
        f"{endpoint}/health"
    )

    aggregate = (
        collections.Counter()
    )

    throttled = False

    for request_count in (
        100,
        200,
        400,
    ):
        counts = run_burst(
            health_url,
            request_count,
        )

        aggregate.update(
            counts,
        )

        print(
            f"INFO: burst "
            f"{request_count}: "
            f"{dict(sorted(counts.items()))}"
        )

        if counts.get(
            429,
            0,
        ) > 0:
            throttled = True
            break

        time.sleep(2)

    if not throttled:
        raise RuntimeError(
            "No HTTP 429 response "
            "was observed after "
            "progressive request bursts"
        )

    evidence_directory = (
        pathlib.Path(
            "release-evidence"
        )
    )

    evidence_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    (
        evidence_directory
        /
        "api-rate-limit-validation.json"
    ).write_text(
        json.dumps(
            {
                "endpoint":
                    "/health",
                "expectedRateLimit":
                    10,
                "expectedBurstLimit":
                    20,
                "statusCounts": {
                    str(key):
                        value
                    for key, value
                    in sorted(
                        aggregate.items()
                    )
                },
                "throttlingObserved":
                    True,
            },
            indent=2,
        )
        + "\n",
        encoding=
            "utf-8",
    )

    print(
        "PASS: API Gateway "
        "throttling produced HTTP 429"
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(
            main()
        )
    except Exception as error:
        print(
            f"ERROR: {error}",
            file=sys.stderr,
        )

        sys.exit(1)
