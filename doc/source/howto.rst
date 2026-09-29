.. include:: ./refs.rst

======
How-To
======

Bundle units with an app
------------------------

Put unit templates in a ``systemd/`` directory inside any installed app. File names
must be ``<name>.<unit type>``, for example ``web.service`` or ``check.timer``.
Templates are Django templates and receive the context described in
:setting:`SYSTEMD_TEMPLATE_CONTEXT`. When two apps provide the same unit name the
app listed first in ``INSTALLED_APPS`` wins. Which templates are discovered is
controlled by the :setting:`SYSTEMD_TEMPLATES` patterns.

``scope`` is always in the template context, so one template can serve either
scope. In the system scope the unit needs a ``User=`` and hooks into
``multi-user.target``; in the user scope it omits ``User=`` and hooks into
``default.target``:

.. code-block:: ini

    [Unit]
    Description={{ settings.PROJECT_NAME|default:"Django" }} web

    [Service]
    {% if scope == "system" %}User=www-data{% endif %}
    ExecStart={{ python }} -m gunicorn --bind 127.0.0.1:8000 myproject.wsgi
    WorkingDirectory={{ venv }}
    Environment=DJANGO_SETTINGS_MODULE={{ DJANGO_SETTINGS_MODULE }}
    Restart=on-failure

    [Install]
    WantedBy={% if scope == "system" %}multi-user.target{% else %}default.target{% endif %}

.. _authorize:

Authorize the deploy user
--------------------------

By default units are managed in the system scope: they are installed under
``/etc/systemd/system``, start at boot, and run as the ``User=`` they name.
Changing them needs privileges. :pypi:`django-systemd` never guesses how to get
them; pick one of these routes. ``list`` needs none of them: it only reads.

Run the command as root
~~~~~~~~~~~~~~~~~~~~~~~~

The simplest route. Nothing needs configuring; the command reads, writes and
talks to systemd directly.

.. code-block:: bash

    sudo -E /srv/mysite/.venv/bin/python -m django systemd install --enable

``-E`` keeps ``DJANGO_SETTINGS_MODULE`` and the rest of your environment.

An escalation prefix
~~~~~~~~~~~~~~~~~~~~~

Run the command as the deploy user and let it prefix only the privileged calls.
Set :setting:`SYSTEMD_ESCALATE` (or pass ``--escalate``):

.. code-block:: python

    SYSTEMD_ESCALATE = "sudo -n"

The prefixed programs are ``systemctl``, ``install`` and ``rm``, so this sudoers
rule is all the deploy user needs:

.. code-block:: text

    deploy ALL=(root) NOPASSWD: /usr/bin/systemctl, /usr/bin/install, /usr/bin/rm

Templates, rendering and every read-only query still run as the deploy user.
``-n`` makes sudo fail instead of prompting when the rule is missing.

Polkit and the link method
~~~~~~~~~~~~~~~~~~~~~~~~~~~

systemd authorizes ``systemctl`` through polkit, so a rule can let the deploy
user manage units without sudo at all. What polkit cannot grant is writing to
``/etc/systemd/system``, so pair it with the link install method: units are
rendered into a directory the deploy user owns and ``systemctl link`` puts
symlinks in the unit directory.

.. code-block:: python

    SYSTEMD_INSTALL_METHOD = "link"
    SYSTEMD_LINK_DIR = "/srv/mysite/units"

``SYSTEMD_LINK_DIR`` must be an absolute path outside systemd's unit search
path, and on a file system that is mounted at boot: systemd reads the linked
file as root during early boot, so a separately mounted ``/home`` is not
suitable. ``install`` creates it with mode ``0755`` before the umask is
applied, and warns on stderr if it, or a rendered unit file, is world-writable.

Save this as ``/etc/polkit-1/rules.d/50-mysite-deploy.rules``:

.. code-block:: javascript

    polkit.addRule(function(action, subject) {
        if (subject.user == "deploy" &&
            (action.id == "org.freedesktop.systemd1.manage-units" ||
             action.id == "org.freedesktop.systemd1.manage-unit-files" ||
             action.id == "org.freedesktop.systemd1.reload-daemon")) {
            return polkit.Result.YES;
        }
    });

