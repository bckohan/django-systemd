# django-systemd

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![PyPI version](https://badge.fury.io/py/django-systemd.svg)](https://pypi.python.org/pypi/django-systemd/)
[![PyPI pyversions](https://img.shields.io/pypi/pyversions/django-systemd.svg)](https://pypi.python.org/pypi/django-systemd/)
[![PyPI djversions](https://img.shields.io/pypi/djversions/django-systemd.svg)](https://pypi.org/project/django-systemd/)
[![PyPI status](https://img.shields.io/pypi/status/django-systemd.svg)](https://pypi.python.org/pypi/django-systemd)
[![Documentation Status](https://readthedocs.org/projects/django-systemd/badge/?version=latest)](http://django-systemd.readthedocs.io/?badge=latest/)
[![Code Cov](https://codecov.io/gh/bckohan/django-systemd/branch/main/graph/badge.svg)](https://codecov.io/gh/bckohan/django-systemd)
[![Test Status](https://github.com/bckohan/django-systemd/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/bckohan/django-systemd/actions/workflows/test.yml?query=branch:main)
[![Lint Status](https://github.com/bckohan/django-systemd/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/bckohan/django-systemd/actions/workflows/lint.yml?query=branch:main)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/bckohan/django-systemd/badge)](https://securityscorecards.dev/viewer/?uri=github.com/bckohan/django-systemd)
[![Published on Django Packages](https://img.shields.io/badge/Published%20on-Django%20Packages-0c3c26)](https://djangopackages.org/packages/p/django-typer/)
                        

[django-systemd](https://pypi.org/project/django-systemd) does two independent things for a Django deployment:

1. **Generate systemd unit files.** Apps bundle unit templates in a `systemd/` directory. Render them at package time with known settings and commit the result, or render them at deploy time from live production settings. This allows you to do things like ship services with your reusable Django app that downstream users can deploy from their own settings.
2. **Manage the project's units.** `django-admin systemd` can list, install, update, restart and reload every unit the project defines, without you naming them. Units are managed in the system scope by default; privileges come from root, an explicit escalation prefix, or [polkit](https://github.com/polkit-org/polkit) with the link install method. The user scope, with nothing running as root, is still available.

```bash
django-admin systemd list
django-admin systemd render ./units --context venv=/srv/app/.venv  # in CI
django-admin systemd install --enable  # on the host
django-admin systemd reload  # on the host
```

See the [documentation](https://django-systemd.readthedocs.io) for the how-to and settings.
