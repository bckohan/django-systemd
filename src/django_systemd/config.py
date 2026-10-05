import logging
import os
import re
import shlex
import sys
import typing as t
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from render_static.context import resolve_context
from render_static.engine import StaticTemplateEngine

from .defines import InstallMethod, SystemdScope, SystemdUnitType

logger = logging.getLogger(__name__)

unit_types = "|".join(re.escape(typ.value) for typ in SystemdUnitType)

# Names start with a word character so none can be mistaken for an option.
SERVICE_UNIT_REGEX = re.compile(rf"^(?P<name>\w[\w.@-]*)\.(?P<type>{unit_types})$")

# A deterministic sort key for the order units are invoked and reported in.
# Sockets sort before the services they activate, paths and timers sort after.
# Anything not listed sorts last. systemd itself orders the jobs within the
# transaction; this only makes the invocation and its output reproducible.
_RESTART_ORDER: dict[SystemdUnitType, int] = {
    SystemdUnitType.SOCKET: 0,
    SystemdUnitType.SERVICE: 1,
    SystemdUnitType.PATH: 2,
    SystemdUnitType.TIMER: 3,
}


@dataclass
class ServiceUnit:
    """
    A systemd unit that belongs to this project.

    :param name: The unit name without its type suffix (e.g. ``web``).
    :param unit_type: The :class:`~django_systemd.defines.SystemdUnitType`.
    :param path: The template (or rendered file) this unit came from, if known.
    :param instanceable: True if this is a template unit (its name ends in ``@``).
    :param template: The template name this unit was discovered under, as the
        render engine knows it (e.g. ``sub/web.service``).
    """

    name: str
    unit_type: SystemdUnitType
    path: Path | None = None
    instanceable: bool = False
    template: str = ""

    def __post_init__(self) -> None:
        if not self.template:
            self.template = self.filename

    @property
    def filename(self) -> str:
        """The unit file name systemd knows this unit by, e.g. ``web.service``."""
        return f"{self.name}.{self.unit_type.value}"

    @property
    def restart_priority(self) -> int:
        """
        A deterministic sort key for invocation and output order; lower values
        sort first. systemd itself orders the jobs within the transaction.
        """
        return _RESTART_ORDER.get(self.unit_type, len(_RESTART_ORDER))

    @classmethod
    def parse(cls, raw: Path | str) -> "ServiceUnit":
        """
        Build a :class:`ServiceUnit` from a unit file name or path.

        :raises ValueError: if the name is not ``<name>.<unit type>``.
        """
        path = raw if isinstance(raw, Path) else None
        name = raw.name if isinstance(raw, Path) else raw
        if mtch := SERVICE_UNIT_REGEX.match(name):
            return cls(
                name=mtch.groupdict()["name"],
                unit_type=SystemdUnitType(mtch.groupdict()["type"]),
                path=path,
                instanceable=mtch.groupdict()["name"].endswith("@"),
            )
        raise ValueError(f"Unrecognized unit name: '{name}'")


def scope() -> SystemdScope:
    """
    The scope units are managed in, from the ``SYSTEMD_SCOPE`` setting.

    Defaults to :attr:`~django_systemd.defines.SystemdScope.SYSTEM`. The setting
    may be a :class:`~django_systemd.defines.SystemdScope` or its string value.

    :raises django.core.exceptions.ImproperlyConfigured: if the setting is not a
        recognised scope.
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    value = getattr(settings, "SYSTEMD_SCOPE", SystemdScope.SYSTEM)
    try:
        return SystemdScope(value)
    except ValueError as err:
        raise ImproperlyConfigured(
            f"SYSTEMD_SCOPE must be one of "
            f"{', '.join(s.value for s in SystemdScope)}, got {value!r}."
        ) from err


def escalation() -> tuple[str, ...]:
    """
    The privilege escalation prefix from the ``SYSTEMD_ESCALATE`` setting.

    Either a command string such as ``"sudo -n"`` (split with :mod:`shlex`) or a
    sequence of arguments. Empty or ``None`` means no escalation. It is only
    applied to privileged calls in the system scope, and never when already root.

    :raises django.core.exceptions.ImproperlyConfigured: if the setting is
        neither a string nor a sequence of arguments, or a string :mod:`shlex`
        cannot parse.
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    value = getattr(settings, "SYSTEMD_ESCALATE", None)
    if not value:
        return ()
    if isinstance(value, str):
        try:
            return tuple(shlex.split(value))
        except ValueError as err:
            raise ImproperlyConfigured(
                "SYSTEMD_ESCALATE must be a command string such as "
                f'"sudo -n" or a sequence of arguments, got {value!r}.'
            ) from err
    if hasattr(value, "__iter__"):
        return tuple(str(part) for part in value)
    raise ImproperlyConfigured(
        "SYSTEMD_ESCALATE must be a command string such as "
        f'"sudo -n" or a sequence of arguments, got {value!r}.'
    )


