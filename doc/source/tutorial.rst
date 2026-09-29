.. include:: ./refs.rst

.. _tutorial:

========
Tutorial
========

This tutorial deploys a Django site as two systemd units that are generated on
the host from the site's live settings, installed, enabled and started, all with
:pypi:`django-systemd`:

- ``mysite-web.service`` serves the site with gunicorn and uvicorn workers.
- ``mysite-dbcheck.timer`` runs a database health check every five minutes by
  starting ``mysite-dbcheck.service``.

By the end you will have a deploy step that is the same two commands every time,
whether it is the first deploy or the fiftieth.

Prerequisites
=============

- A Linux host with systemd, and a non-root user to deploy as. This tutorial
  uses a user named ``deploy``.
- A Django project called ``mysite`` checked out at ``/srv/mysite`` with a
  virtual environment at ``/srv/mysite/.venv``. Any project works; substitute
  your own names.
- Python 3.11 or later.

Units are managed in the system scope by default: they are installed under
``/etc/systemd/system``, start at boot, and run as the ``User=`` they name
rather than as ``deploy``. This tutorial creates a dedicated ``mysite`` service
user for the units to run as, and gives the ``deploy`` user just enough
privilege, through a sudoers rule, to install and manage them. See
:ref:`authorize` for the other two routes (running the command as root, or
polkit with the link install method) if this one does not fit your host.

Create the service user
========================

The units should run as a user of their own, not as ``deploy`` and not as
root. Create a system account with no login shell:

.. code-block:: bash

    sudo useradd --system --home /srv/mysite --shell /usr/sbin/nologin mysite

Give that user read access to the checkout without changing who owns it:

.. code-block:: bash

    sudo chgrp -R mysite /srv/mysite
    sudo chmod -R g+rX /srv/mysite

The site also writes to some of that checkout: uploads go to a ``media/``
directory, and the default ``db.sqlite3`` database is a file SQLite writes to
directly. Grant the ``mysite`` group write access to those paths, and, since
SQLite also creates a journal file next to the database, to ``BASE_DIR``
itself:

.. code-block:: bash

    sudo chgrp mysite /srv/mysite /srv/mysite/media /srv/mysite/db.sqlite3
    sudo chmod g+w /srv/mysite /srv/mysite/media /srv/mysite/db.sqlite3

A real deployment usually points ``DATABASES`` at a database server instead of
SQLite, which does not need this.

Install the packages
====================

Activate the virtual environment and install :pypi:`django-systemd` along with
the server that the web service will run:

.. code-block:: bash

    cd /srv/mysite
    source .venv/bin/activate
    export DJANGO_SETTINGS_MODULE=mysite.settings
    pip install django-systemd gunicorn uvicorn-worker

Every ``django-admin``/``python -m django`` command in the rest of this
tutorial needs ``DJANGO_SETTINGS_MODULE`` set; a deploy script or routine
that runs them non-interactively should set it too, rather than rely on an
activated shell.

Add ``django_systemd`` to ``INSTALLED_APPS``:

.. code-block:: python

    INSTALLED_APPS = [
        ...,
        "django_systemd",
    ]

In your production settings, authorize the ``deploy`` user to run the
privileged parts of the ``systemd`` command without a password:

.. code-block:: python

    SYSTEMD_ESCALATE = "sudo -n"

and add the matching sudoers rule (for example in
``/etc/sudoers.d/mysite-deploy``):

.. code-block:: text

    deploy ALL=(root) NOPASSWD: /usr/bin/systemctl, /usr/bin/install, /usr/bin/rm

``-n`` makes sudo fail instead of prompting when the rule is missing, and the
prefix is only ever applied to the ``systemctl``, ``install`` and ``rm`` calls
that change state; reads such as ``systemd list`` still run as ``deploy``
directly.

Create an app for the unit templates
====================================

Unit templates live in a ``systemd/`` directory inside an installed app.
Deployment files do not belong to any one feature app, so give them their own:

.. code-block:: bash

    python -m django startapp deploy
    mkdir deploy/systemd

Add ``deploy`` to ``INSTALLED_APPS`` too. The app needs nothing else; the empty
``models.py`` and friends that ``startapp`` created can stay or go.

Write the web service
=====================

Create ``deploy/systemd/mysite-web.service``:

.. code-block:: ini

    [Unit]
    Description=mysite web
    After=network.target

    [Service]
    Type=simple
    User=mysite
    Group=mysite
    WorkingDirectory={{ settings.BASE_DIR }}
    Environment=DJANGO_SETTINGS_MODULE={{ DJANGO_SETTINGS_MODULE }}
    ExecStart={{ venv }}/bin/gunicorn mysite.asgi:application \
        --worker-class uvicorn_worker.UvicornWorker \
        --workers {{ settings.GUNICORN_WORKERS|default:2 }} \
        --bind 127.0.0.1:8000
    ExecReload=/bin/kill -s HUP $MAINPID
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target

