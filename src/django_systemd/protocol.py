"""
A thin, mockable seam over ``systemctl`` and the unit directory.

The seam works in either :class:`~django_systemd.defines.SystemdScope`. In the
system scope units live in ``/etc/systemd/system`` and changing them needs
privileges; in the user scope they live in the user's unit directory and need
none. Privileges are never detected: they come from running as root, from an
explicitly configured escalation prefix, or from polkit rules. Read-only queries
are never escalated.

In the user scope, talking to the manager from a non-login session (for example
over SSH as a deploy user) requires lingering to be enabled for that user with
``loginctl enable-linger``, or ``XDG_RUNTIME_DIR`` to be set. Failures show up as
``Failed to connect to bus`` in the raised CalledProcessError.

The escalation seam (:attr:`SubprocessSystemdCtl.escalates`) uses ``os.geteuid``
and is therefore POSIX-only.
"""

from __future__ import annotations

import os
import shutil

# Running systemctl is this module's purpose; see _systemctl for how it is called.
import subprocess  # nosec B404
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .defines import SystemdScope


def user_unit_dir() -> Path:
    """
    The directory systemd searches for user units that we install into.

    This is ``$XDG_CONFIG_HOME/systemd/user`` when ``XDG_CONFIG_HOME`` is set,
    otherwise ``~/.config/systemd/user``.
    """
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "systemd" / "user"


def system_unit_dir() -> Path:
    """The directory for locally administered system units."""
    return Path("/etc/systemd/system")


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The outcome of one systemctl invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@runtime_checkable
class SystemdCtl(Protocol):
    """
    What the :class:`systemd command <django_systemd.management.commands.systemd.Command>`
    needs from systemd. Implement this to swap in a fake for tests or a different
    transport.
    """

    unit_dir: Path
    scope: SystemdScope

    @property
    def available(self) -> bool:
        """
        True if a systemctl binary is on PATH. This does not check that the
        manager is reachable; callers must check it before calling any other
        systemctl-backed method, which raise FileNotFoundError when systemctl is
        absent. ``is_installed``, ``install_unit`` and ``uninstall_unit`` are
        filesystem-only and do not require this check.
        """
        ...

    def daemon_reload(self) -> None: ...
    def restart(self, *units: str) -> None: ...
    def reload(self, *units: str) -> None: ...
    def stop(self, unit: str) -> None: ...
    def can_reload(self, unit: str) -> bool:
        """True if the unit defines a reload action (``ExecReload=``)."""
        ...

    def enable(self, unit: str) -> None: ...
    def disable(self, unit: str) -> None: ...
    def is_active(self, unit: str) -> bool: ...
    def is_enabled(self, unit: str) -> bool: ...

    def is_installed(self, name: str) -> bool:
        """True if a unit file with this name exists in :attr:`unit_dir`."""
        ...

    def install_unit(
        self, source: Path, *, name: str | None = None, mode: int = 0o644
    ) -> Path:
        """
        Copy ``source`` into :attr:`unit_dir`, replacing any existing file.

        :param source: The rendered unit file to install.
        :param name: Install under this file name instead of ``source.name``.
        :param mode: File mode to apply to the installed unit.
        :return: The path of the installed unit file.
        :raises subprocess.CalledProcessError: if escalated and the escalated
            command fails.
        :raises OSError: if not escalated and the file operation fails.
        """
        ...

    def uninstall_unit(self, name: str) -> bool:
        """
        Remove the unit file with this name from :attr:`unit_dir`.

        :return: True if a file was removed, False if there was nothing to remove.
        :raises subprocess.CalledProcessError: if escalated and the escalated
            command fails.
        :raises OSError: if not escalated and the file operation fails.
        """
        ...


