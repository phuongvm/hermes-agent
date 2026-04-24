# Design: Enable WhatsApp Gateway

## Architecture

The WhatsApp Gateway relies on a Bridge Pattern:
1. **Core Agent**: Python `gateway/platforms/whatsapp.py`
2. **Bridge Process**: Node.js `scripts/whatsapp-bridge/bridge.js` using `@whiskeysockets/baileys`.

Hermes spawns the Node process, which manages the WebSocket connection to WhatsApp. The Python adapter polls `/messages` and posts to `/send`.

## Configuration Strategy

### 1. Enablement (`config.yaml`)
```yaml
whatsapp:
  enabled: true
```

### 2. Security (`.env`)
By default, the bridge operates in `self-chat` mode. If `WHATSAPP_MODE=bot` is used, the agent will reply to anyone. Therefore, the `.env` file MUST define allowed users if the bot is publicly reachable:

```env
WHATSAPP_MODE=self-chat
WHATSAPP_ALLOWED_USERS=84912345678,84987654321
```

### 3. Execution
Starting `hermes gateway` initializes the bridge. On first run, it prints a QR code in the terminal. The user links their device via the WhatsApp app, and the Baileys session is stored in `~/.hermes/whatsapp/session`.

## Artifact Traceability
This design relies directly on findings from `explorations/whatsapp_gateway_exploration.md`.
