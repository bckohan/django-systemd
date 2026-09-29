.. include:: ../refs.rst

.. _defines:

=======
Defines
=======

.. automodule:: django_systemd.defines

Unit Types
==========

.. autoclass:: django_systemd.defines.SystemdUnitType

.. enum-table:: django_systemd.defines.SystemdUnitType
    :columns: name, value, doc
    :headers: name=Member, value=Suffix, doc=Description

Startup Types
=============

.. autoclass:: django_systemd.defines.SystemdStartupType

.. enum-table:: django_systemd.defines.SystemdStartupType
    :columns: name, value, doc
    :headers: name=Member, value=Type=, doc=Description

Restart Types
=============

.. autoclass:: django_systemd.defines.SystemdRestartType

.. enum-table:: django_systemd.defines.SystemdRestartType
    :columns: name, value, doc
    :headers: name=Member, value=Restart=, doc=Description

Scopes
======

.. autoclass:: django_systemd.defines.SystemdScope

.. enum-table:: django_systemd.defines.SystemdScope
    :columns: name, value, doc
    :headers: name=Member, value=Value, doc=Description

Install Methods
================

.. autoclass:: django_systemd.defines.InstallMethod

.. enum-table:: django_systemd.defines.InstallMethod
    :columns: name, value, doc
    :headers: name=Member, value=Value, doc=Description
