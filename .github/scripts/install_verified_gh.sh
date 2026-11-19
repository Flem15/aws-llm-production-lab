#!/usr/bin/env bash
set -euo pipefail
# Official cli/cli v2.104.0 Linux amd64 archive, checksum reviewed in protected source.
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ]
TOOL_DIR=$(mktemp -d "${RUNNER_TEMP:-/tmp}/publication-gh.XXXXXX")
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  https://github.com/cli/cli/releases/download/v2.104.0/gh_2.104.0_linux_amd64.tar.gz \
  --output "${TOOL_DIR}/gh.tar.gz"
printf '%s  %s\n' 98cb101ad87dbbd3f6b2f2bfcbd43197ccd07643140121d8a9cfaa91eadbc24e "${TOOL_DIR}/gh.tar.gz" | sha256sum --check --status
tar -xzf "${TOOL_DIR}/gh.tar.gz" -C "${TOOL_DIR}"
"${TOOL_DIR}/gh_2.104.0_linux_amd64/bin/gh" --version
printf '%s\n' "${TOOL_DIR}/gh_2.104.0_linux_amd64/bin" >> "${GITHUB_PATH}"
