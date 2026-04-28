# Task Completion Checklist

1. Review the `style_and_conventions.md` to ensure `get_hermes_home()` and `display_hermes_home()` were used properly instead of hardcoded paths for `.hermes`.
2. Check that prompt caching constraints weren't violated (i.e., avoiding mid-conversation context or toolset resets).
3. If submitting a new tool, ensure the handler returns a JSON string, it's imported in `model_tools.py`, and added to `toolsets.py`.
4. Always execute the full test suite (`python -m pytest tests/ -q`) to guarantee no state or logic regressions.