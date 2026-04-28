# Hermes Agent Overview

**Purpose**: Python-based AI agent framework equipped with a central chat interface (`cli.py`, `run_agent.py`), diverse plugin tool ecosystem (`tools/`), slash command registry, multiple execution environments, and robust gateway platform implementations (Discord, Telegram, Slack).

**Structure**:
- `run_agent.py`: `AIAgent` core chat/tool loop.
- `cli.py` & `hermes_cli/`: CLI entrypoints, slash command handling (`commands.py`), theme configuration (`skin_engine.py`).
- `tools/`: Registry (`registry.py`) and implementation of tools (e.g., `web_tools.py`, `file_tools.py`, `mcp_tool.py`).
- `gateway/`: Messaging platform adapters.
- `agent/`: Internal prompt builders, state compression.
- `tests/`: 3000+ pytest tests.
- `batch_runner.py`: Parallel batch processing.

**Tech Stack**: Python (`venv`), SQLite (SessionDB for `hermes_state.py`), Pytest, Anthropic API (with prompt caching).

**Operating System Environment**: Windows/Linux compatible. Default activation via `source venv/bin/activate`.