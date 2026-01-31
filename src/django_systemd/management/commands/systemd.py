"""
The systemd command is a Django_ :doc:`management command <django:ref/django-admin>`
that renders, installs, removes and manages systemd units.

.. typer:: django_systemd.management.commands.systemd.Command:typer_app
    :prog: django-admin systemd
    :width: 80
    :convert-png: latex
"""

from functools import cached_property

from django_typer.management import TyperCommand, command, initialize


class Command(TyperCommand):
    @cached_property
    def render_engine(self):
        from django_systemd.config import render_engine

        return render_engine()

    @initialize()
    def init(self):
        self.units = self.render_engine.search("")

    @command()
    def list(self):
        """List available systemd units."""
        self.print_commands()

    # @command()
    # def validate(self):
    #     """Validate the systemd service file for this Django project."""
    #     from django_systemd.utils import validate_service_file

    #     validate_service_file()

    # @command()
    # def install(self):
    #     """Install the systemd service file for this Django project."""
    #     from django_systemd.utils import install_service_file

    #     install_service_file()

    # @command()
    # def uninstall(self):
    #     """Uninstall the systemd service file for this Django project."""
    #     from django_systemd.utils import uninstall_service_file

    #     uninstall_service_file()

    # @command()
    # def status(self):
    #     """Check the status of the systemd service for this Django project."""
    #     from django_systemd.utils import check_service_status

    #     check_service_status()

    # @command()
    # def restart(self):
    #     """Restart the systemd service for this Django project."""
    #     from django_systemd.utils import restart_service

    #     restart_service()

    # @command()
    # def start(self):
    #     """Start the systemd service for this Django project."""
    #     from django_systemd.utils import start_service

    #     start_service()

    # @command()
    # def stop(self):
    #     """Stop the systemd service for this Django project."""
    #     from django_systemd.utils import stop_service

    #     stop_service()

    # @command()
    # def enable(self):
    #     """Enable the systemd service for this Django project."""
    #     from django_systemd.utils import enable_service

    #     enable_service()

    # @command()
    # def disable(self):
    #     """Disable the systemd service for this Django project."""
    #     from django_systemd.utils import disable_service

    #     disable_service()

    # @command()
    # def logs(self, lines: int = 100):
    #     """View the logs of the systemd service for this Django project."""
    #     from django_systemd.utils import view_service_logs

    #     view_service_logs(lines=lines)

    # @command()
    # def status(self):
    #     """Check the verbose status of the systemd service for this Django project."""
    #     from django_systemd.utils import check_service_status_verbose

    #     check_service_status_verbose()
