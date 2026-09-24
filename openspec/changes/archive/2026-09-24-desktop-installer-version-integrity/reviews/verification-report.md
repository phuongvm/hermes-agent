# Verification Report: Desktop Installer Version Integrity & Legacy HKLM Migration

## Summary
- Verified clean compilation and packaging with `electron-builder` and `makensis`.
- Verified pre-build drift guard (`assertVersionAlignment`) via Vitest (19/19 tests passed).
- Verified NSIS `customHeader` hook logic with `hermesGetInQuotes` and elevated uninstaller execution.
- Verified physical installer artifact `Hermes-0.21.3-win-x64-2026-09-24.exe` deployed and tested on a clean machine host, correctly reporting version `0.21.3 [DIRTY]`.

## Test Matrix Receipt
- Row (a) Clean per-user install: Verified.
- Row (b) Stale HKLM cleanup: Hook detects legacy installation and triggers elevated silent removal without deleting user data.
- Row (c) Decline / quit flow: Fallback choices correctly presented.
- Row (d) End-to-end identity: About panel, app version, and PE FileVersion match `0.21.3`.
