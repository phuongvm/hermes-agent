# Specification: Venv Update Health and Entry Point Repair

## Purpose
Establishes the self-healing and validation requirements for Python virtual environments during `hermes update` runs, ensuring that corrupted packaging residue and incomplete entry point extra dependencies are corrected automatically.

## Requirements

### Requirement: Corrupted Dist-Info Sanitization
During Python dependency synchronization following an update, the updater SHALL inspect the target virtualenv's site-packages and remove any `*.dist-info` directories that lack a valid `METADATA` file.

#### Scenario: Corrupted dist-info is removed and triggers reinstall
- **GIVEN** a virtual environment containing a `*.dist-info` directory with no `METADATA` file
- **WHEN** dependency synchronization runs
- **THEN** the updater SHALL remove the corrupted directory
- **AND** it SHALL force dependency installation even if git commit hashes appear unchanged

### Requirement: ACP Entry Point Dependency Self-Repair
When the `hermes-acp` console script entry point exists in the scripts directory, the updater SHALL verify that module `acp` is importable in the target virtual environment.

#### Scenario: Missing acp module repaired
- **GIVEN** `hermes-acp` executable exists in `scripts_dir`
- **WHEN** `import acp` fails in the target virtual environment
- **THEN** the updater SHALL execute a repair installation with `.[acp]`
