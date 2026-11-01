# whisplay-im-hermes-plugin

Hermes Agent platform plugin for the Whisplay IM bridge exposed by `whisplay-ai-chatbot`.

The plugin connects Hermes Gateway to a Whisplay device through the local bridge endpoints:

- `GET /whisplay-im/poll` receives device messages
- `POST /whisplay-im/send` sends Hermes replies back to the device
- `POST /whisplay-im/status` shows live Hermes status on the device

## Install on Raspberry Pi

Copy this directory into the Hermes user plugin directory:

```bash
mkdir -p ~/.hermes/plugins/whisplay-im
rsync -av ./ ~/.hermes/plugins/whisplay-im/
hermes plugins enable whisplay-im
```

Configure Hermes secrets in `~/.hermes/.env`:

```bash
WHISPLAY_IM_BASE_URL=http://127.0.0.1:18888
WHISPLAY_IM_CHAT_ID=whisplay-device
WHISPLAY_IM_ALLOW_ALL_USERS=true
# WHISPLAY_IM_RECONNECT_SEC=10
# WHISPLAY_IM_TOKEN=optional-shared-token
```

Restart the gateway:

```bash
sudo systemctl restart hermes-gateway.service
```

The plugin does not require the Whisplay IM bridge to be online before Hermes
starts. If `WHISPLAY_IM_BASE_URL` is unavailable, it keeps retrying every
`WHISPLAY_IM_RECONNECT_SEC` seconds until the bridge comes up.

## Hermes Model Example

For DeepSeek:

```bash
hermes config set model.provider deepseek
hermes config set model.default deepseek-chat
echo "DEEPSEEK_API_KEY=..." >> ~/.hermes/.env
sudo systemctl restart hermes-gateway.service
```

## Files

- `plugin.yaml` declares the Hermes platform plugin.
- `adapter.py` implements polling, sending, and status updates for Whisplay IM.
- `__init__.py` exposes the Hermes `register(ctx)` entry point.

## Adapter regression tests (no device or gateway required)

The unit suite uses the selected Hermes source tree's real adapter base and
secret-scope implementation, with HTTPX MockTransport simulating bridge I/O.
It covers failure/recovery, cancellation during polling and backoff, concurrent
start/stop, scoped configuration, and the simulated legacy import fallback.
The tests require Hermes v0.21.6 or later; a skipped test is not compatibility
evidence. No model calls or real credentials are needed.

Run in a disposable environment with Hermes' import dependencies and `httpx`
available. Set `HERMES_AGENT_DIR` to the source snapshot being tested and
`HERMES_HOME` to an empty disposable directory: the real base adapter writes
runtime status there. Do not point the test suite at a live gateway's home.

```bash
HERMES_AGENT_DIR=/path/to/hermes-source \
HERMES_HOME=/path/to/disposable-test-home \
python -m unittest discover -s tests -v
```

This is adapter-level compatibility coverage, not a full gateway/device E2E
test. The pre-existing handling of non-success HTTP poll responses is separate
from the network-failure/reconnect lifecycle covered by this change.

## Automated E2E Test

Run this on the Raspberry Pi after Hermes Gateway and whisplay-ai-chatbot are running:

```bash
python3 tests/e2e_whisplay_hermes.py
```

The test posts a unique message to `/whisplay-im/inbox`, watches `~/.hermes/logs/gateway.log`, and passes when Hermes receives the message and attempts to deliver a response through the `whisplay_im` platform. It only requires `hermes-gateway.service` and the Whisplay IM bridge endpoint to be available; `chatbot.service` does not need to exist or be active when the bridge is launched by `whisplay-daemon`.

If you want the test to fail when the model provider/API key is not configured, use:

```bash
python3 tests/e2e_whisplay_hermes.py --require-real-response
```
