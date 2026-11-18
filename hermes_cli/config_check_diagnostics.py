"""Read-only diagnostics for saved toolsets, bundled platform selections and voice values."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def config_check_diagnostics(config: dict[str, Any], get_env_value: Callable[[str], str | None]) -> list[str]:
    """Report saved selections that would otherwise be lost in startup output.

    Toolset names are judged by the resolver ``hermes config migrate`` also uses, disabled platforms
    by the runtime's own ``plugins.disabled`` reader and the manifest-name gate of
    ``plugins_discovery.gate_manifest``. A configured credential is only a reason to *mention* a
    disabled platform; disabling it may have been intentional.
    """
    from hermes_cli.config import _platform_manifest_env_entries, _platform_plugin_manifests
    from hermes_cli.plugins_discovery import _get_disabled_plugins
    from hermes_cli.toolset_validation import saved_toolset_resolver, validate_platform_toolsets

    from hermes_cli.config_value_checks import _validate_voice

    diagnostics = validate_platform_toolsets(config.get("platform_toolsets"), saved_toolset_resolver(config))
    voice_issues: list = []
    _validate_voice(config, voice_issues)
    diagnostics += [f"{issue.message}. {issue.hint}." for issue in voice_issues]

    diagnostics += _effective_catalog_diagnostics()

    disabled = _get_disabled_plugins()
    for name, manifest in _platform_plugin_manifests(source="bundled"):
        key = f"platforms/{name}"
        if not {key, str(manifest.get("name"))} & disabled:
            continue
        required = [env for env, _secret, _meta in _platform_manifest_env_entries(manifest, optional=False)]
        if required and all(get_env_value(env) for env in required):
            diagnostics.append(
                f"platform plugin '{key}' is disabled while its required credentials are configured. "
                f"Run `hermes plugins enable {key}` if you want it active."
            )
    return diagnostics


def _effective_catalog_diagnostics() -> list[str]:
    from agent.prerequisite_catalog import catalog_diagnostics
    from hermes_cli.config_backend import ConfigBackendUnavailable
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_yaml import YAMLError

    try:
        effective = load_user_config_effective(fail_closed=True, strict_managed=True)
    except (ConfigBackendUnavailable, OSError, TypeError, ValueError, YAMLError):
        return ["prerequisite_catalog: profile-config-unavailable"]
    return [f"prerequisite_catalog: {reason}"
            for reason in catalog_diagnostics(effective.get("prerequisite_catalog", {}))]