def install_method() -> InstallMethod:
    """
    The unit install method, from the ``SYSTEMD_INSTALL_METHOD`` setting.

    Defaults to :attr:`~django_systemd.defines.InstallMethod.COPY`. The setting
    may be an :class:`~django_systemd.defines.InstallMethod` or its string value.

    :raises django.core.exceptions.ImproperlyConfigured: if the setting is not a
        recognised install method.
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    value = getattr(settings, "SYSTEMD_INSTALL_METHOD", InstallMethod.COPY)
    try:
        return InstallMethod(value)
    except ValueError as err:
        raise ImproperlyConfigured(
            f"SYSTEMD_INSTALL_METHOD must be one of "
            f"{', '.join(m.value for m in InstallMethod)}, got {value!r}."
        ) from err


def link_dir() -> Path | None:
    """
    The ``SYSTEMD_LINK_DIR`` setting: where rendered units are kept when the
    install method is :attr:`~django_systemd.defines.InstallMethod.LINK`.

    :return: A :class:`~pathlib.Path`, or ``None`` when unset.
    :raises django.core.exceptions.ImproperlyConfigured: if the setting is a
        relative path.
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    value = getattr(settings, "SYSTEMD_LINK_DIR", None)
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ImproperlyConfigured(
            f"SYSTEMD_LINK_DIR must be an absolute path, got {value!r}."
        )
    return path


def render_dir() -> Path | None:
    """
    The ``SYSTEMD_RENDER_DIR`` setting: the default directory ``render`` writes
    unit files into. A relative path is resolved against the current directory.

    :return: A :class:`~pathlib.Path`, or ``None`` when unset.
    """
    from django.conf import settings

    value = getattr(settings, "SYSTEMD_RENDER_DIR", None)
    return Path(value) if value else None


def source_dir() -> Path | None:
    """
    The ``SYSTEMD_SOURCE_DIR`` setting: the default directory ``install`` reads
    pre-rendered unit files from instead of rendering them. A relative path is
    resolved against the current directory.

    :return: A :class:`~pathlib.Path`, or ``None`` when unset.
    """
    from django.conf import settings

    value = getattr(settings, "SYSTEMD_SOURCE_DIR", None)
    return Path(value) if value else None


@cache
def template_engine_config() -> dict[str, t.Any]:
    """
    Get the configuration for the systemd template rendering engine.

    :return: The configuration dictionary for the rendering engine.
    :rtype: Dict[str, Any]
    """
    from django.conf import settings
    from django_typer.utils import get_usage_script

    engine_config = getattr(
        settings,
        "SYSTEMD_TEMPLATE_ENGINE",
        {
            "ENGINES": [
                {
                    "BACKEND": "render_static.backends.StaticDjangoTemplates",
                    "OPTIONS": {
                        "app_dir": "systemd",
                        "loaders": [
                            "render_static.loaders.StaticAppDirectoriesBatchLoader"
                        ],
                        "builtins": ["render_static.templatetags.render_static"],
                        "autoescape": False,
                    },
                }
            ]
        },
    )
    engine_config.setdefault(
        "context", getattr(settings, "SYSTEMD_TEMPLATE_CONTEXT", {})
    )
    engine_config["context"] = resolve_context(engine_config["context"])
    engine_config["context"].setdefault("settings", settings)
    engine_config["context"].setdefault("venv", Path(sys.prefix))
    engine_config["context"].setdefault("python", Path(sys.executable))
    engine_config["context"].setdefault("django-admin", get_usage_script())
    engine_config.setdefault(
        "templates",
        getattr(
            settings,
            "SYSTEMD_TEMPLATES",
            [f"**/*.{unit_type}" for unit_type in SystemdUnitType],
        ),
    )
    engine_config["context"].setdefault(
        "DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "")
    )
    resolved_scope = scope().value
    existing_scope = engine_config["context"].get("scope")
    if existing_scope is not None and existing_scope != resolved_scope:
        logger.warning(
            "Ignoring scope=%r in SYSTEMD_TEMPLATE_CONTEXT; the scope is set by "
            "SYSTEMD_SCOPE",
            existing_scope,
        )
    engine_config["context"]["scope"] = resolved_scope
    return engine_config


@cache
def render_engine() -> StaticTemplateEngine:
    """
    Get the configured rendering engine for systemd service units.

    :return: Rendering engine that knows how to find and render systemd service unit
        templates.
    :rtype: :class:`~render_static.engine.StaticTemplateEngine`
    """
    return StaticTemplateEngine(template_engine_config())


def project_units() -> list[ServiceUnit]:
    """
    The manifest: every systemd unit template bundled by an installed app.

    Templates are those matching the ``SYSTEMD_TEMPLATES`` patterns. The render
    engine resolves each template name to its highest-precedence app and may
    yield that winner once per app that provides the name, so repeats are
    collapsed here by unit file name. Files that match a pattern but are not
    valid ``<name>.<unit type>`` names are skipped with a warning.

    When two templates produce the same unit file name, the first one
    discovered wins and a warning is logged.

    :return: Units in discovery order, each with ``path`` set to its template
        file and ``template`` set to the name the render engine knows it by.
    """
    from django.template.exceptions import TemplateDoesNotExist

    engine = render_engine()
    seen: dict[str, str] = {}
    units: list[ServiceUnit] = []
    for pattern in template_engine_config()["templates"]:
        try:
            for render in engine.find(pattern):
                origin = Path(render.template.origin.name)
                try:
                    unit = ServiceUnit.parse(origin)
                except ValueError as err:
                    logger.warning("Ignoring %s: %s", origin, err)
                    continue
                template_name = render.template.origin.template_name
                if unit.filename in seen:
                    logger.warning(
                        "Ignoring %s: unit file %s is already provided by template %s",
                        origin,
                        unit.filename,
                        seen[unit.filename],
                    )
                    continue
                unit.template = template_name
                seen[unit.filename] = template_name
                units.append(unit)
        except TemplateDoesNotExist:
            continue
    return units
