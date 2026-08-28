## What & why

<!-- What does this change, and what problem does it solve? -->

## Checklist

- [ ] `python manage.py test --settings=artsite.settings_test` passes
- [ ] `ruff check .` and `ruff format .` are clean
- [ ] New behaviour has tests (and new sensitive fields have public-page
      `assertNotContains` guards — see CONTRIBUTING.md)
- [ ] Docs updated if behaviour or setup changed (README / SELF_HOSTING / CLAUDE.md)
