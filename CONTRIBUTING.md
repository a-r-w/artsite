# Contributing

Thanks for your interest! Issues and pull requests are welcome — including
"the setup instructions didn't work for me", which counts as a bug.

## Dev setup

Python 3.12. No database server, cloud account, or other services needed:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
./dev.sh        # SQLite + local media; migrates, then runs the dev server
```

`python manage.py seed_demo` loads a few sample pieces to click around
(`--clear` removes them again).

## Before you open a PR

```bash
python manage.py test --settings=artsite.settings_test   # the full suite
ruff check .                                             # lint
ruff format .                                            # formatting
```

CI runs all three on every push and PR. To catch issues at commit time instead:

```bash
pip install pre-commit && pre-commit install
```

Please add tests for new behaviour. Two testing conventions matter more than
the rest:

- **The privacy boundary is test-enforced.** Purchase price/currency, private
  notes, and staff documents must never be reachable from a public page. If you
  add a sensitive field, give it an `assertNotContains` guard on the public
  views (see `test_public_views.py`), and keep the access-control gate matrix in
  `art/tests/test_security.py` in sync when adding `/curate/` URLs.
- **New `Piece` fields touch several places** — model, form, admin, presence
  filters, templates. `CLAUDE.md` has the step-by-step checklist (useful to
  humans too, not just AI assistants).

## Security issues

Please don't open a public issue for a suspected vulnerability — see
[`SECURITY.md`](SECURITY.md).
