.. include:: ./refs.rst
.. role:: big

==============
Django Systemd
==============

.. image:: https://img.shields.io/badge/License-MIT-blue.svg
   :target: https://opensource.org/licenses/MIT
   :alt: License: MIT

.. image:: https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json
   :target: https://github.com/astral-sh/ruff
   :alt: Ruff

.. image:: https://badge.fury.io/py/django-systemd.svg
   :target: https://pypi.python.org/pypi/django-systemd/
   :alt: PyPI version

.. image:: https://img.shields.io/pypi/pyversions/django-systemd.svg
   :target: https://pypi.python.org/pypi/django-systemd/
   :alt: PyPI pyversions

.. image:: https://img.shields.io/pypi/djversions/django-systemd.svg
   :target: https://pypi.org/project/django-systemd/
   :alt: PyPI djversions

.. image:: https://img.shields.io/pypi/status/django-systemd.svg
   :target: https://pypi.python.org/pypi/django-systemd
   :alt: PyPI status

.. image:: https://readthedocs.org/projects/django-systemd/badge/?version=latest
   :target: https://django-systemd.readthedocs.io/?badge=latest
   :alt: Documentation Status

.. image:: https://codecov.io/gh/bckohan/django-systemd/branch/main/graph/badge.svg
   :target: https://codecov.io/gh/bckohan/django-systemd
   :alt: Code Cov

.. image:: https://github.com/bckohan/django-systemd/actions/workflows/test.yml/badge.svg?branch=main
   :target: https://github.com/bckohan/django-systemd/actions/workflows/test.yml?query=branch:main
   :alt: Test Status

.. image:: https://github.com/bckohan/django-systemd/actions/workflows/lint.yml/badge.svg?branch=main
   :target: https://github.com/bckohan/django-systemd/actions/workflows/lint.yml?query=branch:main
   :alt: Lint Status

.. image:: https://api.securityscorecards.dev/projects/github.com/bckohan/django-systemd/badge
   :target: https://securityscorecards.dev/viewer/?uri=github.com/bckohan/django-systemd
   :alt: OpenSSF Scorecard


:pypi:`django-systemd` does two independent things for a Django deployment:

1. **Generate systemd unit files.** Apps bundle unit templates in a `systemd/`
   directory. Render them at package time with known settings and commit the
   result, or render them at deploy time from live production settings. This
   allows you to do things like ship services with your reusable Django app
   that downstream users can deploy from their own settings.
2. **Manage the project's units.** :django-admin:`systemd` can list, install,
   update, restart and reload every unit the project defines, without you
   naming them. Units are managed in the system scope by default; privileges
   come from root, an explicit escalation prefix, or
   `polkit <https://github.com/polkit-org/polkit>`_ with the link install
   method. The user scope, with nothing running as root, is still available.

.. code-block:: bash

   django-admin systemd list
   django-admin systemd render ./units --context venv=/srv/app/.venv  # in CI
   django-admin systemd install --enable  # on the host
   django-admin systemd reload  # on the host

|

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   tutorial
   howto
   command
   settings
   reference/index
   changelog
