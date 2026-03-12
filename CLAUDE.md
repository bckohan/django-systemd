# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

This project uses `just` as a task runner and `uv` for package management.

```bash
# Initial setup
just setup              # Create venv and install pre-commit hooks
just install            # Install all dependencies with extras

# Running tests
just test-all           # Run full test suite with coverage (isolated env)
just test               # Run tests using project venv (faster, for development)
just test tests/test_basics.py::TestClass::test_method  # Run single test

# Code quality
just fix                # Auto-fix formatting, linting, and imports
just check              # Run all static analysis (ruff, mypy, pyright, doc8)
just check-all          # Run all checks including doc link checking (slow)
just check-types        # Run type checkers only

# Documentation
just docs               # Build and open docs
just docs-live          # Serve docs with auto-reload

# Django management
just manage <command>   # Run django-admin commands (uses tests.settings)
```

## Architecture

**Package structure**: Source code in `src/django_systemd/`, tests in `tests/`.

**Core components**:
- `defines.py`: Enum definitions using `enum-properties` for systemd unit types (`SystemdUnitType`), startup types, restart types, and scope (user/system)
- `config.py`: Template engine configuration using `django-render-static` to discover and render systemd unit templates from app `systemd/` directories
- `management/commands/systemd.py`: Django management command built with `django-typer`

**Key dependencies**:
- `django-render-static`: Template discovery and rendering engine for finding systemd units in app directories
- `django-typer`: CLI framework for the `systemd` management command
- `enum-properties`: Extended enums with additional properties (descriptions, paths, etc.)

**Template system**: Apps can bundle systemd unit templates in their `systemd/` subdirectory. The engine uses `StaticAppDirectoriesBatchLoader` to find templates matching `**/*.{service,socket,timer,...}` patterns. Templates have access to `settings`, `venv`, `python`, `django-admin`, and `DJANGO_SETTINGS_MODULE` in their context.

**Configuration settings**:
- `SYSTEMD_TEMPLATE_ENGINE`: Override the template engine configuration
- `SYSTEMD_TEMPLATE_CONTEXT`: Add custom context variables for templates
- `SYSTEMD_TEMPLATES`: Override which template patterns to discover

## Testing

Tests use `pytest` with `pytest-django`. Test settings are in `tests/settings.py` (DJANGO_SETTINGS_MODULE=tests.settings). Test apps in `tests/apps/app1/` and `tests/apps/app2/` contain example systemd unit templates for testing template precedence and rendering.
