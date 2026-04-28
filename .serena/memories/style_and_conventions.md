# Style and Conventions

**Profile Awareness (CRITICAL)**:
- Hermes uses "profiles" to support multitenancy.
- **NEVER hardcode `~/.hermes` or `Path.home() / '.hermes'`!**
- **Internal Paths**: Always use `get_hermes_home()` from `hermes_constants`.
- **Display Paths (user facing)**: Always use `display_hermes_home()`.
- Profile tests must mock `Path.home()`.

**Adding Tools**:
1. Create `tools/your_tool.py`.
2. Register it using `from tools.registry import registry; registry.register(...)`. All tool handlers MUST return a JSON string.
3. Import in `model_tools.py` under `_discover_tools()`.
4. Add to `toolsets.py` in the appropriate collection.

**Adding Slash Commands**:
1. Add `CommandDef` to `COMMAND_REGISTRY` in `hermes_cli/commands.py`.
2. Implement handling in `cli.py` (`process_command()` -> `_handle_mycommand()`).
3. (Optional) Add handling in `gateway/run.py` if global.

**Additional Constraints**:
- **Prompt Caching**: Do NOT alter past context mid-conversation to preserve Anthropic Prompt Caching savings.
- UI: Avoid `simple_term_menu` (use `curses` instead). Avoid `\033[K` (use space padding).
- Do not reference other tools inside a tool's description schema unless done dynamically at runtime.