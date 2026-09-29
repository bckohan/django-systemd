.. include:: ./refs.rst

==========
Change Log
==========

v0.2.0 (2026-09-29)
===================

* System scope is now the default: units are managed with the system manager,
  installed under ``/etc/systemd/system``, and run as the ``User=`` they name.
  The user scope remains available through :setting:`SYSTEMD_SCOPE` or
  ``--scope user``.
* Privilege escalation is explicit and never guessed. :setting:`SYSTEMD_ESCALATE`
  (or ``--escalate``) configures a prefix such as ``sudo -n`` for privileged
  calls in the system scope; it is never applied to ``list`` or to any
  read-only query, and never when already running as root.
* Added the link install method
  (:attr:`~django_systemd.defines.InstallMethod.LINK`,
  :setting:`SYSTEMD_INSTALL_METHOD`, :setting:`SYSTEMD_LINK_DIR`): rendered
  units are kept in a directory the deploying user owns and ``systemctl link``
  places the symlink, so only polkit authorization is needed, never file
  system privileges.
* ``scope`` is now always in the template context, so one unit template can
  serve both scopes.
* Every ``systemctl`` call now passes ``--no-ask-password`` so a missing
  authorization fails instead of prompting.

v0.1.0 (2026-09-28)
===================

* Initial Release.
* Requires Python 3.11 or later and Django 5.2 or later.
* Apps bundle systemd unit templates in a ``systemd/`` directory. The set of
  templates matching :setting:`SYSTEMD_TEMPLATES` forms the project's unit
  manifest (:func:`~django_systemd.config.project_units`).
* The ``systemd`` management command provides ``list``, ``render``,
  ``install``, ``uninstall``, ``restart`` and ``reload``, all driven by the
  manifest so deployments never name units.
* ``render --context`` and ``install --source`` support rendering at package
  time and installing the pre-rendered units on the host.
* Units are managed in the user scope only, through ``systemctl --user``.
  Nothing runs as root.
* :data:`~django_systemd.signals.unit_installed` is sent with ``unit`` and
  ``destination`` for each installed unit.
* Unit files are rendered with autoescaping off, since they are not HTML.
