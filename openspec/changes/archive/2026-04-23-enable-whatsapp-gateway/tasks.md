# Tasks: Enable WhatsApp Gateway

## 1. Verify Bridge Dependencies
- [x] Verify Node.js is installed in WSL.
- [x] Run `npm install` inside `scripts/whatsapp-bridge` to install `@whiskeysockets/baileys` and its dependencies.

## 2. Apply Configuration (`config.yaml`)
- [x] Add/ensure `enabled: true` under the `whatsapp:` block in `~/.hermes/config.yaml`.

## 3. Apply Security Setup (`.env`)
- [x] Define `WHATSAPP_MODE=self-chat` (or `bot`) in `~/.hermes/.env`.
- [x] Define `WHATSAPP_ALLOWED_USERS` with the appropriate phone numbers in `~/.hermes/.env` to secure the bridge.

## 4. Initialization & Verification
- [x] Start `hermes gateway`.
- [x] Scan the generated QR code using the WhatsApp app (Linked Devices).
- [x] Send a test message to verify the Python agent receives and responds to the WhatsApp message.
