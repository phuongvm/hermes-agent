# Proposal: Enable WhatsApp Gateway

## Overview
This change documents and formalizes the process of securely enabling the WhatsApp gateway for Hermes Agent. Since there is no official open API for personal WhatsApp accounts, Hermes utilizes a Node.js bridge (`Baileys`) to connect via WebSockets (simulating a linked device). 

## Motivation
Users need a clear, safe, and correct way to enable WhatsApp integration. Improper configuration (especially in Bot mode) without `WHATSAPP_ALLOWED_USERS` can lead to security risks and API cost exhaustion if strangers interact with the bot.

## Proposed Changes
1. Formalize the bridge dependency setup (`npm install` in `scripts/whatsapp-bridge`).
2. Formalize the configuration in `~/.hermes/config.yaml` (`whatsapp: enabled: true`).
3. Detail the `.env` security variables (`WHATSAPP_MODE` and `WHATSAPP_ALLOWED_USERS`).
4. Provide pairing instructions for the gateway.

## Scope
This change only covers configuration, setup, and documentation of the existing WhatsApp bridge. No new feature code is being written; we are solidifying the deployment process for this capability.
