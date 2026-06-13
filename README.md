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
# WHISPLAY_IM_TOKEN=optional-shared-token
```

Restart the gateway:

```bash
sudo systemctl restart hermes-gateway.service
```

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

## Automated E2E Test

Run this on the Raspberry Pi after Hermes Gateway and whisplay-ai-chatbot are running:

```bash
python3 tests/e2e_whisplay_hermes.py
```

The test posts a unique message to `/whisplay-im/inbox`, watches `~/.hermes/logs/gateway.log`, and passes when Hermes receives the message and attempts to deliver a response through the `whisplay_im` platform.

If you want the test to fail when the model provider/API key is not configured, use:

```bash
python3 tests/e2e_whisplay_hermes.py --require-real-response
```

