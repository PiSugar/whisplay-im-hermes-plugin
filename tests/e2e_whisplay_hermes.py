#!/usr/bin/env python3
"""End-to-end smoke test for Whisplay IM <-> Hermes Gateway.

Run this on the Raspberry Pi that hosts both whisplay-ai-chatbot and Hermes:

    python3 tests/e2e_whisplay_hermes.py

The test injects a unique message into /whisplay-im/inbox, then watches the
Hermes gateway log until it sees that Hermes consumed the message and attempted
or completed a response delivery through the whisplay_im platform.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def http_json(method: str, url: str, payload: dict | None = None, timeout: float = 10.0) -> tuple[int, str]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def service_active(name: str) -> bool | None:
    try:
        res = subprocess.run(
            ["systemctl", "is-active", name],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return res.stdout.strip() == "active"


def read_since(path: Path, offset: int) -> tuple[str, int]:
    if not path.exists():
        return "", offset
    size = path.stat().st_size
    if size < offset:
        offset = 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        data = f.read()
        return data, f.tell()


def wait_for_log(path: Path, offset: int, message_marker: str, timeout_s: float, require_real_response: bool) -> tuple[bool, str]:
    deadline = time.time() + timeout_s
    collected: list[str] = []
    saw_inbound = False
    saw_response = False
    saw_send = False
    provider_error = ""

    while time.time() < deadline:
        chunk, offset = read_since(path, offset)
        if chunk:
            collected.append(chunk)
            text = "".join(collected)[-12000:]
            if "inbound message: platform=whisplay_im" in text:
                saw_inbound = True
            if "response ready: platform=whisplay_im" in text:
                saw_response = True
            if "Sending response" in text and "whisplay-device" in text:
                saw_send = True
            for line in text.splitlines():
                lower = line.lower()
                if (
                    "provider auth failed" in lower
                    or "no inference provider configured" in lower
                    or "no api key was found" in lower
                    or "set the deepseek_api_key" in lower
                    or "agent error in session" in lower
                ):
                    provider_error = line
            if saw_inbound and (saw_response or saw_send):
                if require_real_response and provider_error:
                    return False, f"Hermes received the message but provider is not configured: {provider_error}"
                return True, "Hermes received a Whisplay IM message and attempted response delivery."
        time.sleep(1.0)

    tail = "".join(collected)[-3000:]
    details = [
        f"saw_inbound={saw_inbound}",
        f"saw_response={saw_response}",
        f"saw_send={saw_send}",
    ]
    if provider_error:
        details.append(f"provider_error={provider_error}")
    if tail:
        details.append("recent_log=" + tail)
    return False, "; ".join(details)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Whisplay IM <-> Hermes Gateway integration")
    parser.add_argument("--base-url", default="http://127.0.0.1:18888", help="Whisplay IM bridge base URL")
    parser.add_argument("--gateway-log", default="/home/pi/.hermes/logs/gateway.log", help="Hermes gateway log path")
    parser.add_argument("--timeout", type=float, default=180.0, help="Seconds to wait for Hermes response")
    parser.add_argument("--message", default="Hermes Whisplay E2E test")
    parser.add_argument("--require-real-response", action="store_true", help="Fail if Hermes only returns a provider/API-key error")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    gateway_log = Path(args.gateway_log)
    test_id = f"hermes-e2e-{int(time.time())}"
    marker = f"{args.message} id={test_id}"

    hermes_active = service_active("hermes-gateway.service")
    chatbot_active = service_active("chatbot.service")
    if hermes_active is False:
        print("FAIL hermes-gateway.service is not active", file=sys.stderr)
        return 2
    if chatbot_active is False:
        print("FAIL chatbot.service is not active", file=sys.stderr)
        return 2

    poll_url = f"{base_url}/whisplay-im/poll?waitSec=1"
    status, body = http_json("GET", poll_url, timeout=5)
    if status >= 500:
        print(f"FAIL bridge poll returned HTTP {status}: {body[:200]}", file=sys.stderr)
        return 2

    offset = gateway_log.stat().st_size if gateway_log.exists() else 0
    inbox_url = f"{base_url}/whisplay-im/inbox"
    payload = {"id": test_id, "message": marker}
    status, body = http_json("POST", inbox_url, payload, timeout=10)
    if status >= 300:
        print(f"FAIL inbox returned HTTP {status}: {body[:500]}", file=sys.stderr)
        return 2

    ok, detail = wait_for_log(gateway_log, offset, marker, args.timeout, args.require_real_response)
    if not ok:
        print(f"FAIL {detail}", file=sys.stderr)
        return 1

    print("PASS Whisplay IM <-> Hermes Gateway e2e smoke test")
    print(detail)
    print(f"test_id={test_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
