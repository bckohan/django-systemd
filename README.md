# django-systemd

This package allows tighter integration with systemd in your Django project:

- [ x ] Bundle systemd files with your apps and render them at deployment time.
        - Allows overrides based on app-precedence
        - Uses [django-render-static](https://github.com/bckohan/django-render-static)
- [ x ] Installation, validation 
- [  ] Monitoring and basic management of systemd units from the Django admin

