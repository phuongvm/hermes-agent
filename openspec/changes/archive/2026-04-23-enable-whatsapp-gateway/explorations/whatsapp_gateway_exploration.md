# WhatsApp Gateway Exploration

## Architecture Overview

The Hermes WhatsApp integration uses a **Bridge Pattern**. Since Meta does not provide a free/open API for personal accounts, Hermes bypasses this limitation by simulating a WhatsApp Web session.

```ascii
┌──────────────┐         HTTP/IPC          ┌───────────────────────┐
│ Hermes Agent │ ◀───────────────────────▶ │ Node.js Bridge        │
│ (Python)     │    GET /messages (poll)   │ (scripts/bridge.js)   │
│              │    POST /send             │                       │
└──────────────┘                           └───────────────────────┘
                                                       │
                                                       ▼ WebSockets (Baileys)
                                           ┌───────────────────────┐
                                           │ WhatsApp Servers      │
                                           └───────────────────────┘
```

The Node.js bridge uses `@whiskeysockets/baileys` to maintain a persistent connection to WhatsApp, behaving exactly like a Linked Device (WhatsApp Web).

## Configuration Hierarchy

WhatsApp settings can be configured via `config.yaml` or `.env` variables. `config.yaml` takes precedence.

### 1. `config.yaml` Settings

Under the `whatsapp:` block:

```yaml
whatsapp:
  enabled: true
  require_mention: true             # Only respond when explicitly mentioned
  free_response_chats: '123,456'    # Chats where the agent responds to everything
  mention_patterns: ['bot', 'ai']   # Regex patterns to trigger the bot
  reply_prefix: '⚕ *Hermes Agent*\n' # Prefix added to outgoing messages
```

### 2. `.env` Environment Variables

These govern the bridge process and security:

- `WHATSAPP_MODE`: Can be `self-chat` (you talking to yourself) or `bot` (a dedicated bot number).
- `WHATSAPP_ALLOWED_USERS`: Critical for security. A comma-separated list of phone numbers (e.g., `84912345678`) that are allowed to trigger the agent. If empty, the agent processes messages from *everyone*.
- `WHATSAPP_DEBUG`: Set to `true` for verbose logging.
- `WHATSAPP_REQUIRE_MENTION`: Fallback for `require_mention`.
- `WHATSAPP_FREE_RESPONSE_CHATS`: Fallback for `free_response_chats`.

## Security & Interaction Modes

The bridge enforces strict security out-of-the-box depending on the mode:

**Self-Chat Mode (`WHATSAPP_MODE=self-chat` - Default)**
- The agent only reads and responds to messages sent in the "Message Yourself" chat.
- It will prepend the `reply_prefix` to distinguish its messages from your own typed messages.
- If you want it to respond to others, you must whitelist them using `WHATSAPP_ALLOWED_USERS`.

**Bot Mode (`WHATSAPP_MODE=bot`)**
- Useful if you have a dedicated SIM card/number just for the agent.
- Does not prepend a prefix (since the sender identity is obvious).
- `WHATSAPP_ALLOWED_USERS` becomes extremely important here to prevent strangers from racking up your API usage.

## The "Correct Way" to Enable

1. **Enable in Config**: `whatsapp: enabled: true` (Done).
2. **Set Security (Important)**: Define `WHATSAPP_ALLOWED_USERS` in `.env` to prevent unauthorized access.
3. **Set Mode**: Decide if this is your personal number (`self-chat`) or a dedicated bot number (`bot`).
4. **Install Dependencies**: `npm install` in `scripts/whatsapp-bridge` (Done).
5. **Pairing**: Run `hermes gateway` to scan the QR code. The session is saved to `~/.hermes/whatsapp/session`.

## Next Steps / Open Questions

- Do you plan to use your primary personal number, or do you have a secondary number for the agent?
- Who should be allowed to talk to the agent? Just you, or specific groups/contacts?
