"""
Tests for the systemd management command. All systemctl interaction goes through
FakeCtl, so nothing here needs systemd installed.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import override_settings

from django_systemd.config import ServiceUnit, render_engine, template_engine_config
from django_systemd.defines import SystemdScope
from django_systemd.management.commands.systemd import Command, parse_context
from django_systemd.protocol import SystemdCtl
from django_systemd.signals import unit_installed


class FakeCtl:
    """An in-memory SystemdCtl that records every verb it is asked to run."""

    def __init__(
        self,
        unit_dir: Path,
        *,
        available: bool = True,
        reloadable: set[str] | None = None,
        scope: SystemdScope = SystemdScope.SYSTEM,
        escalate: Sequence[str] = (),
    ) -> None:
        self.unit_dir = unit_dir
        self.available = available
        self.scope = scope
        self.escalate = tuple(escalate)
        self.reloadable = reloadable or set()
        self.calls: list[tuple[str, str]] = []
        self.active: set[str] = set()
        self.enabled: set[str] = set()
        # verb -> stderr text; when set, that verb raises CalledProcessError instead
        # of recording a call.
        self.fail: dict[str, str] = {}

    def _maybe_fail(self, verb: str, *args: str) -> None:
        if verb not in self.fail:
            return
        value = self.fail[verb]
        if verb in ("install", "uninstall"):
            if value == "EACCES":
                raise PermissionError(13, "Permission denied")
            raise subprocess.CalledProcessError(
                1, ["sudo", "-n", verb, *args], "", value
            )
        flags = [] if self.scope is SystemdScope.SYSTEM else ["--user"]
        raise subprocess.CalledProcessError(
            1, ["systemctl", *flags, verb, *args], "", value
        )

    def daemon_reload(self) -> None:
        self._maybe_fail("daemon-reload")
        self.calls.append(("daemon-reload", ""))

    def restart(self, *units: str) -> None:
        self._maybe_fail("restart", *units)
        self.calls.append(("restart", units))
        self.active.update(units)

    def reload(self, *units: str) -> None:
        self._maybe_fail("reload", *units)
        self.calls.append(("reload", units))

    def stop(self, unit: str) -> None:
        self._maybe_fail("stop", unit)
        self.calls.append(("stop", unit))

    def can_reload(self, unit: str) -> bool:
        return unit in self.reloadable

    def enable(self, unit: str) -> None:
        self._maybe_fail("enable", unit)
        self.calls.append(("enable", unit))
        self.enabled.add(unit)

    def disable(self, unit: str) -> None:
        self._maybe_fail("disable", unit)
        self.calls.append(("disable", unit))
        self.enabled.discard(unit)
        # Mirrors "systemctl disable" on a unit installed with "systemctl link":
        # disable removes the symlink it created in the unit directory.
        destination = self.unit_dir / unit
        if destination.is_symlink():
            destination.unlink()

    def is_active(self, unit: str) -> bool:
        self._maybe_fail("is-active", unit)
        self.calls.append(("is-active", unit))
        return unit in self.active

    def is_enabled(self, unit: str) -> bool:
        self._maybe_fail("is-enabled", unit)
        self.calls.append(("is-enabled", unit))
        return unit in self.enabled

    def is_installed(self, name: str) -> bool:
        return (self.unit_dir / name).is_file()

    def install_unit(
        self, source: Path, *, name: str | None = None, mode: int = 0o644
    ) -> Path:
        self._maybe_fail("install", source.name)
        # Not recorded in self.calls: install ordering (e.g. daemon-reload coming
        # after every unit is copied) is asserted via daemon-reload's position.
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        destination = self.unit_dir / (name or source.name)
        destination.write_bytes(source.read_bytes())
        return destination

    def uninstall_unit(self, name: str) -> bool:
        self._maybe_fail("uninstall", name)
        destination = self.unit_dir / name
        if destination.is_file() or destination.is_symlink():
            self.calls.append(("uninstall", name))
            destination.unlink()
            return True
        return False

    def link_unit(self, source: Path) -> Path:
        self._maybe_fail("link", source.name)
        self.unit_dir.mkdir(parents=True, exist_ok=True)
        destination = self.unit_dir / source.name
        if destination.exists() and not destination.is_symlink():
            # Mirrors "systemctl link --force": a symlink is replaced, but a
            # regular file at the destination is rejected.
            raise subprocess.CalledProcessError(
                1,
                [
                    "systemctl",
                    "--no-ask-password",
                    "link",
                    "--force",
                    "--",
                    str(source),
                ],
                "",
                f"Failed to link unit: File {destination} already exists.",
            )
        if destination.is_symlink():
            destination.unlink()
        destination.symlink_to(source.absolute())
        self.calls.append(("link", source.name))
        return destination

    def linked_source(self, name: str) -> Path | None:
        destination = self.unit_dir / name
        if destination.is_symlink():
            target = Path(os.readlink(destination))
            return target if target.is_absolute() else destination.parent / target
        return None


@pytest.fixture
def fake_ctl(tmp_path):
    ctl = FakeCtl(tmp_path / "units")
    with mock.patch(
        "django_systemd.management.commands.systemd.SubprocessSystemdCtl",
        return_value=ctl,
    ) as constructor:
        ctl.constructor = constructor
        yield ctl


@pytest.fixture
def make_ctl(tmp_path):
    def factory(**kwargs):
        ctl = FakeCtl(tmp_path / "units", **kwargs)
        patcher = mock.patch(
            "django_systemd.management.commands.systemd.SubprocessSystemdCtl",
            return_value=ctl,
        )
        patcher.start()
        patchers.append(patcher)
        return ctl

    patchers: list = []
    yield factory
    for patcher in patchers:
        patcher.stop()


@pytest.fixture
def no_units():
    """Run the body with no app providing systemd templates."""
    with override_settings(INSTALLED_APPS=["django_systemd", "django_typer"]):
        template_engine_config.cache_clear()
        render_engine.cache_clear()
        yield
    template_engine_config.cache_clear()
    render_engine.cache_clear()


def test_fake_ctl_satisfies_protocol(tmp_path):
    assert isinstance(FakeCtl(tmp_path), SystemdCtl)


@pytest.mark.django_db
class TestScopeAndEscalation:
    def test_defaults_come_from_settings(self, fake_ctl):
        call_command("systemd", "list")
        fake_ctl.constructor.assert_called_once_with(SystemdScope.SYSTEM, escalate=())

    def test_scope_setting(self, fake_ctl):
        with override_settings(SYSTEMD_SCOPE="user"):
            call_command("systemd", "list")
        fake_ctl.constructor.assert_called_once_with(SystemdScope.USER, escalate=())

    def test_scope_flag_overrides_setting(self, fake_ctl):
        call_command("systemd", "--scope", "user", "list")
        fake_ctl.constructor.assert_called_once_with(SystemdScope.USER, escalate=())

    def test_escalate_flag_and_setting(self, fake_ctl):
        with override_settings(SYSTEMD_ESCALATE="doas"):
            call_command("systemd", "list")
        fake_ctl.constructor.assert_called_with(SystemdScope.SYSTEM, escalate=("doas",))
        fake_ctl.constructor.reset_mock()
        call_command("systemd", "--escalate", "sudo -n", "list")
        fake_ctl.constructor.assert_called_with(
            SystemdScope.SYSTEM, escalate=("sudo", "-n")
        )

    def test_invalid_scope_flag(self, fake_ctl):
        # typer rejects the choice as a usage error; django-typer surfaces that as
        # SystemExit or CommandError depending on version. Either is a rejection.
        with pytest.raises((SystemExit, CommandError)):
            call_command("systemd", "--scope", "root", "list")

    def test_invalid_scope_setting_is_a_command_error(self, fake_ctl):
        with override_settings(SYSTEMD_SCOPE="root"):
            with pytest.raises(CommandError, match="SYSTEMD_SCOPE"):
                call_command("systemd", "list")

    def test_invalid_escalate_setting_is_a_command_error(self, fake_ctl):
        with override_settings(SYSTEMD_ESCALATE=12345):
            with pytest.raises(CommandError, match="SYSTEMD_ESCALATE"):
                call_command("systemd", "list")

    @pytest.mark.render
    def test_scope_reaches_templates(self, fake_ctl, tmp_path):
        apps = ["tests.apps.app3", *settings.INSTALLED_APPS]
        with override_settings(
            INSTALLED_APPS=apps, SYSTEMD_TEMPLATES=["**/scoped.service"]
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "render", str(tmp_path / "sys"))
            call_command("systemd", "--scope", "user", "render", str(tmp_path / "usr"))
        assert (
            "WantedBy=multi-user.target"
            in (tmp_path / "sys" / "scoped.service").read_text()
        )
        assert (
            "WantedBy=default.target"
            in (tmp_path / "usr" / "scoped.service").read_text()
        )

    def test_list_never_escalates(self, fake_ctl):
        fake_ctl.unit_dir.mkdir(parents=True)
        (fake_ctl.unit_dir / "web.service").write_text("x")
        call_command("systemd", "--escalate", "sudo -n", "list")
        verbs = {verb for verb, _ in fake_ctl.calls}
        assert verbs <= {"is-active", "is-enabled"}


@pytest.mark.django_db
class TestLinkInstall:
    def test_link_renders_into_link_dir_and_links(self, fake_ctl, tmp_path, capsys):
        link_dir = tmp_path / "rendered"
        call_command(
            "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
        )
        for name in ("web.service", "check.timer", "app@.target"):
            assert (link_dir / name).is_file()
            assert (fake_ctl.unit_dir / name).is_symlink()
            assert (
                fake_ctl.unit_dir.joinpath(name).resolve()
                == (link_dir / name).resolve()
            )
        linked = [u for verb, u in fake_ctl.calls if verb == "link"]
        assert sorted(linked) == ["app@.target", "check.timer", "web.service"]
        assert fake_ctl.calls[-1] == ("daemon-reload", "")
        assert str(fake_ctl.unit_dir / "web.service") in capsys.readouterr().out

    def test_link_from_settings(self, fake_ctl, tmp_path):
        with override_settings(
            SYSTEMD_INSTALL_METHOD="link", SYSTEMD_LINK_DIR=str(tmp_path / "r")
        ):
            call_command("systemd", "install")
        assert (fake_ctl.unit_dir / "web.service").is_symlink()

    def test_link_requires_a_link_dir(self, fake_ctl):
        with pytest.raises(CommandError, match="--link-dir"):
            call_command("systemd", "install", "--method", "link")

    def test_link_dir_must_be_absolute(self, fake_ctl):
        with pytest.raises(CommandError, match="absolute"):
            call_command(
                "systemd", "install", "--method", "link", "--link-dir", "relative/dir"
            )

    def test_link_requires_systemctl(self, make_ctl, tmp_path):
        make_ctl(available=False)
        link_dir = tmp_path / "rendered"
        with pytest.raises(CommandError, match="systemctl"):
            call_command(
                "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
            )
        assert not link_dir.exists()

    def test_link_from_source_dir(self, fake_ctl, tmp_path):
        source = tmp_path / "pre"
        source.mkdir()
        for name in ("web.service", "check.timer", "app@.target"):
            (source / name).write_text("pre")
        call_command("systemd", "install", "--method", "link", "--source", str(source))
        assert (fake_ctl.unit_dir / "web.service").resolve() == (
            source / "web.service"
        ).resolve()

    def test_rerun_relinks(self, fake_ctl, tmp_path):
        link_dir = tmp_path / "rendered"
        call_command(
            "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
        )
        call_command(
            "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
        )
        assert (fake_ctl.unit_dir / "web.service").is_symlink()

    def test_uninstall_removes_linked_source(self, fake_ctl, tmp_path):
        link_dir = tmp_path / "rendered"
        call_command(
            "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
        )
        call_command("systemd", "uninstall", "--link-dir", str(link_dir))
        assert not any(link_dir.iterdir())
        assert not any(fake_ctl.unit_dir.iterdir())
        assert ("disable", "web.service") in fake_ctl.calls

    def test_uninstall_removes_linked_source_via_setting(self, fake_ctl, tmp_path):
        link_dir = tmp_path / "rendered"
        with override_settings(SYSTEMD_LINK_DIR=str(link_dir)):
            call_command("systemd", "install", "--method", "link")
            call_command("systemd", "uninstall")
        assert not any(link_dir.iterdir())
        assert not any(fake_ctl.unit_dir.iterdir())

    def test_uninstall_matches_link_dir_through_a_directory_symlink(
        self, fake_ctl, tmp_path
    ):
        real = tmp_path / "real"
        real.mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(real)
        call_command("systemd", "install", "--method", "link", "--link-dir", str(alias))
        call_command("systemd", "uninstall", "--link-dir", str(real))
        assert not any(real.iterdir())
        assert not any(fake_ctl.unit_dir.iterdir())

    def test_uninstall_relative_link_dir_is_a_command_error(self, fake_ctl):
        with pytest.raises(CommandError, match="absolute"):
            call_command("systemd", "uninstall", "--link-dir", "relative/dir")

    def test_uninstall_without_link_dir_leaves_source_in_place(
        self, fake_ctl, tmp_path, capsys
    ):
        link_dir = tmp_path / "rendered"
        call_command(
            "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
        )
        call_command("systemd", "uninstall")
        assert (link_dir / "web.service").is_file()
        err = capsys.readouterr().err
        assert "left" in err and "in place" in err

    def test_uninstall_leaves_source_installed_units_in_place(
        self, fake_ctl, tmp_path, capsys
    ):
        source = tmp_path / "pre"
        source.mkdir()
        for name in ("web.service", "check.timer", "app@.target"):
            (source / name).write_text("pre")
        call_command("systemd", "install", "--method", "link", "--source", str(source))
        call_command("systemd", "uninstall")
        assert (source / "web.service").is_file()
        err = capsys.readouterr().err
        assert "left" in err and "in place" in err

    def test_uninstall_unlink_failure_is_reported_not_fatal(
        self, fake_ctl, tmp_path, capsys, monkeypatch
    ):
        link_dir = tmp_path / "rendered"
        call_command(
            "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
        )
        original_unlink = Path.unlink

        def flaky_unlink(self, *args, **kwargs):
            if self.parent == link_dir and self.name == "web.service":
                raise OSError("disk full")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", flaky_unlink)
        call_command("systemd", "uninstall", "--link-dir", str(link_dir))
        err = capsys.readouterr().err
        assert "could not remove" in err
        assert (link_dir / "web.service").exists()

    def test_uninstall_reports_leftover_symlink(self, fake_ctl, tmp_path, capsys):
        link_dir = tmp_path / "rendered"
        call_command(
            "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
        )
        fake_ctl.fail = {"disable": "Failed to connect to bus", "uninstall": "EACCES"}
        call_command("systemd", "uninstall", "--link-dir", str(link_dir))
        err = capsys.readouterr().err
        assert "still linked" in err
        assert not (link_dir / "web.service").exists()

    def test_link_over_copied_unit_names_the_conflict(self, fake_ctl, tmp_path):
        call_command("systemd", "install")
        with pytest.raises(CommandError, match="systemd uninstall"):
            call_command(
                "systemd",
                "install",
                "--method",
                "link",
                "--link-dir",
                str(tmp_path / "r"),
            )
        assert (fake_ctl.unit_dir / "web.service").is_file()
        assert not (fake_ctl.unit_dir / "web.service").is_symlink()
        assert not any(verb == "link" for verb, _ in fake_ctl.calls)

    def test_link_race_conflict_falls_back_to_stderr(self, fake_ctl, tmp_path):
        # The proactive is_installed/linked_source check can't catch a genuine
        # race (something else occupies the destination between our check and
        # systemctl link); the stderr fallback in _install_failure_message
        # covers that case, recognizing systemd's own "already exists" wording.
        fake_ctl.fail = {"link": "Failed to link unit: File already exists."}
        with pytest.raises(CommandError, match="systemd uninstall"):
            call_command(
                "systemd",
                "install",
                "--method",
                "link",
                "--link-dir",
                str(tmp_path / "r"),
            )

    def test_link_dir_is_a_file(self, fake_ctl, tmp_path):
        target = tmp_path / "not-a-dir"
        target.write_text("x")
        with pytest.raises(CommandError, match="not a directory"):
            call_command(
                "systemd", "install", "--method", "link", "--link-dir", str(target)
            )

    def test_link_dir_with_source_is_an_error(self, fake_ctl, tmp_path):
        source = tmp_path / "pre"
        source.mkdir()
        with pytest.raises(CommandError, match="--link-dir"):
            call_command(
                "systemd",
                "install",
                "--method",
                "link",
                "--source",
                str(source),
                "--link-dir",
                str(tmp_path / "r"),
            )

    def test_world_writable_source_file_warns(self, fake_ctl, tmp_path, capsys):
        source = tmp_path / "pre"
        source.mkdir()
        for name in ("web.service", "check.timer", "app@.target"):
            (source / name).write_text("pre")
        (source / "web.service").chmod(0o666)
        call_command("systemd", "install", "--method", "link", "--source", str(source))
        assert "world-writable" in capsys.readouterr().err

    def test_polkit_denial_hints_at_the_rule(self, fake_ctl, tmp_path):
        fake_ctl.fail = {"link": "Interactive authentication required."}
        with pytest.raises(CommandError, match="polkit"):
            call_command(
                "systemd",
                "install",
                "--method",
                "link",
                "--link-dir",
                str(tmp_path / "r"),
            )

    def test_world_writable_link_dir_warns(self, fake_ctl, tmp_path, capsys):
        link_dir = tmp_path / "rendered"
        link_dir.mkdir()
        link_dir.chmod(0o777)
        call_command(
            "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
        )
        assert "world-writable" in capsys.readouterr().err

    def test_invalid_install_method_setting_is_a_command_error(self, fake_ctl):
        with override_settings(SYSTEMD_INSTALL_METHOD="teleport"):
            with pytest.raises(CommandError, match="SYSTEMD_INSTALL_METHOD"):
                call_command("systemd", "install")

    def test_invalid_link_dir_setting_is_a_command_error(self, fake_ctl):
        with override_settings(
            SYSTEMD_INSTALL_METHOD="link", SYSTEMD_LINK_DIR="relative/dir"
        ):
            with pytest.raises(CommandError, match="SYSTEMD_LINK_DIR"):
                call_command("systemd", "install")

    def test_link_dir_created_with_0755(self, fake_ctl, tmp_path):
        link_dir = tmp_path / "rendered"
        old_umask = os.umask(0)
        try:
            call_command(
                "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
            )
        finally:
            os.umask(old_umask)
        assert link_dir.stat().st_mode & 0o777 == 0o755

    def test_dangling_link_is_still_disabled(self, fake_ctl, tmp_path):
        # The rendered source is deleted before uninstall runs: the symlink in
        # unit_dir is now dangling, so is_installed is False, but linked_source
        # still finds it and stop/disable must still run to remove the link.
        link_dir = tmp_path / "rendered"
        call_command(
            "systemd", "install", "--method", "link", "--link-dir", str(link_dir)
        )
        for f in link_dir.iterdir():
            f.unlink()
        assert not fake_ctl.is_installed("web.service")
        call_command("systemd", "uninstall")
        disabled = {unit for verb, unit in fake_ctl.calls if verb == "disable"}
        assert "web.service" in disabled


@pytest.mark.django_db
class TestPermissionErrors:
    def test_copy_permission_error_is_actionable(self, fake_ctl):
        fake_ctl.fail = {"install": "EACCES"}
        with pytest.raises(CommandError) as exc:
            call_command("systemd", "install")
        message = str(exc.value)
        for hint in ("root", "SYSTEMD_ESCALATE", "--method link"):
            assert hint in message

    def test_user_scope_permission_error_has_no_escalation_hint(self, fake_ctl):
        fake_ctl.fail = {"install": "EACCES"}
        with pytest.raises(CommandError) as exc:
            call_command("systemd", "--scope", "user", "install")
        assert "SYSTEMD_ESCALATE" not in str(exc.value)

    def test_escalated_install_failure_shows_stderr(self, fake_ctl):
        fake_ctl.fail = {"install": "sudo: a password is required"}
        with pytest.raises(CommandError, match="password is required"):
            call_command("systemd", "--escalate", "sudo -n", "install")

    def test_escalated_uninstall_failure_shows_stderr(self, fake_ctl):
        call_command("systemd", "install")
        fake_ctl.fail = {"uninstall": "sudo: a password is required"}
        with pytest.raises(CommandError, match="password is required"):
            call_command("systemd", "--escalate", "sudo -n", "uninstall")

    def test_uninstall_permission_error_without_link_is_fatal(self, fake_ctl):
        call_command("systemd", "install")
        fake_ctl.fail = {"uninstall": "EACCES"}
        with pytest.raises(CommandError) as exc:
            call_command("systemd", "uninstall")
        message = str(exc.value)
        assert "still linked" not in message
        for hint in ("root", "SYSTEMD_ESCALATE", "--method link"):
            assert hint in message

    def test_uninstall_permission_error_user_scope_no_hint(self, fake_ctl):
        call_command("systemd", "--scope", "user", "install")
        fake_ctl.fail = {"uninstall": "EACCES"}
        with pytest.raises(CommandError) as exc:
            call_command("systemd", "--scope", "user", "uninstall")
        assert "SYSTEMD_ESCALATE" not in str(exc.value)

    def test_daemon_reload_polkit_denial_hints_at_the_rule(self, fake_ctl):
        fake_ctl.fail = {"daemon-reload": "Interactive authentication required."}
        with pytest.raises(CommandError, match="polkit"):
            call_command("systemd", "install")


@pytest.mark.django_db
class TestList:
    def test_no_units(self, fake_ctl, no_units, capsys):
        call_command("systemd", "list")
        assert "No systemd unit templates found" in capsys.readouterr().out

    def test_lists_every_project_unit_with_source(self, fake_ctl, capsys):
        call_command("systemd", "list")
        out = capsys.readouterr().out
        assert "UNIT" in out and "INSTALLED" in out
        for name in ("web.service", "check.timer", "app@.target"):
            assert name in out
        assert "app2" in out

    def test_not_installed_rows_do_not_query_systemctl(self, fake_ctl, capsys):
        call_command("systemd", "list")
        out = capsys.readouterr().out
        assert fake_ctl.calls == []
        row = next(line for line in out.splitlines() if line.startswith("web.service"))
        assert row.split()[1:4] == ["no", "-", "-"]

    def test_installed_rows_show_state(self, fake_ctl, capsys):
        fake_ctl.unit_dir.mkdir(parents=True)
        (fake_ctl.unit_dir / "web.service").write_text("x")
        fake_ctl.active.add("web.service")
        call_command("systemd", "list")
        out = capsys.readouterr().out
        row = next(line for line in out.splitlines() if line.startswith("web.service"))
        assert row.split()[1:4] == ["yes", "no", "yes"]

    def test_instanceable_units_are_never_queried(self, fake_ctl, capsys):
        fake_ctl.unit_dir.mkdir(parents=True)
        (fake_ctl.unit_dir / "app@.target").write_text("x")
        call_command("systemd", "list")
        out = capsys.readouterr().out
        row = next(line for line in out.splitlines() if line.startswith("app@.target"))
        assert row.split()[1:4] == ["yes", "-", "-"]
        assert fake_ctl.calls == []

    def test_unreachable_bus_is_a_command_error(self, fake_ctl):
        fake_ctl.unit_dir.mkdir(parents=True)
        (fake_ctl.unit_dir / "web.service").write_text("x")
        fake_ctl.fail = {"is-active": "Failed to connect to bus"}
        with pytest.raises(CommandError, match="Failed to connect to bus"):
            call_command("systemd", "list")

    def test_unavailable_systemctl_shows_dashes(self, make_ctl, capsys):
        ctl = make_ctl(available=False)
        ctl.unit_dir.mkdir(parents=True)
        (ctl.unit_dir / "web.service").write_text("x")
        call_command("systemd", "list")
        row = next(
            line
            for line in capsys.readouterr().out.splitlines()
            if line.startswith("web.service")
        )
        assert row.split()[1:4] == ["yes", "-", "-"]


@pytest.mark.render
@pytest.mark.django_db
class TestRender:
    def test_creates_files(self, fake_ctl, tmp_path, capsys):
        call_command("systemd", "render", str(tmp_path))
        files = {f.name for f in tmp_path.rglob("*") if f.is_file()}
        assert files == {"web.service", "check.timer", "app@.target"}
        out = capsys.readouterr().out
        assert str(tmp_path / "web.service") in out

    def test_default_dir_is_cwd(self, fake_ctl, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        call_command("systemd", "render")
        assert (tmp_path / "web.service").is_file()

    def test_default_dir_from_setting(self, fake_ctl, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with override_settings(SYSTEMD_RENDER_DIR=str(tmp_path / "units")):
            call_command("systemd", "render")
        assert (tmp_path / "units" / "web.service").is_file()
        assert not (tmp_path / "web.service").exists()

    def test_relative_setting_is_relative_to_cwd(self, fake_ctl, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with override_settings(SYSTEMD_RENDER_DIR="units"):
            call_command("systemd", "render")
        assert (tmp_path / "units" / "web.service").is_file()

    def test_argument_overrides_setting(self, fake_ctl, tmp_path):
        with override_settings(SYSTEMD_RENDER_DIR=str(tmp_path / "units")):
            call_command("systemd", "render", str(tmp_path / "explicit"))
        assert (tmp_path / "explicit" / "web.service").is_file()
        assert not (tmp_path / "units").exists()

    def test_highest_precedence_content(self, fake_ctl, tmp_path):
        call_command("systemd", "render", str(tmp_path))
        assert "app2 override" in (tmp_path / "web.service").read_text()

    def test_context_overrides(self, fake_ctl, tmp_path):
        call_command(
            "systemd",
            "render",
            str(tmp_path),
            "-c",
            "venv=/srv/app/.venv",
            "--context",
            "python=/srv/app/.venv/bin/python",
        )
        content = (tmp_path / "web.service").read_text()
        assert "WorkingDirectory=/srv/app/.venv" in content
        assert "ExecStart=/srv/app/.venv/bin/python" in content

    def test_renders_without_html_autoescaping(self, fake_ctl, tmp_path):
        call_command(
            "systemd",
            "render",
            str(tmp_path),
            "-c",
            "python=/a&b/'py'",
        )
        content = (tmp_path / "web.service").read_text()
        assert "ExecStart=/a&b/'py'" in content

    def test_bad_context_pair(self, fake_ctl, tmp_path):
        with pytest.raises(CommandError, match="KEY=VALUE"):
            call_command("systemd", "render", str(tmp_path), "-c", "novalue")
        with pytest.raises(CommandError, match="KEY=VALUE"):
            call_command("systemd", "render", str(tmp_path), "-c", "=x")

    def test_no_templates(self, fake_ctl, no_units, tmp_path):
        with pytest.raises(CommandError, match="No systemd unit templates"):
            call_command("systemd", "render", str(tmp_path))

    def test_output_path_is_a_file(self, fake_ctl, tmp_path):
        target = tmp_path / "not-a-dir"
        target.write_text("x")
        with pytest.raises(CommandError, match="not a directory"):
            call_command("systemd", "render", str(target))

    def test_broken_template_errors_without_partial_output(self, fake_ctl, tmp_path):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/broken.service"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            with pytest.raises(CommandError, match="broken.service"):
                call_command("systemd", "render", str(tmp_path))
        assert not (tmp_path / "broken.service").exists()

    def test_non_template_error_still_cleans_up(self, fake_ctl, tmp_path):
        from django.conf import settings
        from django.urls import NoReverseMatch

        apps = ["tests.apps.app3", *settings.INSTALLED_APPS]
        with override_settings(
            INSTALLED_APPS=apps, SYSTEMD_TEMPLATES=["**/runtime.service"]
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            with pytest.raises(NoReverseMatch):
                call_command("systemd", "render", str(tmp_path))
        assert not (tmp_path / "runtime.service").exists()

    def test_systemd_templates_setting_scopes_render(self, fake_ctl, tmp_path):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/*.timer"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "render", str(tmp_path))
        assert (tmp_path / "my.app.timer").is_file()
        assert (tmp_path / "check.timer").is_file()
        assert not any(p.is_dir() for p in tmp_path.iterdir())

    def test_nested_template_renders_flat(self, fake_ctl, tmp_path):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", "django_systemd", "django_typer"],
            SYSTEMD_TEMPLATES=["**/web.service"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "render", str(tmp_path))
        content = (tmp_path / "web.service").read_text()
        assert "nested web (app3)" in content
        assert not (tmp_path / "sub").exists()

    def test_context_cannot_set_scope(self, fake_ctl, tmp_path):
        with pytest.raises(CommandError, match="--scope"):
            call_command("systemd", "render", str(tmp_path), "-c", "scope=user")


@pytest.mark.django_db
class TestInstall:
    def test_installs_every_unit_and_reloads_once(self, fake_ctl, capsys):
        call_command("systemd", "install")
        installed = {f.name for f in fake_ctl.unit_dir.iterdir()}
        assert installed == {"web.service", "check.timer", "app@.target"}
        assert "app2 override" in (fake_ctl.unit_dir / "web.service").read_text()
        assert fake_ctl.calls == [("daemon-reload", "")]
        out = capsys.readouterr().out
        assert str(fake_ctl.unit_dir / "web.service") in out

    def test_rerun_updates_in_place(self, fake_ctl):
        call_command("systemd", "install")
        (fake_ctl.unit_dir / "web.service").write_text("stale")
        call_command("systemd", "install")
        assert "stale" not in (fake_ctl.unit_dir / "web.service").read_text()

    def test_enable_skips_template_units(self, fake_ctl):
        call_command("systemd", "install", "--enable")
        enabled = {unit for verb, unit in fake_ctl.calls if verb == "enable"}
        assert enabled == {"web.service", "check.timer"}
        assert fake_ctl.calls.index(("daemon-reload", "")) < fake_ctl.calls.index(
            ("enable", "web.service")
        )

    def test_context_overrides(self, fake_ctl):
        call_command("systemd", "install", "-c", "venv=/srv/app/.venv")
        assert (
            "WorkingDirectory=/srv/app/.venv"
            in (fake_ctl.unit_dir / "web.service").read_text()
        )

    def test_source_dir_skips_rendering(self, fake_ctl, tmp_path):
        source = tmp_path / "prerendered"
        source.mkdir()
        for name in ("web.service", "check.timer", "app@.target"):
            (source / name).write_text(f"prerendered {name}")
        call_command("systemd", "install", "--source", str(source))
        assert (
            fake_ctl.unit_dir / "web.service"
        ).read_text() == "prerendered web.service"

    def test_source_dir_missing_unit_errors(self, fake_ctl, tmp_path):
        source = tmp_path / "prerendered"
        source.mkdir()
        (source / "web.service").write_text("x")
        with pytest.raises(CommandError, match="check.timer"):
            call_command("systemd", "install", "--source", str(source))
        assert not fake_ctl.unit_dir.exists()

    def test_sends_unit_installed(self, fake_ctl):
        received: list[dict] = []

        def receiver(sender, **kwargs):
            received.append({"sender": sender, **kwargs})

        unit_installed.connect(receiver)
        try:
            call_command("systemd", "install")
        finally:
            unit_installed.disconnect(receiver)
        assert len(received) == 3
        for event in received:
            assert isinstance(event["sender"], Command)
            assert isinstance(event["unit"], ServiceUnit)
            assert event["destination"] == fake_ctl.unit_dir / event["unit"].filename

    def test_no_units_errors(self, fake_ctl, no_units):
        with pytest.raises(CommandError, match="No systemd unit templates"):
            call_command("systemd", "install")

    def test_without_systemctl_still_copies(self, make_ctl, capsys):
        ctl = make_ctl(available=False)
        call_command("systemd", "install", "--enable")
        assert (ctl.unit_dir / "web.service").is_file()
        assert ctl.calls == []
        assert "systemctl not found" in capsys.readouterr().err

    def test_source_with_context_is_an_error(self, fake_ctl, tmp_path):
        source = tmp_path / "prerendered"
        source.mkdir()
        with pytest.raises(CommandError, match="--context"):
            call_command(
                "systemd",
                "install",
                "--source",
                str(source),
                "-c",
                "venv=/x",
            )

    def test_daemon_reload_failure_is_a_command_error(self, fake_ctl):
        fake_ctl.fail = {"daemon-reload": "Failed to connect to bus: No medium found"}
        with pytest.raises(CommandError, match="Failed to connect to bus"):
            call_command("systemd", "install")
        installed = {f.name for f in fake_ctl.unit_dir.iterdir()}
        assert installed == {"web.service", "check.timer", "app@.target"}

    def test_enable_failure_is_a_command_error(self, fake_ctl):
        fake_ctl.fail = {"enable": "Unit has no installation config"}
        with pytest.raises(CommandError, match="no installation config"):
            call_command("systemd", "install", "--enable")

    def test_install_failure_names_the_unit(self, fake_ctl):
        original = fake_ctl.install_unit
        state = {"calls": 0, "failing_name": None}

        def flaky(source, **kwargs):
            state["calls"] += 1
            if state["calls"] == 2:
                state["failing_name"] = source.name
                raise OSError("disk full")
            return original(source, **kwargs)

        fake_ctl.install_unit = flaky  # type: ignore[method-assign]
        with pytest.raises(CommandError) as exc_info:
            call_command("systemd", "install")
        message = str(exc_info.value)
        # The second install_unit call is the one that fails; assert the exact
        # name that raised appears in the error, not just any project unit name.
        assert state["failing_name"] is not None
        assert state["failing_name"] in message
        assert "disk full" in message
        assert ("daemon-reload", "") not in fake_ctl.calls

    def test_empty_stderr_reports_exit_status(self, fake_ctl):
        fake_ctl.fail = {"daemon-reload": ""}
        with pytest.raises(CommandError, match="exit status 1"):
            call_command("systemd", "install")

    def test_context_cannot_set_scope(self, fake_ctl):
        with pytest.raises(CommandError, match="--scope"):
            call_command("systemd", "install", "-c", "scope=user")


@pytest.mark.django_db
class TestUninstall:
    def test_disables_removes_reloads(self, fake_ctl, capsys):
        call_command("systemd", "install", "--enable")
        fake_ctl.calls.clear()
        call_command("systemd", "uninstall")
        assert not any(fake_ctl.unit_dir.iterdir())
        disabled = {unit for verb, unit in fake_ctl.calls if verb == "disable"}
        assert disabled == {"web.service", "check.timer"}
        assert fake_ctl.calls.index(("stop", "web.service")) < fake_ctl.calls.index(
            ("disable", "web.service")
        )
        assert fake_ctl.calls.index(("disable", "web.service")) < fake_ctl.calls.index(
            ("uninstall", "web.service")
        )
        target_calls = [call for call in fake_ctl.calls if call[1] == "app@.target"]
        assert target_calls == [("uninstall", "app@.target")]
        assert fake_ctl.calls[-1] == ("daemon-reload", "")
        out = capsys.readouterr().out
        assert "web.service" in out

    def test_nothing_installed_is_a_noop(self, fake_ctl, capsys):
        call_command("systemd", "uninstall")
        assert "removed" not in capsys.readouterr().out
        assert fake_ctl.calls[-1] == ("daemon-reload", "")
        assert not any(verb in ("stop", "disable") for verb, _ in fake_ctl.calls)

    def test_stop_or_disable_failure_is_reported_not_fatal(self, fake_ctl, capsys):
        fake_ctl.fail = {"disable": "Unit not enabled"}
        call_command("systemd", "install")
        call_command("systemd", "uninstall")
        assert not any(fake_ctl.unit_dir.iterdir())
        assert ("daemon-reload", "") in fake_ctl.calls
        assert "Unit not enabled" in capsys.readouterr().err

    def test_uninstall_failure_names_the_unit(self, fake_ctl):
        call_command("systemd", "install")

        def flaky(name):
            raise OSError("permission denied")

        fake_ctl.uninstall_unit = flaky  # type: ignore[method-assign]
        with pytest.raises(CommandError) as exc_info:
            call_command("systemd", "uninstall")
        message = str(exc_info.value)
        assert "permission denied" in message
        assert str(fake_ctl.unit_dir) in message

    def test_without_systemctl_still_removes(self, make_ctl, capsys):
        ctl = make_ctl(available=False)
        call_command("systemd", "install")
        call_command("systemd", "uninstall")
        assert not any(ctl.unit_dir.iterdir())
        # "uninstall" is FakeCtl's own bookkeeping for file removal, not a
        # systemctl verb (mirrors uninstall_unit, which never shells out).
        assert all(verb == "uninstall" for verb, _ in ctl.calls)
        assert "systemctl not found" in capsys.readouterr().err


@pytest.mark.render
class TestParseContext:
    def test_value_contains_equals(self):
        assert parse_context(["KEY=a=b"]) == {"KEY": "a=b"}

    def test_duplicate_key_last_wins(self):
        assert parse_context(["KEY=a", "KEY=b"]) == {"KEY": "b"}

    def test_empty_value(self):
        assert parse_context(["KEY="]) == {"KEY": ""}

    def test_key_is_stripped(self):
        assert parse_context([" venv =x"]) == {"venv": "x"}


@pytest.mark.django_db
class TestRestart:
    def test_restarts_installed_units_in_order(self, fake_ctl, capsys):
        call_command("systemd", "install")
        fake_ctl.calls.clear()
        call_command("systemd", "restart")
        assert fake_ctl.calls == [("restart", ("web.service", "check.timer"))]
        out = capsys.readouterr().out
        assert "restarted web.service check.timer" in out

    def test_only_installed_units_by_default(self, fake_ctl):
        call_command("systemd", "install")
        fake_ctl.uninstall_unit("check.timer")
        fake_ctl.calls.clear()
        call_command("systemd", "restart")
        assert fake_ctl.calls == [("restart", ("web.service",))]

    def test_explicit_subset(self, fake_ctl):
        call_command("systemd", "restart", "check.timer")
        assert fake_ctl.calls == [("restart", ("check.timer",))]

    def test_explicit_units_are_ordered(self, fake_ctl):
        call_command("systemd", "restart", "check.timer", "web.service")
        assert fake_ctl.calls == [("restart", ("web.service", "check.timer"))]

    def test_duplicate_explicit_units_are_deduped(self, fake_ctl):
        call_command("systemd", "restart", "web.service", "web.service")
        assert fake_ctl.calls == [("restart", ("web.service",))]

    def test_unknown_unit(self, fake_ctl):
        with pytest.raises(CommandError, match="nope.service"):
            call_command("systemd", "restart", "nope.service")
        assert fake_ctl.calls == []

    def test_template_unit_is_not_a_target(self, fake_ctl):
        with pytest.raises(
            CommandError, match="Template units cannot be restarted directly"
        ):
            call_command("systemd", "restart", "app@.target")

    def test_nothing_installed_is_a_noop(self, fake_ctl, capsys):
        call_command("systemd", "restart")
        assert fake_ctl.calls == []
        captured = capsys.readouterr()
        assert "restarted" not in captured.out
        assert "No installed project units" in captured.err

    def test_failure_is_a_command_error(self, fake_ctl):
        fake_ctl.fail = {"restart": "Job for web.service failed"}
        with pytest.raises(CommandError, match="Job for web.service failed"):
            call_command("systemd", "restart", "web.service")

    def test_requires_systemctl(self, make_ctl):
        make_ctl(available=False)
        with pytest.raises(CommandError, match="systemctl"):
            call_command("systemd", "restart")

    def test_socket_and_service_restart_in_one_transaction(self, fake_ctl):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/web.socket", "**/web.service"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "install")
            fake_ctl.calls.clear()
            call_command("systemd", "restart")
        assert fake_ctl.calls == [("restart", ("web.socket", "web.service"))]

    def test_timer_triggered_service_is_left_to_its_timer(self, fake_ctl):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/cleanup.*"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "install")
            fake_ctl.calls.clear()
            call_command("systemd", "restart")
        assert fake_ctl.calls == [("restart", ("cleanup.timer",))]

    def test_timer_triggered_service_can_be_named_explicitly(self, fake_ctl):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/cleanup.*"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "install")
            fake_ctl.calls.clear()
            call_command("systemd", "restart", "cleanup.service")
        assert fake_ctl.calls == [("restart", ("cleanup.service",))]


@pytest.mark.django_db
class TestReload:
    def test_reloads_when_supported_else_restarts(self, fake_ctl, capsys):
        fake_ctl.reloadable.add("web.service")
        call_command("systemd", "install")
        fake_ctl.active.add("web.service")
        fake_ctl.calls.clear()
        call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [
            ("restart", ("check.timer",)),
            ("reload", ("web.service",)),
        ]
        out = capsys.readouterr().out
        assert "reloaded web.service" in out
        assert "restarted check.timer" in out

    def test_reload_restarts_before_reloading(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        fake_ctl.active.add("web.service")
        call_command("systemd", "install")
        fake_ctl.calls.clear()
        call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [
            ("restart", ("check.timer",)),
            ("reload", ("web.service",)),
        ]

    def test_reload_only_no_restart_call(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        fake_ctl.active.add("web.service")
        call_command("systemd", "reload", "web.service")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [("reload", ("web.service",))]

    def test_reload_inactive_service_is_restarted(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        call_command("systemd", "install")
        fake_ctl.calls.clear()
        call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [("restart", ("web.service", "check.timer"))]

    def test_socket_is_kept_when_service_reloads(self, fake_ctl, capsys):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/web.socket", "**/web.service"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "install")
            fake_ctl.calls.clear()
            fake_ctl.reloadable.add("web.service")
            fake_ctl.active.add("web.service")
            call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [("reload", ("web.service",))]
        assert "kept web.socket" in capsys.readouterr().out

    def test_reload_never_queries_non_services(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        fake_ctl.active.update({"web.service", "check.timer"})
        call_command("systemd", "install")
        fake_ctl.calls.clear()
        original_can_reload = fake_ctl.can_reload
        calls: list[str] = []

        def recording_can_reload(unit):
            calls.append(unit)
            return original_can_reload(unit)

        fake_ctl.can_reload = recording_can_reload  # type: ignore[method-assign]
        call_command("systemd", "reload")
        assert calls == ["web.service"]

    def test_nothing_installed_is_a_noop(self, fake_ctl, capsys):
        call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == []
        captured = capsys.readouterr()
        assert "reloaded" not in captured.out
        assert "restarted" not in captured.out
        assert "No installed project units" in captured.err

    def test_unknown_unit(self, fake_ctl):
        with pytest.raises(CommandError, match="nope.service"):
            call_command("systemd", "reload", "nope.service")

    def test_failure_is_a_command_error(self, fake_ctl):
        fake_ctl.reloadable.add("web.service")
        fake_ctl.active.add("web.service")
        fake_ctl.fail = {"reload": "Failed to reload web.service"}
        with pytest.raises(CommandError, match="Failed to reload web.service"):
            call_command("systemd", "reload", "web.service")

    def test_requires_systemctl(self, make_ctl):
        make_ctl(available=False)
        with pytest.raises(CommandError, match="systemctl"):
            call_command("systemd", "reload")

    def test_timer_triggered_service_is_left_to_its_timer(self, fake_ctl):
        with override_settings(
            INSTALLED_APPS=["tests.apps.app3", *settings.INSTALLED_APPS],
            SYSTEMD_TEMPLATES=["**/cleanup.*"],
        ):
            template_engine_config.cache_clear()
            render_engine.cache_clear()
            call_command("systemd", "install")
            fake_ctl.calls.clear()
            call_command("systemd", "reload")
        actions = [c for c in fake_ctl.calls if c[0] in {"restart", "reload"}]
        assert actions == [("restart", ("cleanup.timer",))]
