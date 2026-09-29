"""
Enumerations of the values systemd accepts in unit files.

Each member is the literal string systemd uses, so members convert to and from
that string: ``str(member)`` returns it and ``SystemdUnitType("service")`` looks a
member up by it.
"""

from enum import StrEnum


class SystemdUnitType(StrEnum):
    """The kinds of systemd unit, named by their file suffix."""

    SERVICE = "service"
    """
    Manages system services and daemons, including their startup, shutdown, and runtime
    behavior.
    """

    SOCKET = "socket"
    """
    Implements socket-based activation, starting a service when traffic arrives on a
    specific socket.
    """

    TARGET = "target"
    """
    Groups other units together to define synchronization points or system states (e.g.,
    multi-user.target, graphical.target)
    """

    TIMER = "timer"
    """
    Functions as a cron-like job scheduler, activating a service unit on a real-time or
    monotonic timer.
    """

    PATH = "path"
    """
    Monitors files or directories and can activate a service unit when a change or
    access occurs.
    """

    MOUNT = "mount"
    """Manages filesystem mount points."""

    AUTOMOUNT = "automount"
    """
    Provides on-demand mounting of filesystems, similar to traditional automounters.
    """

    SWAP = "swap"
    """Manages swap devices and files."""

    DEVICE = "device"
    """
    Controls access to kernel-recognized devices and can activate other units when a
    specific device becomes available.
    """

    SCOPE = "scope"
    """
    Manages externally created processes that were not started by systemd itself (e.g.,
    user sessions from a login manager).
    """

    SNAPSHOT = "snapshot"
    """
    Saves the current state of the systemd manager and all running units, allowing the
    system to be restored to that state later.
    """

    SLICE = "slice"
    """
    Organizes units into a hierarchical tree for resource management (CPU, memory, etc.)
    using control groups (cgroups).
    """


class SystemdStartupType(StrEnum):
    """Values of ``Type=`` in a ``[Service]`` section."""

    SIMPLE = "simple"
    """
    systemd considers the service started immediately after the main process is forked.
    """

    EXEC = "exec"
    """
    Similar to simple, but systemd waits until the main service binary has successfully
    executed before proceeding.
    """

    FORKING = "forking"
    """
    For traditional UNIX daemons that fork into the background; systemd waits for the
    parent process to exit and the child to become the main process.
    """

    ONESHOT = "oneshot"
    """
    A service that runs a single command and then exits. Often used with
    RemainAfterExit=yes for actions that change system state.
    """

    DBUS = "dbus"
    """
    The service is considered started when it acquires a specific name on the D-Bus
    system bus.
    """

    NOTIFY = "notify"
    """
    Similar to exec, but the service sends a notification message to systemd when it is
    ready.
    """

    NOTIFY_RELOAD = "notify-reload"
    """
    Similar to notify, but the service also notifies systemd when a reload operation is
    complete.
    """

    IDLE = "idle"
    """
    Delays execution of the service binary until all other active jobs are dispatched,
    primarily to improve console output readability.
    """


class SystemdRestartType(StrEnum):
    """Values of ``Restart=`` in a ``[Service]`` section."""

    NO = "no"
    """No automatic restarts."""

    ON_SUCCESS = "on-success"
    """Restarts only if the service exits cleanly (exit code 0 or specific signals)."""

    ON_FAILURE = "on-failure"
    """
    Restarts on non-zero exit code, termination by certain signals, or timeout. This is
    a common setting for general services.
    """

    ON_ABNORMAL = "on-abnormal"
    """Restarts if terminated by a signal or timeout, but not for normal error exit."""

    ON_WATCHDOG = "on-watchdog"
    """Restarts only if the watchdog timeout is triggered."""

    ON_ABORT = "on-abort"
    """Restarts on exit due to an uncaught signal not defined as clean."""

    ALWAYS = "always"
    """Restarts regardless of exit status, signal termination, or timeout."""


class SystemdScope(StrEnum):
    """Which systemd manager owns the project's units."""

    SYSTEM = "system"
    """
    The system manager. Units live in ``/etc/systemd/system``, start at boot, and
    run as the ``User=`` they name (root if they name none). Managing them needs
    root, an escalation prefix such as ``sudo -n``, or polkit rules.
    """

    USER = "user"
    """
    The invoking user's manager (``systemctl --user``). Units live in
    ``$XDG_CONFIG_HOME/systemd/user`` (usually ``~/.config/systemd/user``), run
    as that user, and need no privileges, but the manager only runs while the
    user has a session or lingering is enabled.
    """


class InstallMethod(StrEnum):
    """How ``install`` puts a unit into the unit search path."""

    COPY = "copy"
    """Copy the rendered file into the unit directory. Needs write access there."""

    LINK = "link"
    """
    Keep the rendered file in a directory the deploying user owns and have
    ``systemctl link`` place a symlink in the unit directory. Needs only
    systemd's own authorization (polkit), never file system privileges.
    """