This is a Django template. The values in double braces come from the
:setting:`SYSTEMD_TEMPLATE_CONTEXT`, which always includes:

- ``settings``, the live Django settings. ``BASE_DIR`` is the project directory
  that ``startproject`` defines, and ``GUNICORN_WORKERS`` is a setting of your
  own that this template falls back to ``2`` for.
- ``venv`` and ``python``, the virtual environment and interpreter that render
  the template. At deploy time those are the ones the service should run under.
- ``DJANGO_SETTINGS_MODULE``, so the service uses the same settings the deploy
  did.

Three things about this unit are specific to the system scope:

- ``User=mysite`` and ``Group=mysite``. Without a ``User=``, a system unit runs
  as root; naming the service user keeps it from doing so.
- ``WantedBy=multi-user.target``, the system manager's normal boot target,
  rather than the user manager's ``default.target``.
- The service binds to a loopback port rather than a socket under a user's
  runtime directory, so a reverse proxy running as another user can still
  reach it at ``http://127.0.0.1:8000``.

``ExecReload`` matters later. It lets gunicorn reload its workers in place when
you deploy new code, without dropping connections.

Write the database check
========================

The check itself is an ordinary management command run by a oneshot service.
Create ``deploy/systemd/mysite-dbcheck.service``:

.. code-block:: ini

    [Unit]
    Description=mysite database health check

    [Service]
    Type=oneshot
    User=mysite
    Group=mysite
    WorkingDirectory={{ settings.BASE_DIR }}
    Environment=DJANGO_SETTINGS_MODULE={{ DJANGO_SETTINGS_MODULE }}
    ExecStart={{ python }} -m django check --database default

``python -m django`` is ``django-admin``. Running it from the project directory
with the settings module set is all a management command needs. This service
has no ``[Install]`` section on purpose: it should only ever run when the timer
starts it, so there is nothing to enable.

Now the timer, ``deploy/systemd/mysite-dbcheck.timer``:

.. code-block:: ini

    [Unit]
    Description=Run the mysite database health check every five minutes

    [Timer]
    OnCalendar=*:0/5
    Persistent=true

    [Install]
    WantedBy=timers.target

A timer starts the service with the same name, so ``mysite-dbcheck.timer``
starts ``mysite-dbcheck.service``. ``Persistent=true`` runs the check on the
next start if the machine was off when one was due. ``WantedBy=timers.target``
is the system manager's target for timers, the counterpart of
``multi-user.target`` for services.

See what the project defines
============================

The three files are now part of the project's unit manifest. From the project
directory:

.. code-block:: bash

    python -m django systemd list

.. code-block:: text

    UNIT                     INSTALLED  ENABLED  ACTIVE   SOURCE
    mysite-web.service       no         -        -        /srv/mysite/deploy/systemd/mysite-web.service
    mysite-dbcheck.service   no         -        -        /srv/mysite/deploy/systemd/mysite-dbcheck.service
    mysite-dbcheck.timer     no         -        -        /srv/mysite/deploy/systemd/mysite-dbcheck.timer

Nothing is installed yet, so the state columns show ``-``. This command never
escalates and works anywhere, including a development machine without
systemd, so it is a good first check that the templates are found.

Render the units
================

Before installing anything, render the templates to a scratch directory and
read the output:

.. code-block:: bash

    python -m django systemd render /tmp/units
    cat /tmp/units/mysite-web.service

.. code-block:: ini

    [Unit]
    Description=mysite web
    After=network.target

    [Service]
    Type=simple
    User=mysite
    Group=mysite
    WorkingDirectory=/srv/mysite
    Environment=DJANGO_SETTINGS_MODULE=mysite.settings
    ExecStart=/srv/mysite/.venv/bin/gunicorn mysite.asgi:application \
        --worker-class uvicorn_worker.UvicornWorker \
        --workers 2 \
        --bind 127.0.0.1:8000
    ExecReload=/bin/kill -s HUP $MAINPID
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target

Every placeholder has been replaced with a value from this host: the
environment path, the settings module, the worker count. That is the point of
rendering at deploy time. The unit file reflects the machine it will run on, and
the template in version control stays free of host details.

``render`` never talks to systemd and never escalates, so it is safe to run as
often as you like. The scratch copy can be deleted; ``install`` renders its own.

Install, enable and start
==========================

Install the units and enable them, as the ``deploy`` user with
:setting:`SYSTEMD_ESCALATE` set as above:

.. code-block:: bash

    python -m django systemd install --enable

