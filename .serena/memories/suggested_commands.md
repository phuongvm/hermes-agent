# Suggested Commands

**Environment Setup**:
Activate the virtual environment BEFORE running any hermes python scripts or tests.
```bash
source venv/bin/activate
```

**Testing**:
Run the full test suite:
```bash
python -m pytest tests/ -q
```
Run specific test categories:
```bash
python -m pytest tests/test_model_tools.py -q
python -m pytest tests/test_cli_init.py -q
python -m pytest tests/gateway/ -q
python -m pytest tests/tools/ -q
```