``manage-units`` covers start, stop, restart and reload; ``manage-unit-files``
covers enable, disable and link; ``reload-daemon`` covers ``daemon-reload``. To
restrict the rule to the project's units, test ``action.lookup("unit")`` against
their names. A denial from any of these (stderr containing "Interactive
authentication required" or "Access denied") is reported with a hint pointing
back at this section.

Note that a user who may link unit files can run anything as root through a
unit, so this grants the deploy user root-equivalent power over the machine,
as does the sudoers rule above.

``uninstall`` removes the symlink through ``disable`` and then deletes the
rendered file, but only when it can confirm which link directory it owns: pass
``--link-dir`` (or set :setting:`SYSTEMD_LINK_DIR`) to the same directory the
unit was installed with. A unit linked from elsewhere, for example one supplied
with ``install --source``, is left in place with a notice naming it. Trying to
link over a unit file that a previous ``install`` copied into place fails with
a message telling you to run ``systemd uninstall`` first; systemd itself
refuses to replace a regular file with a link.

.. _user-scope:

Run in the user scope instead
------------------------------

With ``--scope user`` or ``SYSTEMD_SCOPE = "user"`` everything uses
``systemctl --user`` and installs into ``$XDG_CONFIG_HOME/systemd/user``
(``~/.config/systemd/user`` by default). No privileges are needed and the units
run as the deploying user.

- Talking to the user manager from a non-login session, for example over SSH as a
  deploy user, requires lingering to be enabled once for that user, or
  ``XDG_RUNTIME_DIR`` to be set. Failures show up as ``Failed to connect to bus``
  in the command's error output.

  .. code-block:: bash

      loginctl enable-linger "$USER"

- Enabling lingering also keeps your services running after you log out.

All projects deployed as the same user share one unit directory. Prefix your
unit names per project (for example ``myproject-web.service``) so they do not
collide with another project's units in the same unit directory.

.. note::

    **Developing without systemd**

    ``render`` and ``list`` work on any machine, with or without systemd.
    ``install --method copy`` (the default) still copies unit files into the
    unit directory; if ``systemctl`` is not found it skips the rest and prints
    "systemctl not found; skipped daemon-reload and enable." ``install
    --method link`` needs ``systemctl link`` to place the symlink, so without
    systemctl it refuses outright with "--method link needs systemctl, which
    is not available on this system," before rendering anything or creating
    the link directory. ``uninstall`` behaves like the copy method, printing
    "systemctl not found; skipped stop, disable and daemon-reload." ``restart``
    and ``reload`` need systemctl and fail outright with "systemctl is not
    available on this system."

See which units belong to the project
--------------------------------------

.. code-block:: bash

    django-admin systemd list

Each row shows the unit, whether it is installed in the unit directory,
whether it is enabled and active, and which template it comes from. State columns
show ``-`` for units that are not installed, for template units (``name@.type``),
and when ``systemctl`` is not available.

Render units at package time
----------------------------

Rendering ahead of time makes the unit files reviewable in version control, and a
deploy no longer depends on rendering succeeding on the host. Rendering also bakes
in the interpreter and virtual environment paths of the machine doing the
rendering, so when you render in CI for a different host, override them:

.. code-block:: bash

    django-admin systemd render ./units \
        --context venv=/srv/app/.venv \
        --context python=/srv/app/.venv/bin/python

Commit ``./units`` and install them on the host without rendering again:

.. code-block:: bash

    django-admin systemd install --source ./units --enable

``install`` is still a Django management command even with ``--source``, so
Django settings must still load successfully on the host.

Render and install at deploy time
---------------------------------

``--enable`` makes the units start with the manager at login or boot; it does
not start them now. ``restart`` starts units that are not running and restarts
the ones that are.

On the host, with production settings active:

.. code-block:: bash

    django-admin systemd install --enable
    django-admin systemd restart

Running ``install`` again replaces the installed unit files, so it doubles as the
update step. The :data:`~django_systemd.signals.unit_installed` signal fires for
each unit as it is installed.

Restart or reload after a deploy
--------------------------------

``restart`` restarts every installed project unit in a single ``systemctl``
transaction, so systemd orders sockets, services and timers itself. ``reload``
reloads services that are running and define ``ExecReload=`` and restarts
everything else; a socket whose service is reloaded in place is left listening.
Both accept an explicit list of unit file names.

Two systemd behaviours to know about:

- systemd refuses to restart a socket on its own while its service is running.
  Restart the pair together (the default, with no names given) or name both.
- The socket-to-service pairing assumes the default ``<name>.service``. A socket
  that sets ``Service=`` to a different unit is restarted like any other unit.
- A service triggered by a timer or path unit of the same name (typically a
  oneshot job) is not restarted by default, because that would run the job.
  Name it explicitly to restart it.
- None of this depends on scope: ``restart`` and ``reload`` behave the same way
  whether the units are managed in the system or the user scope.

Restart units from a deployment routine
---------------------------------------

With :pypi:`django-routines` you can make unit management part of a deploy routine
without naming the units anywhere in the routine. Add to your settings:

.. code-block:: python

    from django_routines import command, routine

    routine("deploy", "Deploy the site.")
    command("deploy", "migrate")
    command("deploy", "collectstatic", "--noinput")
    command("deploy", "systemd", "install", "--enable")
    command("deploy", "systemd", "reload")

Then ``django-admin routine deploy`` re-installs every project unit (including
any new ones) and reloads or restarts each installed one. Use ``systemd
restart`` instead of ``reload`` when you always want a full restart.

Remove the units
----------------

.. code-block:: bash

    django-admin systemd uninstall

This stops and disables each installed unit, removes its file, and reloads the
daemon. It is safe to run when nothing is installed.

Units whose templates are removed from the project are not uninstalled
automatically: ``uninstall`` only acts on units the current manifest still
knows about. Run ``uninstall`` before removing a template from your project,
or delete the installed unit file by hand.
