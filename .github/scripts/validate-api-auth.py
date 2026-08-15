#!/usr/bin/env python3

import datetime
import hashlib
import hmac
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request


SERVICE = "execute-api"


def require_environment(
    name: str,
) -> str:
    value = (
        os.environ
        .get(
            name,
            "",
        )
        .strip()
    )

    if not value:
        raise RuntimeError(
            f"Required environment "
            f"variable {name} "
            f"is missing"
        )

    return value


def sha256_hex(
    value: bytes,
) -> str:
    return hashlib.sha256(
        value
    ).hexdigest()


def sign(
    key: bytes,
    message: str,
) -> bytes:
    return hmac.new(
        key,
        message.encode(
            "utf-8",
        ),
        hashlib.sha256,
    ).digest()


def derive_signing_key(
    secret_key: str,
    date_stamp: str,
    region: str,
) -> bytes:
    date_key = sign(
        (
            "AWS4"
            + secret_key
        ).encode(
            "utf-8",
        ),
        date_stamp,
    )

    region_key = sign(
        date_key,
        region,
    )

    service_key = sign(
        region_key,
        SERVICE,
    )

    return sign(
        service_key,
        "aws4_request",
    )


def perform_request(
    request:
        urllib.request.Request,
) -> tuple[
    int,
    str,
    dict[str, str],
]:
    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            return (
                response.status,
                response
                .read()
                .decode(
                    "utf-8",
                ),
                {
                    key.lower():
                        value
                    for key, value
                    in response
                    .headers
                    .items()
                },
            )

    except urllib.error.HTTPError as error:
        return (
            error.code,
            error
            .read()
            .decode(
                "utf-8",
            ),
            {
                key.lower():
                    value
                for key, value
                in error.headers.items()
            },
        )


def signed_request(
    *,
    url: str,
    region: str,
    access_key: str,
    secret_key: str,
    session_token: str,
    payload: bytes,
    content_type: str,
) -> tuple[
    int,
    str,
    dict[str, str],
]:
    parsed = (
        urllib.parse
        .urlparse(
            url,
        )
    )

    host = parsed.netloc

    canonical_uri = (
        urllib.parse.quote(
            parsed.path or "/",
            safe="/-_.~",
        )
    )

    now = (
        datetime.datetime.now(
            datetime.timezone.utc,
        )
    )

    amz_date = (
        now.strftime(
            "%Y%m%dT%H%M%SZ",
        )
    )

    date_stamp = (
        now.strftime(
            "%Y%m%d",
        )
    )

    payload_hash = (
        sha256_hex(
            payload,
        )
    )

    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{session_token}\n"
    )

    signed_headers = (
        "content-type;"
        "host;"
        "x-amz-date;"
        "x-amz-security-token"
    )

    canonical_request = (
        "\n".join([
            "POST",
            canonical_uri,
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        ])
    )

    credential_scope = (
        f"{date_stamp}/"
        f"{region}/"
        f"{SERVICE}/"
        "aws4_request"
    )

    string_to_sign = (
        "\n".join([
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            sha256_hex(
                canonical_request
                .encode(
                    "utf-8",
                )
            ),
        ])
    )

    derived_key = (
        derive_signing_key(
            secret_key,
            date_stamp,
            region,
        )
    )

    signature = (
        hmac.new(
            derived_key,
            string_to_sign.encode(
                "utf-8",
            ),
            hashlib.sha256,
        )
        .hexdigest()
    )

    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/"
        f"{credential_scope}, "
        f"SignedHeaders="
        f"{signed_headers}, "
        f"Signature="
        f"{signature}"
    )

    request = (
        urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization":
                    authorization,
                "Content-Type":
                    content_type,
                "Host":
                    host,
                "X-Amz-Date":
                    amz_date,
                "X-Amz-Security-Token":
                    session_token,
            },
        )
    )

    return perform_request(
        request,
    )


