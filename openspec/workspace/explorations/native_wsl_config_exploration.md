# Exploration: Native WSL Deployment & Configuration

**Date:** 2026-04-22
**Topic:** Configuring Hermes for Native WSL, WhatsApp Bridge, Budget Governance, and Agent Rosters.
**Context:** The goal is to move away from Docker to save RAM, configure the WhatsApp bridge, and establish governance parameters for the team.

## 1. Architecture Shift: Docker to Native WSL

Currently, `~/.hermes/config.yaml` has `terminal.backend: local`. This successfully points the execution environment directly to WSL instead of a container.

```
┌───────────────────────┐      ┌───────────────────────┐
│ PREVIOUS (Docker)     │      │ CURRENT (WSL Native)  │
│                       │      │                       │
│  Hermes Core          │      │  Hermes Core          │
│      │                │      │      │                │
│  [Docker Socket]      │      │  [Subprocess]         │
│      │                │      │      │                │
│  Container (Heavy)    │      │  WSL Environment      │
└───────────────────────┘      └───────────────────────┘
```
**Findings:**
- The shift to `local` backend is correct for RAM optimization.
- *Cleanup Opportunity:* `config.yaml` still contains dormant `docker_image`, `docker_volumes`, etc., which might cause confusion later but are harmless right now.
- *Dependency Risk:* Tools that previously relied on the `python-nodejs` Docker image now require Node.js and other binaries to be installed directly in your WSL environment.

## 2. WhatsApp Bridge Configuration

We inspected `gateway/platforms/whatsapp.py`. 

**Findings:**
- The WhatsApp bridge uses a Node.js Subprocess (`bridge.js`).
- It auto-installs dependencies via `npm install` inside `scripts/whatsapp-bridge/`.
- In `config.yaml`, the `whatsapp` block is currently empty (`{}`). It should explicitly declare `enabled: true`.
- **Requirement:** WSL MUST have `node` and `npm` installed natively for the bridge to boot successfully.

## 3. Budget Governance Configuration

**Findings:**
- Budget constraints (to prevent context window overflow) are managed in `tools/budget_config.py`.
- The current limits are hardcoded:
  - `DEFAULT_RESULT_SIZE_CHARS = 100_000`
  - `DEFAULT_TURN_BUDGET_CHARS = 200_000`
- To configure this dynamically without RL environments, we may need to inject override logic into `cli.py` or `config.yaml`, OR simply rely on the default hardcoded caps which are currently acting as the system's strict budget governance.

## 4. Agent Rosters

**Findings:**
- Agent roles (Leader, Coder, DevOps, QA, etc.) are activated via `/start-agent-*` workflows.
- Roster management isn't a hardcoded config file; it is an organizational protocol managed via the `agent_share.md` coordination file.
- The rosters will be finalized once we list the active OpenSpec tasks for the deployment.

---

## Actionable Paths Forward

If we decide to formalize this into an OpenSpec change, the proposal should cover:
1. **Config Updates:** Adding `enabled: true` to the WhatsApp configuration in `config.yaml` and cleaning up dormant Docker configurations.
2. **Environment Checks:** Adding a script or validation step to ensure Node.js is installed natively in WSL for the WhatsApp bridge.
3. **Governance Adjustments:** Deciding if the 200,000 character hard limit is sufficient, or if it needs to be parameterized in `config.yaml`.

*Exploration concluded. Hard Exit Gate ready.*
