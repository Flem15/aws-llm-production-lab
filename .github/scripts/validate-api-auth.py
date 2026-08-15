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


def require_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Required environment variable {name} is missing"
        )

    return value


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sign(
    key: bytes,
    message: str,
) -> bytes:
    return hmac.new(
        key,
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()


def signing_key(
    secret_key: str,
    date_stamp: str,
    region: str,
    service: str,
) -> bytes:
    date_key = sign(
        ("AWS4" + secret_key).encode("utf-8"),
        date_stamp,
    )

    region_key = sign(
        date_key,
        region,
    )

    service_key = sign(
        region_key,
        service,
    )

    return sign(
        service_key,
        "aws4_request",
    )


def perform_request(
    request: urllib.request.Request,
) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(
            request,
            timeout=30,
        ) as response:
            return (
                response.status,
                response.read().decode("utf-8"),
            )
    except urllib.error.HTTPError as error:
        return (
            error.code,
            error.read().decode("utf-8"),
        )


def main() -> int:
    api_endpoint = require_environment(
        "API_ENDPOINT"
    ).rstrip("/")

    region = require_environment(
        "AWS_REGION"
    )

    access_key = require_environment(
        "AWS_ACCESS_KEY_ID"
    )

    secret_key = require_environment(
        "AWS_SECRET_ACCESS_KEY"
    )

    session_token = require_environment(
        "AWS_SESSION_TOKEN"
    )

    expected_version = require_environment(
        "IMAGE_TAG"
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

    prompt = (
        "authenticated-release-validation-"
        + expected_version[:12]
    )

    payload_object = {
        "prompt": prompt,
    }

    payload = json.dumps(
        payload_object,
        separators=(",", ":"),
    ).encode("utf-8")

    invoke_url = (
        f"{api_endpoint}/invoke"
    )

    unsigned_request = urllib.request.Request(
        invoke_url,
        data=payload,
        method="POST",
        headers={
            "Content-Type":
                "application/json",
        },
    )

    unsigned_status, _ = perform_request(
        unsigned_request
    )

    if unsigned_status not in (401, 403):
        raise RuntimeError(
            "Unsigned POST /invoke was not rejected. "
            f"Received HTTP {unsigned_status}"
        )

    print(
        "PASS: unsigned POST /invoke was rejected"
    )

    parsed = urllib.parse.urlparse(
        invoke_url
    )

    host = parsed.netloc

    canonical_uri = (
        urllib.parse.quote(
            parsed.path or "/",
            safe="/-_.~",
        )
    )

    canonical_query_string = ""

    now = datetime.datetime.now(
        datetime.timezone.utc
    )

    amz_date = now.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    date_stamp = now.strftime(
        "%Y%m%d"
    )

    payload_hash = sha256_hex(
        payload
    )

    canonical_headers = (
        "content-type:application/json\n"
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

    canonical_request = "\n".join([
        "POST",
        canonical_uri,
        canonical_query_string,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    credential_scope = (
        f"{date_stamp}/"
        f"{region}/"
        f"{SERVICE}/"
        "aws4_request"
    )

    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        sha256_hex(
            canonical_request.encode(
                "utf-8"
            )
        ),
    ])

    derived_signing_key = signing_key(
        secret_key,
        date_stamp,
        region,
        SERVICE,
    )

    signature = hmac.new(
        derived_signing_key,
        string_to_sign.encode(
            "utf-8"
        ),
        hashlib.sha256,
    ).hexdigest()

    authorization_header = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/"
        f"{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    signed_request = urllib.request.Request(
        invoke_url,
        data=payload,
        method="POST",
        headers={
            "Authorization":
                authorization_header,
            "Content-Type":
                "application/json",
            "Host":
                host,
            "X-Amz-Date":
                amz_date,
            "X-Amz-Security-Token":
                session_token,
        },
    )

    signed_status, signed_body = (
        perform_request(
            signed_request
        )
    )

    if signed_status != 200:
        raise RuntimeError(
            "Authenticated POST /invoke failed. "
            f"Received HTTP {signed_status}. "
            f"Response: {signed_body}"
        )

    try:
        response = json.loads(
            signed_body
        )
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Authenticated response was not valid JSON"
        ) from error

    if response.get("version") != expected_version:
        raise RuntimeError(
            "Authenticated response version does not "
            "match the requested immutable image SHA"
        )

    if response.get("prompt") != prompt:
        raise RuntimeError(
            "Authenticated response prompt does not "
            "match the submitted prompt"
        )

    expected_response = (
        f"demo-response-for: {prompt}"
    )

    if (
        response.get("response")
        != expected_response
    ):
        raise RuntimeError(
            "Authenticated inference response does not "
            "match the expected demo response"
        )

    evidence = {
        "unsigned_status":
            unsigned_status,
        "authenticated_status":
            signed_status,
        "authorization":
            "AWS_IAM_SIGV4",
        "route":
            "POST /invoke",
        "version":
            response.get("version"),
        "prompt":
            prompt,
        "response_verified":
            True,
    }

    evidence_path = (
        evidence_dir
        / "api-auth-validation.json"
    )

    evidence_path.write_text(
        json.dumps(
            evidence,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        "PASS: SigV4-authenticated POST /invoke "
        "returned the expected immutable release"
    )

    print(
        "PASS: sanitized API authentication "
        "evidence recorded"
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(
            f"ERROR: {error}",
            file=sys.stderr,
        )

        sys.exit(1)