def assert_error_response(
    *,
    status: int,
    body_text: str,
    headers:
        dict[str, str],
    expected_status: int,
    expected_code: str,
) -> str:
    if (
        status !=
        expected_status
    ):
        raise RuntimeError(
            f"Expected HTTP "
            f"{expected_status} "
            f"but received "
            f"{status}. "
            f"Response: "
            f"{body_text}"
        )

    try:
        body = json.loads(
            body_text,
        )
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Expected structured "
            "JSON error response"
        ) from error

    api_error = body.get(
        "error",
        {},
    )

    if (
        api_error.get(
            "code",
        )
        !=
        expected_code
    ):
        raise RuntimeError(
            f"Expected error code "
            f"{expected_code} but "
            f"received "
            f"{api_error.get('code')}"
        )

    request_id = (
        api_error.get(
            "requestId",
        )
    )

    if (
        not isinstance(
            request_id,
            str,
        )
        or
        not request_id
        or
        request_id
        ==
        "unknown"
    ):
        raise RuntimeError(
            "Structured error "
            "did not contain "
            "a usable requestId"
        )

    response_request_id = (
        headers.get(
            "x-request-id",
        )
    )

    if (
        response_request_id
        !=
        request_id
    ):
        raise RuntimeError(
            "Error-body requestId "
            "does not match "
            "x-request-id "
            "response header"
        )

    return request_id


