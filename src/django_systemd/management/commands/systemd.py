"""
The systemd command is a Django_ :doc:`management command <django:ref/django-admin>`
that renders, installs, and restarts the systemd units bundled by your project's
apps. Every subcommand works from the same manifest, the unit templates discovered
in each installed app's ``systemd/`` directory, so a deployment never has to
hard-code unit names.

By default units are managed in the system scope: installed under
``/etc/systemd/system`` and run as the ``User=`` they name. Pass ``--scope
user`` or set :setting:`SYSTEMD_SCOPE` to manage them in the invoking user's
manager instead. Privileges are never guessed; see :ref:`authorize`.

.. typer:: django_systemd.management.commands.systemd.Command:typer_app
    :prog: django-admin systemd
    :width: 80
    :convert-png: latex
"""

from __future__ import annotations

import shlex

# Only for subprocess.CalledProcessError; commands run in protocol.py.
import subprocess  # nosec B404
import tempfile
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import Annotated, TypeVar

import typer
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django_typer.management import TyperCommand, command, initialize

from django_systemd.config import (
    ServiceUnit,
    escalation,
    install_method,
    link_dir,
    project_units,
    render_engine,
)
from django_systemd.config import scope as settings_scope
from django_systemd.defines import InstallMethod, SystemdScope, SystemdUnitType
from django_systemd.protocol import SubprocessSystemdCtl, SystemdCtl
from django_systemd.signals import unit_installed

ContextOption = Annotated[
    list[str] | None,
    typer.Option(
        "--context",
        "-c",
        help="Override a template context variable as KEY=VALUE. May be repeated.",
    ),
]

UnitsArgument = Annotated[
    list[str] | None,
    typer.Argument(
        help="Unit file names to act on. Defaults to every installed project unit."
    ),
]

LinkDirOption = Annotated[
    Path | None,
    typer.Option(
        "--link-dir",
        help="Where rendered units are kept when linking. Must be absolute, "
        "outside systemd's unit search path, and on a file system mounted at "
        "boot (not a separate /home). Defaults to the SYSTEMD_LINK_DIR setting.",
    ),
]

_T = TypeVar("_T")

# Text a polkit denial's stderr contains; systemd itself uses both phrasings
# depending on the backend, so either is treated as a denial.
_POLKIT_DENIAL_MARKERS = ("Interactive authentication required", "Access denied")

_POLKIT_HINT = (
    " (add a polkit rule for the deploy user or configure SYSTEMD_ESCALATE; "
    "see the documentation on authorizing the deploy user)"
)


def describe_failure(err: subprocess.CalledProcessError) -> str:
    """One line naming the systemctl invocation and why it failed."""
    detail = (err.stderr or "").strip() or f"exit status {err.returncode}"
    message = f"{' '.join(err.cmd)} failed: {detail}"
    if any(marker in detail for marker in _POLKIT_DENIAL_MARKERS):
        message += _POLKIT_HINT
    return message


def parse_context(pairs: list[str]) -> dict[str, str]:
    """Turn ``["venv=/srv/app"]`` into ``{"venv": "/srv/app"}``."""
    context: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        key = key.strip()
        if not sep or not key:
            raise CommandError(f"Context overrides must be KEY=VALUE, got: {pair!r}")
        context[key] = value
    return context


