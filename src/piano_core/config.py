"""Typed application configuration for Piano Core.

Rules established by PIANO-P001:

- explicit defaults; the dataclass is frozen after construction;
- fail closed: invalid or unknown input raises :class:`ConfigError`;
- no secrets, no network access, no hidden global mutation;
- environment support uses the explicit ``PIANO_CORE_`` prefix.

Only application-level configuration (mode, log level) exists for now.
MIDI/LED/API configuration is added by the tasks that implement those
subsystems.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from piano_core.led.model import LedMode

#: Prefix for all environment variables read by the application.
ENV_PREFIX = "PIANO_CORE_"

#: Environment variables understood by the application.
KNOWN_ENV_VARS = frozenset(
    {
        "PIANO_CORE_MODE",
        "PIANO_CORE_LOG_LEVEL",
        "PIANO_CORE_MIDI_INPUT_ENABLED",
        "PIANO_CORE_MIDI_INPUT_PORT_MATCHER",
        "PIANO_CORE_MIDI_OUTPUT_ENABLED",
        "PIANO_CORE_MIDI_OUTPUT_PORT_MATCHER",
        "PIANO_CORE_LIBRARY_ROOT",
        "PIANO_CORE_LIBRARY_INDEX_PATH",
        "PIANO_CORE_LIBRARY_ENABLED",
        "PIANO_CORE_PACKAGES_ENABLED",
        "PIANO_CORE_PACKAGES_ROOT",
        "PIANO_CORE_PACKAGES_DB_PATH",
        "PIANO_CORE_LEARN_ENABLED",
        "PIANO_CORE_LEARN_DB_PATH",
        "PIANO_CORE_API_ENABLED",
        "PIANO_CORE_API_HOST",
        "PIANO_CORE_API_PORT",
        "PIANO_CORE_UI_ENABLED",
        "PIANO_CORE_LED_ENABLED",
        "PIANO_CORE_LED_WS_HOST",
        "PIANO_CORE_LED_WS_PORT",
        "PIANO_CORE_LED_MODE",
        "PIANO_CORE_LED_BRIGHTNESS",
        "PIANO_CORE_DEVICE_IDENTITY_DIR",
    }
)

#: Configuration keys accepted by :meth:`AppConfig.from_mapping`.
KNOWN_KEYS = frozenset(
    {
        "mode",
        "log_level",
        "midi_input_enabled",
        "midi_input_port_matcher",
        "midi_output_enabled",
        "midi_output_port_matcher",
        "library_root",
        "library_index_path",
        "library_enabled",
        "packages_enabled",
        "packages_root",
        "packages_db_path",
        "learn_enabled",
        "learn_db_path",
        "api_enabled",
        "api_host",
        "api_port",
        "ui_enabled",
        "led_enabled",
        "led_ws_host",
        "led_ws_port",
        "led_mode",
        "led_brightness",
        "device_identity_dir",
    }
)


class ConfigError(ValueError):
    """Raised when configuration input is invalid. Piano Core fails closed."""


class AppMode(Enum):
    """Runtime mode of the appliance process."""

    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TEST = "test"


class LogLevel(Enum):
    """Log levels supported by the application, mapped onto :mod:`logging`."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

    def to_logging_level(self) -> int:
        """Return the numeric :mod:`logging` level for this member."""
        return _LOGGING_LEVELS[self]


_LOGGING_LEVELS = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
}


def _parse_bool(raw: object, field_name: str) -> bool:
    """Parse a strict boolean (``true``/``false``, case-insensitive)."""
    if raw is True:
        return True
    if raw is False:
        return False
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ConfigError(
        f"{field_name}: invalid boolean {raw!r} (allowed: true, false)"
    )


def _parse_matcher(raw: object, field_name: str) -> str:
    """Validate the non-empty case-insensitive port name matcher."""
    if not isinstance(raw, str):
        raise ConfigError(
            f"{field_name}: expected a string, got {type(raw).__name__}"
        )
    matcher = raw.strip()
    if not matcher:
        raise ConfigError(f"{field_name}: must not be empty")
    return matcher


def _parse_optional_path(raw: object, field_name: str) -> str:
    """Parse an optional path string (empty string = unset)."""
    if not isinstance(raw, str):
        raise ConfigError(
            f"{field_name}: expected a string, got {type(raw).__name__}"
        )
    return raw.strip()


