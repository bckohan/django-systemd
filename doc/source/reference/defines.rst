.. include:: ../refs.rst

.. _defines:

=======
Defines
=======

.. automodule:: django_systemd.defines

Each enumeration's ``value`` is the literal string systemd uses in unit files.
Members convert to that string with :func:`str`, and
:meth:`~django_systemd.defines.SystemdEnum.from_literal` looks a member up by it.

.. autoclass:: django_systemd.defines.SystemdEnum
    :members: from_literal

.. autoclass:: django_systemd.defines.SystemdValue
    :members:

Unit Types
==========

.. autoclass:: django_systemd.defines.SystemdUnitType

.. enum-table:: django_systemd.defines.SystemdUnitType
    :columns: name, value, description
    :headers: name=Member, value=Suffix, description=Description

Startup Types
=============

.. autoclass:: django_systemd.defines.SystemdStartupType

.. enum-table:: django_systemd.defines.SystemdStartupType
    :columns: name, value, description
    :headers: name=Member, value=Type=, description=Description

Restart Types
=============

.. autoclass:: django_systemd.defines.SystemdRestartType

.. enum-table:: django_systemd.defines.SystemdRestartType
    :columns: name, value, description
    :headers: name=Member, value=Restart=, description=Description