.. code-block:: text

    /etc/systemd/system/mysite-web.service
    /etc/systemd/system/mysite-dbcheck.service
    /etc/systemd/system/mysite-dbcheck.timer

``install`` renders the templates, then, since ``deploy`` is not root, runs
``sudo -n install`` to copy each one into ``/etc/systemd/system``, ``sudo -n
systemctl daemon-reload`` to make the system manager see them, and ``sudo -n
systemctl enable`` for each. The ``sudo -n`` prefix is transparent: nothing
about running the command changes except that it now succeeds without a
password prompt, because of the sudoers rule from earlier. Enabling makes the
units start at boot. It does not start them now, so do that:

.. code-block:: bash

    python -m django systemd restart

.. code-block:: text

    restarted mysite-web.service mysite-dbcheck.timer

``restart`` acts on the project's installed units in a single ``systemctl``
call, and it starts anything that is not running. Notice what it did not touch:
``mysite-dbcheck.service``. A service that a timer of the same name triggers is
left to its timer by default, because restarting it would run the check right
now, and on every future deploy. The timer is what gets started.

Check the result:

.. code-block:: bash

    python -m django systemd list

.. code-block:: text

    UNIT                     INSTALLED  ENABLED  ACTIVE   SOURCE
    mysite-web.service       yes        yes      yes      /srv/mysite/deploy/systemd/mysite-web.service
    mysite-dbcheck.service   yes        yes      no       /srv/mysite/deploy/systemd/mysite-dbcheck.service
    mysite-dbcheck.timer     yes        yes      yes      /srv/mysite/deploy/systemd/mysite-dbcheck.timer

The web service and the timer are active. The check service is not, which is
correct: a oneshot service is only active for the moment it runs. Its
``ENABLED`` column reads ``yes`` because systemd reports units without an
``[Install]`` section as ``static``, meaning they are started by something else.

The site is up on port 8000:

.. code-block:: bash

    curl -I http://127.0.0.1:8000/

Everything else about the running units is ordinary systemd. To see the
check's output after its first run, list the running timers, or watch the web
service's logs:

.. code-block:: bash

    systemctl list-timers
    journalctl -u mysite-dbcheck.service
    journalctl -u mysite-web.service -f

Reading system logs as ``deploy`` (rather than as root) needs membership in
the ``systemd-journal`` group, which takes a new login to pick up:

.. code-block:: bash

    sudo usermod -aG systemd-journal deploy

Deploy a change
===============

Deploying again is the same two commands. Suppose you pull new code:

.. code-block:: bash

    cd /srv/mysite
    git pull
    source .venv/bin/activate
    pip install -r requirements.txt
    python -m django migrate
    python -m django systemd install --enable
    python -m django systemd reload

.. code-block:: text

    restarted mysite-dbcheck.timer
    reloaded mysite-web.service

``install`` re-renders every unit and replaces the installed copies. ``reload``
then looks at each installed unit:

- ``mysite-web.service`` defines ``ExecReload`` and is running, so it is
  reloaded in place. Gunicorn starts new workers with the new code and retires
  the old ones without dropping connections.
- ``mysite-dbcheck.timer`` cannot be reloaded, so it is restarted, which picks
  up any change to its schedule.
- ``mysite-dbcheck.service`` is skipped, as before.

A reload signals the running process; it does not start a new one. So a change
to the unit file itself, such as raising ``GUNICORN_WORKERS`` in the settings and
re-rendering, only takes effect on a full restart:

.. code-block:: bash

    python -m django systemd install --enable
    python -m django systemd restart

Use ``reload`` for code deploys and ``restart`` when the units themselves
change. Either way you never name the units; the manifest decides.

Make it a routine
=================

The deploy sequence above is a good fit for :pypi:`django-routines`. With the
routine below in your settings, the whole deploy becomes
``python -m django routine deploy``:

.. code-block:: python

    from django_routines import command, routine

    routine("deploy", "Deploy mysite.")
    command("deploy", "migrate")
    command("deploy", "collectstatic", "--noinput")
    command("deploy", "systemd", "install", "--enable")
    command("deploy", "systemd", "reload")

The :doc:`howto` covers this and the other ways to use the command, including
rendering the units in CI and committing them instead of rendering on the host.

Remove everything
=================

To take the site down and remove its units:

.. code-block:: bash

    python -m django systemd uninstall

.. code-block:: text

    removed mysite-web.service
    removed mysite-dbcheck.service
    removed mysite-dbcheck.timer

``uninstall`` stops and disables each installed unit, deletes the unit files and
reloads the system manager, all through the same ``sudo -n`` prefix. It is safe
to run when nothing is installed. The templates in ``deploy/systemd/`` are
untouched, so ``install`` brings everything back.
