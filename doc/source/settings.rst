.. include:: ./refs.rst

========
Settings
========

``SYSTEMD_TEMPLATE_ENGINE``
---------------------------

.. setting:: SYSTEMD_TEMPLATE_ENGINE

The :setting:`SYSTEMD_TEMPLATE_ENGINE` setting defines the configuration for the
template engine used to render systemd unit files. It follows the same structure as the
:pypi:`django-render-static` setting :setting:`STATIC_TEMPLATES` (itself a superset of the Django_
setting :setting:`TEMPLATES`) but provides defaults tailored for systemd unit file rendering.

By default it will load templates from app directories named ``systemd``. It follows the
same precedence rules as Django's template engine configuration - with higher precedence
apps overriding templates of the same name in lower precedence apps.

By default it will find templates that have recognized systemd unit file extensions.

A number of convenient environment context variables are added to the context. See
:setting:`SYSTEMD_TEMPLATE_CONTEXT` for the list.

.. tip::

    If you want to tweak the recognized template names or add additional context variables, instead
    of overriding the entire setting, consider using the :setting:`SYSTEMD_TEMPLATES` and
    :setting:`SYSTEMD_TEMPLATE_CONTEXT` settings

Unit files are not HTML, so autoescaping is off by default; if you supply your
own engine configuration, set it off too.

Default:

.. code-block:: python

    {
        "ENGINES": [{
            "BACKEND": "render_static.backends.StaticDjangoTemplates",
            "OPTIONS": {
                "app_dir": "systemd",
                "builtins": [
                    "render_static.templatetags.render_static"
                ],
                "loaders": [
                    "render_static.loaders.StaticAppDirectoriesBatchLoader"
                ],
                "autoescape": False
            }
        }],
        "context": {
            "DJANGO_SETTINGS_MODULE": "...",
            "django-admin": "<manage script name or path if not on PATH>",
            "python": "<python interpreter path>",
            "settings": "<django.conf.settings>",
            "venv": "<virtual environment path>"},
        "templates": [
            "**/*.service", "**/*.socket", "**/*.target", "**/*.timer", "**/*.path",
            "**/*.mount", "**/*.automount", "**/*.swap", "**/*.device", "**/*.scope",
            "**/*.snapshot", "**/*.slice"
        ]
    }


``SYSTEMD_TEMPLATES``
---------------------

.. setting:: SYSTEMD_TEMPLATES

The :setting:`SYSTEMD_TEMPLATES` setting defines a list of the :func:`~glob.glob` patterns used to
identify systemd unit file templates within the template engine. By default it will recognize any
file with a standard systemd unit file extension. The :func:`~glob.glob` patterns are ``recursive``.

Default:

    .. code-block:: python

        [
            "**/*.service", "**/*.socket", "**/*.target", "**/*.timer", "**/*.path",
            "**/*.mount", "**/*.automount", "**/*.swap", "**/*.device", "**/*.scope",
            "**/*.snapshot", "**/*.slice"
        ]


``SYSTEMD_TEMPLATE_CONTEXT``
----------------------------

.. setting:: SYSTEMD_TEMPLATE_CONTEXT

Add additional context variables to the rendering context. Provided contexts do not need to be
dictionaries, but can be provided from :ref:`multiple sources <django-render-static:context>`.

**These values will always be added to the context even if you provide your own context.**

- ``DJANGO_SETTINGS_MODULE``: The value of the :envvar:`DJANGO_SETTINGS_MODULE` environment
  variable.
- ``django-admin``: The name or path of the Django
  :doc:`management script <django:ref/django-admin>`.
- ``python``: The path to the Python :py:data:`interpreter <python:sys.executable>`.
- ``scope``: The :class:`~django_systemd.defines.SystemdScope` units are managed in, as a
  string.
- ``settings``: The Django :doc:`settings module <django:ref/settings>`.
- ``venv``: The path to the active python :py:data:`environment <python:sys.prefix>`.


``SYSTEMD_SCOPE``
------------------

.. setting:: SYSTEMD_SCOPE

Which manager owns the project's units: ``"system"`` (the default) or ``"user"``.
See :class:`~django_systemd.defines.SystemdScope`. ``--scope`` on the command
overrides it for one run. The value is also available to templates as ``scope``.
Passing ``--context scope=...`` is refused; use ``--scope`` instead. A ``scope``
key in :setting:`SYSTEMD_TEMPLATE_CONTEXT` is likewise ignored, with a logged
warning, in favor of the resolved scope.

Default: ``"system"``


``SYSTEMD_ESCALATE``
---------------------

.. setting:: SYSTEMD_ESCALATE

A command prefix for privileged calls in the system scope, either a string such
as ``"sudo -n"`` or a list of arguments. It is applied to ``systemctl`` calls
that change state and to installing or removing unit files, never to read-only
queries such as ``list``, and never when already running as root. Use a
non-interactive form (``sudo -n``, ``doas -n``, ``run0``) so a missing
authorization fails instead of prompting. See :ref:`authorize`.

Default: ``None`` (no escalation)


``SYSTEMD_INSTALL_METHOD``
---------------------------

.. setting:: SYSTEMD_INSTALL_METHOD

How ``install`` places units: ``"copy"`` (the default) copies rendered files into
the unit directory; ``"link"`` keeps them in :setting:`SYSTEMD_LINK_DIR` and has
``systemctl link`` put symlinks in the unit directory, which needs no file
system privileges. See :class:`~django_systemd.defines.InstallMethod`.

Default: ``"copy"``


``SYSTEMD_LINK_DIR``
---------------------

.. setting:: SYSTEMD_LINK_DIR

An absolute directory, writable by the deploying user, where rendered units are
kept when :setting:`SYSTEMD_INSTALL_METHOD` is ``"link"``. Required for linking
unless ``install --source`` supplies pre-rendered files, which are then linked
in place. The directory should be outside systemd's unit search path and on a
file system mounted at boot, since systemd reads the linked files as root during
early boot; ``install`` creates it with mode ``0755`` before the umask is
applied, if it does not exist.

Default: ``None``


``SYSTEMD_RENDER_DIR``
-----------------------

.. setting:: SYSTEMD_RENDER_DIR

The directory ``render`` writes unit files into when no output directory is
passed on the command line. A relative path is resolved against the current
directory. An explicit ``render`` argument always takes precedence.

Default: ``None`` (the current directory)


``SYSTEMD_SOURCE_DIR``
-----------------------

.. setting:: SYSTEMD_SOURCE_DIR

A directory of pre-rendered unit files, such as one written by ``render`` and
committed to version control, that ``install`` uses instead of rendering units
when ``--source`` is not passed. A relative path is resolved against the current
directory. ``install --source`` takes precedence, and ``--context`` or
``--link-dir`` are errors while either is in effect, since nothing is rendered.
See :setting:`SYSTEMD_RENDER_DIR` for the matching ``render`` default.

Default: ``None`` (render units at install time)