def main() -> int:
    api_endpoint = (
        require_environment(
            "API_ENDPOINT",
        )
        .rstrip("/")
    )

    region = (
        require_environment(
            "AWS_REGION",
        )
    )

    access_key = (
        require_environment(
            "AWS_ACCESS_KEY_ID",
        )
    )

    secret_key = (
        require_environment(
            "AWS_SECRET_ACCESS_KEY",
        )
    )

    session_token = (
        require_environment(
            "AWS_SESSION_TOKEN",
        )
    )

    expected_version = (
        require_environment(
            "IMAGE_TAG",
        )
    )

    evidence_dir = pathlib.Path(
        os.environ.get(
            "EVIDENCE_DIR",
            "release-evidence",
        )
    )

    evidence_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    invoke_url = (
        f"{api_endpoint}/invoke"
    )

    unsigned_payload = (
        json.dumps({
            "prompt":
                "unsigned-day12-test",
        })
        .encode(
            "utf-8",
        )
    )

    unsigned_status, _, _ = (
        perform_request(
            urllib.request.Request(
                invoke_url,
                data=
                    unsigned_payload,
                method=
                    "POST",
                headers={
                    "Content-Type":
                        "application/json",
                },
            )
        )
    )

    if unsigned_status not in (
        401,
        403,
    ):
        raise RuntimeError(
            "Unsigned POST /invoke "
            "was not rejected. "
            f"Received HTTP "
            f"{unsigned_status}"
        )

    print(
        "PASS: unsigned "
        "POST /invoke was rejected"
    )

    prompt = (
        "authenticated-day12-"
        + expected_version[:12]
    )

    valid_payload = (
        json.dumps(
            {
                "prompt":
                    prompt,
            },
            separators=(
                ",",
                ":",
            ),
        )
        .encode(
            "utf-8",
        )
    )

    (
        valid_status,
        valid_body_text,
        valid_headers,
    ) = signed_request(
        url=
            invoke_url,
        region=
            region,
        access_key=
            access_key,
        secret_key=
            secret_key,
        session_token=
            session_token,
        payload=
            valid_payload,
        content_type=
            "application/json",
    )

    if valid_status != 200:
        raise RuntimeError(
            "Valid authenticated "
            "request failed. "
            f"HTTP "
            f"{valid_status}: "
            f"{valid_body_text}"
        )

    valid_body = json.loads(
        valid_body_text,
    )

    if (
        valid_body.get(
            "version",
        )
        !=
        expected_version
    ):
        raise RuntimeError(
            "Valid response "
            "version does not "
            "match immutable SHA"
        )

    if (
        valid_body.get(
            "prompt",
        )
        !=
        prompt
    ):
        raise RuntimeError(
            "Valid response "
            "prompt mismatch"
        )

    expected_response = (
        f"demo-response-for: "
        f"{prompt}"
    )

    if (
        valid_body.get(
            "response",
        )
        !=
        expected_response
    ):
        raise RuntimeError(
            "Valid inference "
            "response mismatch"
        )

    valid_request_id = (
        valid_headers.get(
            "x-request-id",
        )
    )

    if not valid_request_id:
        raise RuntimeError(
            "Valid response "
            "did not contain "
            "x-request-id"
        )

    print(
        "PASS: valid "
        "SigV4 request returned "
        "the expected release"
    )

    negative_results = []

    test_cases = [
        {
            "name":
                "content_type",
            "payload":
                json.dumps({
                    "prompt":
                        "bad type",
                }).encode(
                    "utf-8",
                ),
            "content_type":
                "text/plain",
            "status":
                415,
            "code":
                "UNSUPPORTED_MEDIA_TYPE",
        },
        {
            "name":
                "malformed_json",
            "payload":
                b'{"prompt":',
            "content_type":
                "application/json",
            "status":
                400,
            "code":
                "MALFORMED_JSON",
        },
        {
            "name":
                "missing_prompt",
            "payload":
                b'{}',
            "content_type":
                "application/json",
            "status":
                400,
            "code":
                "INVALID_REQUEST",
        },
        {
            "name":
                "additional_property",
            "payload":
                json.dumps({
                    "prompt":
                        "valid",
                    "unexpected":
                        True,
                }).encode(
                    "utf-8",
                ),
            "content_type":
                "application/json",
            "status":
                400,
            "code":
                "INVALID_REQUEST",
        },
        {
            "name":
                "long_prompt",
            "payload":
                json.dumps({
                    "prompt":
                        "a" * 4001,
                }).encode(
                    "utf-8",
                ),
            "content_type":
                "application/json",
            "status":
                400,
            "code":
                "PROMPT_TOO_LONG",
        },
        {
            "name":
                "oversized_body",
            "payload":
                json.dumps({
                    "prompt":
                        "a"
                        * (
                            20
                            * 1024
                        ),
                }).encode(
                    "utf-8",
                ),
            "content_type":
                "application/json",
            "status":
                413,
            "code":
                "PAYLOAD_TOO_LARGE",
        },
    ]

    for test_case in test_cases:
        (
            status,
            body_text,
            headers,
        ) = signed_request(
            url=
                invoke_url,
            region=
                region,
            access_key=
                access_key,
            secret_key=
                secret_key,
            session_token=
                session_token,
            payload=
                test_case[
                    "payload"
                ],
            content_type=
                test_case[
                    "content_type"
                ],
        )

        request_id = (
            assert_error_response(
                status=
                    status,
                body_text=
                    body_text,
                headers=
                    headers,
                expected_status=
                    test_case[
                        "status"
                    ],
                expected_code=
                    test_case[
                        "code"
                    ],
            )
        )

        negative_results.append({
            "test":
                test_case[
                    "name"
                ],
            "status":
                status,
            "code":
                test_case[
                    "code"
                ],
            "requestIdPresent":
                bool(
                    request_id,
                ),
        })

        print(
            "PASS: "
            f"{test_case['name']} "
            "negative request "
            "was rejected correctly"
        )

    evidence = {
        "unsignedStatus":
            unsigned_status,
        "authenticatedStatus":
            valid_status,
        "authorization":
            "AWS_IAM_SIGV4",
        "route":
            "POST /invoke",
        "version":
            expected_version,
        "validRequestIdPresent":
            bool(
                valid_request_id,
            ),
        "negativeTests":
            negative_results,
    }

    (
        evidence_dir
        /
        "api-auth-validation.json"
    ).write_text(
        json.dumps(
            evidence,
            indent=2,
        )
        + "\n",
        encoding=
            "utf-8",
    )

    print(
        "PASS: Day 12 live "
        "negative request matrix passed"
    )

    print(
        "PASS: sanitized "
        "API hardening evidence saved"
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
