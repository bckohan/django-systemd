"""
All :pypi:`django-systemd` specific :doc:`django:topics/signals` are defined here.

All signals contain a ``unit`` field that holds the
:class:`~django_systemd.config.ServiceUnit` of the routine in question.
"""

from django.dispatch import Signal

unit_installed = Signal()
"""
Signal sent when a routine is started, but before any commands have been run.

**Signature:**
``(sender, unit, **kwargs)``

:param sender: An instance of the running systemd command.
:type sender: :class:`Systemd Command <django_systemd.management.commands.systemd.Command>`
:param unit: The service unit associated with the signal.
:type unit: :class:`~django_systemd.config.ServiceUnit`
"""