def _parse_identity_directory(raw: object, field_name: str) -> str:
    """Parse the device identity directory (empty string = unconfigured).

    Structural validation ONLY (PIANO-PROD-001): a non-empty value must
    be an absolute path without NUL and without ``..`` components.
    Deliberately NOT validated here: existence, permissions, ownership
    (that is the identity module's storage contract at load time), and
    deliberately NOT expanded: ``~`` and environment variables are kept
    literal (a relative or tilde value fails closed instead of silently
    resolving somewhere else).
    """
    value = _parse_optional_path(raw, field_name)
    if not value:
        return ""
    if not value.startswith("/"):
        raise ConfigError(
            f"{field_name}: must be an absolute path (got {value!r})"
        )
    if "\0" in value:
        raise ConfigError(f"{field_name}: must not contain NUL")
    if any(component == ".." for component in value.split("/")):
        raise ConfigError(
            f"{field_name}: must not contain '..' path components"
        )
    return value


def _parse_enum_member(enum_cls: type[Enum], raw: object, field_name: str):
    """Parse ``raw`` into a member of ``enum_cls`` (case-insensitive)."""
    if not isinstance(raw, str):
        raise ConfigError(
            f"{field_name}: expected a string, got {type(raw).__name__}"
        )
    normalized = raw.strip().lower()
    for member in enum_cls:
        if member.value.lower() == normalized:
            return member
    allowed = ", ".join(member.value for member in enum_cls)
    raise ConfigError(
        f"{field_name}: invalid value {raw!r} (allowed: {allowed})"
    )


def _parse_led_mode(raw: object, field_name: str):
    """Parse the bounded LED mode enum (lazy import: no config->led cycle)."""
    from piano_core.led.model import LedMode

    return _parse_enum_member(LedMode, raw, field_name)


def _parse_brightness(raw: object, field_name: str) -> int:
    """Parse the bounded LED brightness (0..100 percent)."""
    if isinstance(raw, bool):
        raise ConfigError(f"{field_name}: invalid brightness {raw!r}")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().lstrip("+").isdigit():
        value = int(raw.strip())
    else:
        raise ConfigError(
            f"{field_name}: invalid brightness {raw!r} (expected 0..100)"
        )
    if not 0 <= value <= 100:
        raise ConfigError(
            f"{field_name}: brightness out of range [0..100]: {value!r}"
        )
    return value


def _parse_port(raw: object, field_name: str) -> int:
    """Parse a strict TCP port (1..65535; ints or digit strings only)."""
    if isinstance(raw, bool):
        raise ConfigError(f"{field_name}: invalid port {raw!r}")
    if isinstance(raw, int):
        port = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        port = int(raw.strip())
    else:
        raise ConfigError(
            f"{field_name}: invalid port {raw!r} (expected 1..65535)"
        )
    if not 1 <= port <= 65535:
        raise ConfigError(
            f"{field_name}: port out of range (1..65535): {port!r}"
        )
    return port


def _parse_host(raw: object, field_name: str) -> str:
    """Parse a bind address (IPv4 literal or ``localhost``; fail closed).

    Deliberately narrow: the local control API must never silently bind a
    name that could resolve beyond the appliance. IPv6 literals are not
    supported in P012 (the HTTP server binds AF_INET).
    """
    if not isinstance(raw, str):
        raise ConfigError(
            f"{field_name}: expected a string, got {type(raw).__name__}"
        )
    host = raw.strip()
    if not host:
        raise ConfigError(f"{field_name}: must not be empty")
    if host.lower() == "localhost":
        return "localhost"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ConfigError(
            f"{field_name}: invalid bind address {raw!r} "
            "(allowed: an IPv4 literal or 'localhost')"
        ) from None
    if address.version != 4:
        raise ConfigError(
            f"{field_name}: only IPv4 bind addresses are supported "
            f"(got {raw!r})"
        )
    return host