class Command(TyperCommand):
    """
    The ``systemd`` management command: list, render, install, uninstall,
    restart and reload this project's units.

    The group callback :meth:`init` resolves ``scope`` and ``escalate`` from the
    ``--scope``/``--escalate`` options and the ``SYSTEMD_SCOPE``/``SYSTEMD_ESCALATE``
    settings before any subcommand runs. Code that calls a subcommand method
    directly (bypassing typer's own dispatch, e.g. in tests) must call
    :meth:`init` first so ``self.scope``/``self.escalate`` are set.
    """

    scope: SystemdScope
    escalate: tuple[str, ...]

    def _setting(self, reader: Callable[[], _T]) -> _T:
        """Call a ``config`` settings reader, turning a bad setting into a CommandError."""
        try:
            return reader()
        except ImproperlyConfigured as err:
            raise CommandError(str(err)) from err

    @initialize()
    def init(
        self,
        scope: Annotated[
            SystemdScope | None,
            typer.Option(
                "--scope",
                help="Manage units in the system manager or the invoking user's "
                "manager. Defaults to the SYSTEMD_SCOPE setting.",
            ),
        ] = None,
        escalate: Annotated[
            str | None,
            typer.Option(
                "--escalate",
                help="Privilege escalation prefix for privileged calls in the "
                'system scope, e.g. "sudo -n". Defaults to the SYSTEMD_ESCALATE '
                "setting.",
            ),
        ] = None,
    ) -> None:
        self.scope = scope or self._setting(settings_scope)
        self.escalate = (
            tuple(shlex.split(escalate))
            if escalate is not None
            else self._setting(escalation)
        )

    @cached_property
    def ctl(self) -> SystemdCtl:
        return SubprocessSystemdCtl(self.scope, escalate=self.escalate)

    @cached_property
    def units(self) -> list[ServiceUnit]:
        return project_units()

    def run_ctl(self, verb: Callable[..., None], *args: str) -> None:
        """Run a systemctl-backed verb, surfacing stderr on failure."""
        try:
            verb(*args)
        except subprocess.CalledProcessError as err:
            raise CommandError(describe_failure(err)) from err

    def require_systemctl(self) -> None:
        """Refuse to proceed when systemctl is not available on this system."""
        if not self.ctl.available:
            raise CommandError("systemctl is not available on this system.")

    def targets(self, names: list[str]) -> list[ServiceUnit]:
        """
        Resolve unit names to project units.

        The priority sort only makes the invocation and its output
        deterministic; systemd orders the jobs itself within the transaction.
        Template units (``name@.type``) are never targets because systemctl needs
        an instance name to act on them. With no names, every installed
        non-template unit is selected, except that a service with a timer or
        path unit of the same name is dropped: that service is triggered by
        its timer or path (typically a oneshot job) and restarting it directly
        would run the job, not just make it ready to run. Naming the service
        explicitly still restarts it.
        """
        by_name = {u.filename: u for u in self.units if not u.instanceable}
        template_names = {u.filename for u in self.units if u.instanceable}
        if names:
            templates = [name for name in names if name in template_names]
            if templates:
                raise CommandError(
                    f"Template units cannot be restarted directly: {', '.join(templates)}"
                )
            unknown = [name for name in names if name not in by_name]
            if unknown:
                raise CommandError(f"Unknown project unit(s): {', '.join(unknown)}")
            selected = [by_name[n] for n in dict.fromkeys(names)]
        else:
            selected = [
                u for u in by_name.values() if self.ctl.is_installed(u.filename)
            ]
            triggered = {
                u.name
                for u in selected
                if u.unit_type in (SystemdUnitType.TIMER, SystemdUnitType.PATH)
            }
            selected = [
                u
                for u in selected
                if not (u.unit_type is SystemdUnitType.SERVICE and u.name in triggered)
            ]
        return sorted(selected, key=lambda u: u.restart_priority)

    def render_units(
        self, dest: Path, context: dict[str, str] | None = None
    ) -> list[tuple[ServiceUnit, Path]]:
        """Render every project unit into ``dest`` as ``<dest>/<unit filename>``."""
        if dest.exists() and not dest.is_dir():
            raise CommandError(f"{dest} exists and is not a directory.")
        if not self.units:
            raise CommandError("No systemd unit templates found.")
        dest.mkdir(parents=True, exist_ok=True)
        # scope always wins over a user-supplied override: it reflects the scope
        # this very command is running in, e.g. after --scope user.
        context = {**(context or {}), "scope": self.scope.value}
        rendered: list[tuple[ServiceUnit, Path]] = []
        for unit in self.units:
            # Absolute: render-static 3.5 builds a Path from a SafeString, which
            # Python 3.11 rejects for a single relative path part.
            target = dest.absolute() / unit.filename
            try:
                for render in render_engine().render_each(
                    unit.template, dest=target, context=context
                ):
                    rendered.append((unit, Path(render.destination)))
            except (TemplateDoesNotExist, TemplateSyntaxError) as err:
                target.unlink(missing_ok=True)
                raise CommandError(f"Failed to render {unit.template}: {err}") from err
            except BaseException:
                # Anything else (e.g. NoReverseMatch from {% url %}) is a bug in the
                # template or the project and should surface with its traceback.
                target.unlink(missing_ok=True)
                raise
        return rendered

    @command(name="list")
    def list_units(self) -> None:
        """List this project's systemd units and whether each is installed, enabled and active."""
        if not self.units:
            typer.echo("No systemd unit templates found.")
            return
        width = max(len("UNIT"), *(len(u.filename) for u in self.units))
        typer.echo(
            f"{'UNIT':<{width}} {'INSTALLED':<10} {'ENABLED':<8} {'ACTIVE':<8} SOURCE"
        )
        available = self.ctl.available
        for unit in self.units:
            installed = self.ctl.is_installed(unit.filename)
            enabled = active = "-"
            if installed and available and not unit.instanceable:
                try:
                    enabled = "yes" if self.ctl.is_enabled(unit.filename) else "no"
                    active = "yes" if self.ctl.is_active(unit.filename) else "no"
                except subprocess.CalledProcessError as err:
                    raise CommandError(describe_failure(err)) from err
            typer.echo(
                f"{unit.filename:<{width}} {'yes' if installed else 'no':<10} "
                f"{enabled:<8} {active:<8} {unit.path}"
            )

    def _context(self, pairs: list[str] | None) -> dict[str, str]:
        """Parse --context overrides, refusing an override of the scope."""
        context = parse_context(pairs or [])
        if "scope" in context:
            raise CommandError("Set the scope with --scope, not --context.")
        return context

    @command()
    def render(
        self,
        output_dir: Annotated[
            Path | None,
            typer.Argument(
                help="Directory to render unit files into. Defaults to the current directory."
            ),
        ] = None,
        context: ContextOption = None,
    ) -> None:
        """Render this project's unit templates to a directory."""
        rendered = self.render_units(output_dir or Path("."), self._context(context))
        for _, path in rendered:
            typer.echo(str(path))

    def _permission_hint(
        self, action: str, preposition: str, unit: ServiceUnit, err: OSError
    ) -> str:
        """A CommandError message for a PermissionError raised by install/uninstall."""
        message = f"Failed to {action} {unit.filename} {preposition} {self.ctl.unit_dir}: {err}."
        if self.scope is SystemdScope.SYSTEM and not self.escalate:
            message += (
                " The system scope needs privileges: run as root, set SYSTEMD_ESCALATE"
                ' (for example "sudo -n"), or install with --method link and polkit'
                " rules. See the documentation on authorizing the deploy user."
            )
        return message

    def _copied_unit_conflict_message(self, unit: ServiceUnit) -> str:
        return (
            f"A copied unit file is already installed at "
            f"{self.ctl.unit_dir / unit.filename}; run `systemd uninstall` "
            "(with the method it was installed with) before linking."
        )

    def _install_failure_message(
        self, unit: ServiceUnit, err: subprocess.CalledProcessError
    ) -> str:
        """
        A CommandError message for a CalledProcessError from install_unit/link_unit.

        The usual conflict (a copied unit file already in the way) is caught
        proactively before ``link_unit`` is even called; this stderr-based check
        is only a fallback for a genuine race.
        """
        stderr = err.stderr or ""
        if "already exists" in stderr or "File exists" in stderr:
            return self._copied_unit_conflict_message(unit)
        return describe_failure(err)

    def _warn_if_world_writable(self, path: Path) -> None:
        if path.stat().st_mode & 0o002:
            typer.secho(f"warning: {path} is world-writable.", err=True)

    @command()
    def install(
        self,
        source: Annotated[
            Path | None,
            typer.Option(
                "--source",
                help="Install pre-rendered unit files from this directory instead of rendering now.",
                exists=True,
                file_okay=False,
            ),
        ] = None,
        enable: Annotated[
            bool,
            typer.Option(
                "--enable/--no-enable", help="Enable the units after installing."
            ),
        ] = False,
        method: Annotated[
            InstallMethod | None,
            typer.Option(
                "--method",
                help="Copy the units into the unit directory, or link them from "
                "--link-dir. Defaults to the SYSTEMD_INSTALL_METHOD setting.",
            ),
        ] = None,
        link_dir_option: LinkDirOption = None,
        context: ContextOption = None,
    ) -> None:
        """
        Install this project's units into the unit directory.

        Units are rendered first unless --source points at pre-rendered files.
        Running install again replaces the installed files, so this is also how
        you update units after a deploy. With --method link, the rendered files
        are kept in --link-dir and `systemctl link --force` places a symlink in
        the unit directory instead of copying into it.
        """
        if not self.units:
            raise CommandError("No systemd unit templates found.")
        if source is not None and context:
            raise CommandError("--context has no effect with --source.")
        if source is not None and link_dir_option is not None:
            raise CommandError("--link-dir has no effect with --source.")

        method = method or self._setting(install_method)
        target_dir = link_dir_option or self._setting(link_dir)

        if method is InstallMethod.LINK and not self.ctl.available:
            raise CommandError(
                "--method link needs systemctl, which is not available on this system."
            )

        if method is InstallMethod.LINK and source is None:
            if target_dir is None:
                raise CommandError(
                    "--method link needs a directory to keep the rendered units "
                    "in: pass --link-dir or set SYSTEMD_LINK_DIR."
                )
            if not target_dir.is_absolute():
                raise CommandError(f"--link-dir must be absolute, got {target_dir}.")
            if target_dir.exists() and not target_dir.is_dir():
                raise CommandError(f"{target_dir} exists and is not a directory.")
            # mode is subject to umask, which may reduce it further (e.g. to
            # 0o750); it is never widened past what we ask for here.
            target_dir.mkdir(parents=True, exist_ok=True, mode=0o755)

        with tempfile.TemporaryDirectory() as tmp:
            if source is None:
                if method is InstallMethod.LINK:
                    assert target_dir is not None  # validated above
                    dest = target_dir
                else:
                    dest = Path(tmp)
                files = self.render_units(dest, self._context(context))
                if method is InstallMethod.LINK:
                    self._warn_if_world_writable(dest)
                    for _, path in files:
                        self._warn_if_world_writable(path)
            else:
                # Pre-rendered units are flat files named by unit file name, exactly
                # as `systemd render` writes them.
                files = [(unit, source / unit.filename) for unit in self.units]
                missing = [path.name for _, path in files if not path.is_file()]
                if missing:
                    raise CommandError(
                        f"Missing unit files in {source}: {', '.join(missing)}"
                    )
                if method is InstallMethod.LINK:
                    for _, path in files:
                        self._warn_if_world_writable(path)
            for unit, path in files:
                if (
                    method is InstallMethod.LINK
                    and self.ctl.is_installed(unit.filename)
                    and self.ctl.linked_source(unit.filename) is None
                ):
                    # A regular (copied) file already occupies the destination:
                    # systemd's own "link --force" would refuse it too, but
                    # checking here doesn't depend on parsing its stderr.
                    raise CommandError(self._copied_unit_conflict_message(unit))
                try:
                    if method is InstallMethod.LINK:
                        destination = self.ctl.link_unit(path)
                    else:
                        destination = self.ctl.install_unit(path)
                except PermissionError as err:
                    raise CommandError(
                        self._permission_hint("install", "in", unit, err)
                    ) from err
                except OSError as err:
                    raise CommandError(
                        f"Failed to install {unit.filename} to {self.ctl.unit_dir}: {err}. "
                        "Re-run install once the cause is fixed."
                    ) from err
                except subprocess.CalledProcessError as err:
                    raise CommandError(
                        self._install_failure_message(unit, err)
                    ) from err
                unit_installed.send(sender=self, unit=unit, destination=destination)
                typer.echo(str(destination))

        if not self.ctl.available:
            typer.secho(
                "systemctl not found; skipped daemon-reload and enable.", err=True
            )
            return
        self.run_ctl(self.ctl.daemon_reload)
        if enable:
            for unit in self.units:
                if not unit.instanceable:
                    self.run_ctl(self.ctl.enable, unit.filename)

    @command()
    def uninstall(self, link_dir_option: LinkDirOption = None) -> None:
        """
        Stop, disable and remove this project's units from the unit directory.

        A unit installed with --method link leaves its rendered file in the
        link directory; pass --link-dir (or set SYSTEMD_LINK_DIR) to the same
        directory it was installed with so that file is removed too. Without a
        matching link directory, uninstall never deletes a linked source file
        it cannot confirm it owns; it is left in place and reported.
        """
        resolved_link_dir = link_dir_option or self._setting(link_dir)
        if resolved_link_dir is not None and not resolved_link_dir.is_absolute():
            raise CommandError(f"--link-dir must be absolute, got {resolved_link_dir}.")
        for unit in self.units:
            linked = self.ctl.linked_source(unit.filename)
            if (
                self.ctl.available
                and not unit.instanceable
                and (self.ctl.is_installed(unit.filename) or linked is not None)
            ):
                for verb in (self.ctl.stop, self.ctl.disable):
                    try:
                        verb(unit.filename)
                    except subprocess.CalledProcessError as err:
                        typer.secho(describe_failure(err), err=True)
            try:
                removed = self.ctl.uninstall_unit(unit.filename)
            except PermissionError as err:
                if linked is None:
                    raise CommandError(
                        self._permission_hint("remove", "from", unit, err)
                    ) from err
                typer.secho(
                    f"{unit.filename} is still linked from {self.ctl.unit_dir}: {err}",
                    err=True,
                )
                removed = False
            except OSError as err:
                raise CommandError(
                    f"Failed to remove {unit.filename} from {self.ctl.unit_dir}: {err}"
                ) from err
            except subprocess.CalledProcessError as err:
                raise CommandError(describe_failure(err)) from err
            if linked is not None:
                if (
                    resolved_link_dir is not None
                    and linked.parent.resolve() == resolved_link_dir.resolve()
                ):
                    try:
                        linked.unlink()
                        removed = True
                    except OSError as err:
                        typer.secho(f"could not remove {linked}: {err}", err=True)
                else:
                    typer.secho(
                        f"left {linked} in place (not under the link directory)",
                        err=True,
                    )
            if removed:
                typer.echo(f"removed {unit.filename}")
        if self.ctl.available:
            self.run_ctl(self.ctl.daemon_reload)
        else:
            typer.secho(
                "systemctl not found; skipped stop, disable and daemon-reload.",
                err=True,
            )

    @command()
    def restart(self, units: UnitsArgument = None) -> None:
        """
        Restart this project's units in a single systemctl transaction.

        Restarting sockets and services one at a time does not work: systemd
        refuses to start a socket whose service is still running. Passing every
        target to one systemctl invocation lets systemd order the stops and
        starts itself. A failure reports the whole invocation; systemctl's
        output names the job that failed.
        """
        self.require_systemctl()
        targets = self.targets(units or [])
        if not targets:
            typer.secho("No installed project units to restart.", err=True)
            return
        names = [unit.filename for unit in targets]
        self.run_ctl(self.ctl.restart, *names)
        typer.echo(f"restarted {' '.join(names)}")

    @command()
    def reload(self, units: UnitsArgument = None) -> None:
        """
        Reload services that support it and are running; restart everything else.

        Only services can define ExecReload=, and systemctl refuses to reload an
        inactive unit, so anything that is not an active reloadable service is
        restarted instead, in one systemctl transaction. A socket paired with a
        service that reloads in place is left as it is.
        """
        self.require_systemctl()
        targets = self.targets(units or [])
        if not targets:
            typer.secho("No installed project units to reload.", err=True)
            return
        to_reload: list[str] = []
        to_restart: list[ServiceUnit] = []
        for unit in targets:
            name = unit.filename
            if (
                unit.unit_type is SystemdUnitType.SERVICE
                and self.ctl.can_reload(name)
                and self.ctl.is_active(name)
            ):
                to_reload.append(name)
            else:
                to_restart.append(unit)
        # A listening socket whose service is reloaded in place needs no action,
        # and systemd would refuse to restart it while the service runs.
        kept = [
            u
            for u in to_restart
            if u.unit_type is SystemdUnitType.SOCKET
            and f"{u.name}.service" in to_reload
        ]
        to_restart = [u for u in to_restart if u not in kept]
        for unit in kept:
            typer.echo(
                f"kept {unit.filename} listening; {unit.name}.service reloads in place"
            )
        if to_restart:
            names = [u.filename for u in to_restart]
            self.run_ctl(self.ctl.restart, *names)
            typer.echo(f"restarted {' '.join(names)}")
        if to_reload:
            self.run_ctl(self.ctl.reload, *to_reload)
            typer.echo(f"reloaded {' '.join(to_reload)}")
