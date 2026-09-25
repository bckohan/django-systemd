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

This package allows tighter integration with systemd in your Django project:

- [ x ] Bundle systemd files with your apps and render them at deployment time.
        - Allows overrides based on app-precedence
        - Uses [django-render-static](https://github.com/bckohan/django-render-static)
- [ x ] Installation, validation 
- [  ] Monitoring and basic management of systemd units from the Django admin