@dataclass(frozen=True)
class AppConfig:
    """Immutable application configuration.

    Construct directly with enum members or through :meth:`from_mapping` /
    :meth:`from_env`, which validate strictly and fail closed.
    """

    mode: AppMode = AppMode.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    midi_input_enabled: bool = False
    midi_input_port_matcher: str = "roland"
    # PIANO-P007: output is disabled by default (safe default); the matcher
    # is a stable port-NAME matcher, never a volatile ALSA address.
    midi_output_enabled: bool = False
    midi_output_port_matcher: str = "roland"
    # PIANO-P010: song library configuration; empty = unset (the library
    # service fails closed when an operation requires them). The index path
    # must never point inside the source library.
    library_root: str = ""
    library_index_path: str = ""
    # PIANO-P011: runtime library integration is DISABLED by default (safe
    # default). When enabled, Application constructs the canonical
    # LibraryService and applies the documented degrade contract: any
    # validation failure marks the library unavailable while the service
    # stays healthy. Startup never deep-scans or enriches.
    library_enabled: bool = False
    # M5 (ADR-PACKAGE-PRACTICE-V1): imported practice packages. DISABLED by
    # default (safe default, same auxiliary-degrade contract as the
    # library). packages_root is the managed library root for ACTIVATED
    # package copies (staging lives under <root>/incoming); the separate
    # packages DB persists packages + sessions + progress. Empty root =
    # unset; every operation that needs them fails closed.
    packages_enabled: bool = False
    packages_root: str = ""
    packages_db_path: str = ""
    # PIANO-LEARN-001: Learn Library catalog (separate namespace from
    # the Player library; disabled by default like every auxiliary).
    learn_enabled: bool = False
    learn_db_path: str = ""
    # PIANO-P012: local control API. DISABLED by default and bound to
    # LOOPBACK by default; LAN exposure requires an explicit production
    # configuration decision (no hidden 0.0.0.0 fallback). The API is an
    # unauthenticated trusted-LAN surface (limitation recorded in
    # docs/operations/API.md). PIANO-P015-HF2: the production appliance
    # binds 0.0.0.0 EXPLICITLY (all interfaces) — INADDR_ANY succeeds
    # before any interface address exists at cold boot and follows DHCP
    # address changes without reconfiguration.
    api_enabled: bool = False
    api_host: str = "127.0.0.1"
    api_port: int = 8788
    # PIANO-P013: packaged web UI served by the API server (same origin,
    # allowlisted static assets only). ENABLED by default: the UI adds no
    # listener, dependency, or network surface of its own — it only gates
    # static asset responses on the already-configured API bind. The
    # security model is unchanged (trusted LAN, no auth, not
    # Internet-safe). Disable with ``PIANO_CORE_UI_ENABLED=false`` to
    # restore the bare P012 API-only surface.
    ui_enabled: bool = True
    # PIANO-P014: keyboard LED subsystem (ESP32 WebSocket client target),
    # DISABLED by default (safe default) and bound to LOOPBACK by default;
    # production enables it explicitly (the ESP32 dials the LAN address).
    # The subsystem is visual output only and auxiliary: any failure
    # degrades LED visualization while the service stays healthy.
    # PIANO-P015-HF2: the production appliance binds 0.0.0.0 EXPLICITLY
    # so the endpoint is listening before the network is up at cold boot
    # (the ESP32 reconnects on its own once its Wi-Fi is up).
    led_enabled: bool = False
    led_ws_host: str = "127.0.0.1"
    led_ws_port: int = 8787
    led_mode: LedMode = LedMode.PLAYBACK
    led_brightness: int = 100
    # PIANO-PROD-001: persistent device identity directory. EMPTY by
    # default = the identity feature is unconfigured (no identity I/O
    # anywhere in the application). When set it must be an absolute
    # path (production: /var/lib/piano-core/identity); the application
    # only ever LOADS the identity from there — it never generates,
    # repairs, or deletes it.
    device_identity_dir: str = ""

    @classmethod
    def from_mapping(cls, data: object) -> "AppConfig":
        """Build configuration from a mapping, failing closed on bad input."""
        if not isinstance(data, Mapping):
            raise ConfigError(
                f"configuration must be a mapping, got {type(data).__name__}"
            )
        unknown = sorted(str(key) for key in data if key not in KNOWN_KEYS)
        if unknown:
            raise ConfigError(
                f"unknown configuration key(s): {', '.join(unknown)}"
            )
        kwargs = {}
        if "mode" in data:
            kwargs["mode"] = _parse_enum_member(AppMode, data["mode"], "mode")
        if "log_level" in data:
            kwargs["log_level"] = _parse_enum_member(
                LogLevel, data["log_level"], "log_level"
            )
        if "midi_input_enabled" in data:
            kwargs["midi_input_enabled"] = _parse_bool(
                data["midi_input_enabled"], "midi_input_enabled"
            )
        if "midi_input_port_matcher" in data:
            kwargs["midi_input_port_matcher"] = _parse_matcher(
                data["midi_input_port_matcher"], "midi_input_port_matcher"
            )
        if "midi_output_enabled" in data:
            kwargs["midi_output_enabled"] = _parse_bool(
                data["midi_output_enabled"], "midi_output_enabled"
            )
        if "midi_output_port_matcher" in data:
            kwargs["midi_output_port_matcher"] = _parse_matcher(
                data["midi_output_port_matcher"], "midi_output_port_matcher"
            )
        if "library_root" in data:
            kwargs["library_root"] = _parse_optional_path(
                data["library_root"], "library_root"
            )
        if "library_index_path" in data:
            kwargs["library_index_path"] = _parse_optional_path(
                data["library_index_path"], "library_index_path"
            )
        if "library_enabled" in data:
            kwargs["library_enabled"] = _parse_bool(
                data["library_enabled"], "library_enabled"
            )
        if "packages_enabled" in data:
            kwargs["packages_enabled"] = _parse_bool(
                data["packages_enabled"], "packages_enabled"
            )
        if "packages_root" in data:
            kwargs["packages_root"] = _parse_optional_path(
                data["packages_root"], "packages_root"
            )
        if "packages_db_path" in data:
            kwargs["packages_db_path"] = _parse_optional_path(
                data["packages_db_path"], "packages_db_path"
            )
        if "learn_enabled" in data:
            kwargs["learn_enabled"] = _parse_bool(
                data["learn_enabled"], "learn_enabled"
            )
        if "learn_db_path" in data:
            kwargs["learn_db_path"] = _parse_optional_path(
                data["learn_db_path"], "learn_db_path"
            )
        if "api_enabled" in data:
            kwargs["api_enabled"] = _parse_bool(
                data["api_enabled"], "api_enabled"
            )
        if "api_host" in data:
            kwargs["api_host"] = _parse_host(data["api_host"], "api_host")
        if "api_port" in data:
            kwargs["api_port"] = _parse_port(data["api_port"], "api_port")
        if "ui_enabled" in data:
            kwargs["ui_enabled"] = _parse_bool(
                data["ui_enabled"], "ui_enabled"
            )
        if "led_enabled" in data:
            kwargs["led_enabled"] = _parse_bool(
                data["led_enabled"], "led_enabled"
            )
        if "led_ws_host" in data:
            kwargs["led_ws_host"] = _parse_host(
                data["led_ws_host"], "led_ws_host"
            )
        if "led_ws_port" in data:
            kwargs["led_ws_port"] = _parse_port(
                data["led_ws_port"], "led_ws_port"
            )
        if "led_mode" in data:
            kwargs["led_mode"] = _parse_led_mode(data["led_mode"], "led_mode")
        if "led_brightness" in data:
            kwargs["led_brightness"] = _parse_brightness(
                data["led_brightness"], "led_brightness"
            )
        if "device_identity_dir" in data:
            kwargs["device_identity_dir"] = _parse_identity_directory(
                data["device_identity_dir"], "device_identity_dir"
            )
        return cls(**kwargs)

    @classmethod
    def from_env(cls, env: object = None) -> "AppConfig":
        """Build configuration from environment variables.

        Reads ``PIANO_CORE_MODE`` and ``PIANO_CORE_LOG_LEVEL``. Any other
        ``PIANO_CORE_*`` variable is rejected (fail closed) so that typo'd
        variables cannot silently vanish. Non-prefixed variables are ignored.
        """
        source = os.environ if env is None else env
        if not isinstance(source, Mapping):
            raise ConfigError(
                f"env must be a mapping, got {type(source).__name__}"
            )
        data = {}
        for key, value in source.items():
            if not key.startswith(ENV_PREFIX):
                continue
            suffix = key[len(ENV_PREFIX) :].lower()
            if suffix not in KNOWN_KEYS:
                raise ConfigError(
                    f"unknown environment variable {key!r} "
                    f"(supported: {', '.join(sorted(KNOWN_ENV_VARS))})"
                )
            data[suffix] = value
        return cls.from_mapping(data)

    def logging_level(self) -> int:
        """Return the numeric :mod:`logging` level for this configuration."""
        return self.log_level.to_logging_level()
