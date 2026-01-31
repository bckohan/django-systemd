from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Mapping, Protocol, Sequence, runtime_checkable

from .defines import SystemdScope

UnitState = Literal[
    "active", "inactive", "failed", "activating", "deactivating", "unknown"
]


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class UnitStatus:
    unit: str
    scope: SystemdScope
    is_active: bool
    state: UnitState
    # Raw `systemctl status` output is handy for logs/UI/error messages
    raw: str


@dataclass(frozen=True, slots=True)
class InstalledUnit:
    """
    Represents the effective destination on disk and whether we had to
    perform a daemon-reload or enable to activate the install.
    """

    unit_name: str
    scope: SystemdScope
    destination: Path
    daemon_reloaded: bool
    enabled: bool


@runtime_checkable
class SystemdCtl(Protocol):
    """
    A structural interface (Protocol) for a systemctl + unit installer wrapper.
    """

    # --- core systemctl lifecycle
    def daemon_reload(self, *, scope: SystemdScope = "system") -> None: ...
    def start(self, unit: str, *, scope: SystemdScope = "system") -> None: ...
    def stop(self, unit: str, *, scope: SystemdScope = "system") -> None: ...
    def restart(self, unit: str, *, scope: SystemdScope = "system") -> None: ...
    def reload(self, unit: str, *, scope: SystemdScope = "system") -> None: ...
    def enable(self, unit: str, *, scope: SystemdScope = "system") -> None: ...
    def disable(self, unit: str, *, scope: SystemdScope = "system") -> None: ...
    def mask(self, unit: str, *, scope: SystemdScope = "system") -> None: ...
    def unmask(self, unit: str, *, scope: SystemdScope = "system") -> None: ...

    # --- querying
    def is_active(self, unit: str, *, scope: SystemdScope = "system") -> bool: ...
    def is_enabled(self, unit: str, *, scope: SystemdScope = "system") -> bool: ...
    def status(self, unit: str, *, scope: SystemdScope = "system") -> UnitStatus: ...

    def list_units(
        self,
        *,
        scope: SystemdScope = "system",
        states: Iterable[UnitState] = ("active", "inactive", "failed"),
    ) -> Sequence[str]:
        """
        Return unit names. Typically maps to:
          systemctl list-units --no-legend --plain --state=...
        """

    def list_unit_files(self, *, scope: SystemdScope = "system") -> Mapping[str, str]:
        """
        Return mapping unit_name -> enabled_state.
        Typically maps to:
          systemctl list-unit-files --no-legend --plain
        """

    # --- install units
    def install_unit(
        self,
        unit_source: Path,
        *,
        scope: SystemdScope = SystemdScope.SYSTEM,
        name: str | None = None,
        enable: bool = False,
        daemon_reload: bool = True,
        mode: int = 0o644,
    ) -> InstalledUnit:
        """
        "Install" a unit file onto disk in an appropriate unit search path.

        - scope="system": typically /etc/systemd/system
        - scope="user": typically ~/.config/systemd/user

        Implementations should:
          - create destination dirs
          - copy bytes from unit_source to destination (atomic replace preferred)
          - chmod to `mode`
          - optionally run daemon-reload
          - optionally enable the unit (creates symlinks under wants/)

        Note: This is intentionally NOT `systemctl link` vs `install` vs "drop-ins";
              it's a pragmatic "copy into unit dir" operation.
        """

    def uninstall_unit(
        self,
        unit_name: str,
        *,
        scope: SystemdScope = SystemdScope.SYSTEM,
        disable: bool = True,
        daemon_reload: bool = True,
    ) -> None:
        """
        Remove installed unit file (and optionally disable it), then reload.
        """