class SubprocessSystemdCtl:
    """
    :class:`SystemdCtl` implemented by shelling out to ``systemctl``.

    Works in either :class:`~django_systemd.defines.SystemdScope`.

    :param scope: The scope to manage units in. Defaults to
        :attr:`~django_systemd.defines.SystemdScope.SYSTEM`.
    :param unit_dir: Where to install unit files. Defaults to
        :func:`system_unit_dir` in the system scope and :func:`user_unit_dir` in
        the user scope.
    :param escalate: A privilege escalation prefix, e.g. ``("sudo", "-n")``. Only
        applied to privileged calls in the system scope, and never when already
        root. Read-only queries are never escalated.
    """

    # systemctl is-enabled prints one of many states; these all mean "will start".
    _ENABLED_STATES = frozenset(
        {"enabled", "enabled-runtime", "static", "indirect", "alias"}
    )
    # is-active states that mean the unit is up or coming up.
    _ACTIVE_STATES = frozenset({"active", "activating", "reloading"})

    def __init__(
        self,
        scope: SystemdScope = SystemdScope.SYSTEM,
        *,
        unit_dir: Path | None = None,
        escalate: Sequence[str] = (),
    ) -> None:
        self.scope = scope
        self.escalate = tuple(escalate)
        self.unit_dir = unit_dir or (
            system_unit_dir() if scope is SystemdScope.SYSTEM else user_unit_dir()
        )

    @property
    def available(self) -> bool:
        return shutil.which("systemctl") is not None

    @property
    def escalates(self) -> bool:
        """
        True if privileged calls are prefixed: a prefix is configured, the scope
        is the system one, and the effective user is not root.
        """
        return (
            bool(self.escalate)
            and self.scope is SystemdScope.SYSTEM
            and os.geteuid() != 0
        )

    def _run(self, cmd: list[str], *, check: bool = True) -> CommandResult:
        """Run ``cmd`` (an argument list, never a shell) and wrap the result."""
        # An argument list with no shell. The optional leading prefix is operator
        # configuration from settings (SYSTEMD_ESCALATE), never user input; the
        # executable that follows it is always one of "systemctl", "install" or
        # "rm", and unit names are confined to bare file names after "--".
        result = subprocess.run(  # nosec B603
            cmd, capture_output=True, text=True, check=False
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return CommandResult(
            argv=tuple(cmd),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _privileged(self, cmd: list[str], *, check: bool = True) -> CommandResult:
        """Run ``cmd`` under the escalation prefix when :attr:`escalates`."""
        return self._run([*self.escalate, *cmd] if self.escalates else cmd, check=check)

    def _systemctl_argv(self, *args: str, units: Sequence[str] = ()) -> list[str]:
        # --no-ask-password: fail rather than prompt when authorization is missing.
        # Unit names follow "--" so systemctl never parses one as an option.
        return [
            "systemctl",
            *(["--user"] if self.scope is SystemdScope.USER else []),
            "--no-ask-password",
            *args,
            *(("--", *units) if units else ()),
        ]

    def _systemctl(
        self, *args: str, units: Sequence[str] = (), check: bool = True
    ) -> CommandResult:
        """A privileged systemctl call: changes manager state, so may be escalated."""
        return self._privileged(self._systemctl_argv(*args, units=units), check=check)

    def _query(
        self, *args: str, units: Sequence[str] = (), check: bool = False
    ) -> CommandResult:
        """A read-only systemctl call. Never escalated."""
        return self._run(self._systemctl_argv(*args, units=units), check=check)

    def daemon_reload(self) -> None:
        self._systemctl("daemon-reload")

    def restart(self, *units: str) -> None:
        self._systemctl("restart", units=units)

    def reload(self, *units: str) -> None:
        self._systemctl("reload", units=units)

    def stop(self, unit: str) -> None:
        self._systemctl("stop", units=[unit])

    def can_reload(self, unit: str) -> bool:
        result = self._query(
            "show", "--property=CanReload", "--value", units=[unit], check=False
        )
        return result.stdout.strip() == "yes"

    def enable(self, unit: str) -> None:
        self._systemctl("enable", units=[unit])

    def disable(self, unit: str) -> None:
        self._systemctl("disable", units=[unit])

    def is_active(self, unit: str) -> bool:
        result = self._query("is-active", units=[unit], check=False)
        if result.returncode != 0 and not result.stdout:
            raise subprocess.CalledProcessError(
                result.returncode, result.argv, result.stdout, result.stderr
            )
        return result.stdout.strip() in self._ACTIVE_STATES

    def is_enabled(self, unit: str) -> bool:
        result = self._query("is-enabled", units=[unit], check=False)
        if result.returncode != 0 and not result.stdout:
            raise subprocess.CalledProcessError(
                result.returncode, result.argv, result.stdout, result.stderr
            )
        return result.stdout.strip() in self._ENABLED_STATES

    def _unit_path(self, name: str) -> Path:
        if not name or Path(name).name != name:
            raise ValueError(f"Not a bare unit file name: {name!r}")
        return self.unit_dir / name

    def is_installed(self, name: str) -> bool:
        return self._unit_path(name).is_file()

    def install_unit(
        self, source: Path, *, name: str | None = None, mode: int = 0o644
    ) -> Path:
        destination = self._unit_path(name if name is not None else source.name)
        if self.escalates:
            # install(1) creates or replaces the file with the mode in one step and
            # is easy to allow in a sudoers rule. Unlike the Python path below,
            # this replace is not atomic; only a concurrent daemon-reload could
            # observe the file mid-write.
            self._privileged(
                ["install", "-m", f"{mode:o}", "--", str(source), str(destination)]
            )
            return destination
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        staged = destination.with_name(destination.name + ".tmp")
        try:
            shutil.copyfile(source, staged)
            staged.chmod(mode)
            os.replace(staged, destination)
        except OSError:
            staged.unlink(missing_ok=True)
            raise
        return destination

    def uninstall_unit(self, name: str) -> bool:
        destination = self._unit_path(name)
        # The existence check only needs read/search access to the unit directory;
        # rm -f tolerates a file that vanished in between, so the race is benign.
        if not (destination.is_file() or destination.is_symlink()):
            return False
        if self.escalates:
            self._privileged(["rm", "-f", "--", str(destination)])
        else:
            destination.unlink()
        return True
