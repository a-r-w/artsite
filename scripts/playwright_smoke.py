#!/usr/bin/env python3
"""Read-only Playwright smoke test of the /curate/ admin.

Safe to run anytime — it only logs in and navigates (no creates/edits/deletes,
no GCS uploads), so it never mutates the collection. Use it as the template
for richer tests: copy it, and if you add write actions, make them
self-cleaning (create -> assert -> delete the same throwaway record).

Prereqs:
  1. Dev server running:      ./dev.sh
  2. Playwright installed:    pip install playwright && playwright install chromium

Run:
  python3 scripts/playwright_smoke.py

Env overrides: BASE_URL, CURATE_USER, CURATE_PASS, SHOTS_DIR, PLAYWRIGHT_BROWSERS_PATH
"""

import os
import sys

# Some environments set PLAYWRIGHT_BROWSERS_PATH=0 (an unwritable path).
# Default to a writable cache before importing playwright.
if os.environ.get('PLAYWRIGHT_BROWSERS_PATH') in ('', '0'):
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = os.path.expanduser('~/.cache/ms-playwright')

from playwright.sync_api import expect, sync_playwright  # noqa: E402

BASE = os.environ.get('BASE_URL', 'http://127.0.0.1:8000')
USER = os.environ.get('CURATE_USER', 'admin')
PASS = os.environ.get('CURATE_PASS', 'admin')
SHOTS = os.environ.get('SHOTS_DIR', '/tmp/curate_shots')
os.makedirs(SHOTS, exist_ok=True)

results = []


def ok(m):
    results.append(('PASS', m))
    print('PASS:', m, flush=True)


def info(m):
    results.append(('INFO', m))
    print('INFO:', m, flush=True)


def fail(m):
    results.append(('FAIL', m))
    print('FAIL:', m, flush=True)


errors = []

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_context(viewport={'width': 1366, 'height': 950}).new_page()
    page.on('console', lambda m: errors.append(f'{m.type}: {m.text}') if m.type == 'error' else None)
    page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))

    def shot(name):
        page.screenshot(path=f'{SHOTS}/{name}.png', full_page=True)

    # Login
    page.goto(f'{BASE}/curate/', wait_until='domcontentloaded')
    if '/login' in page.url:
        ok('Unauthenticated /curate/ redirects to login')
    shot('smoke_01_login')
    page.fill("input[name='username']", USER)
    page.fill("input[name='password']", PASS)
    page.click('button[type=submit]')
    page.wait_for_load_state('domcontentloaded')
    if '/curate' in page.url and '/login' not in page.url:
        ok(f'Logged in as {USER!r}')
    else:
        fail(f'Login failed, url={page.url}')
        shot('smoke_login_failed')

    # Landing: /curate/ goes straight to the pieces list (no dashboard)
    page.goto(f'{BASE}/curate/', wait_until='domcontentloaded')
    if page.url.rstrip('/').endswith('/curate/pieces'):
        ok('/curate/ lands on Pieces')
    else:
        fail(f'/curate/ did not land on Pieces, url={page.url}')
    shot('smoke_02_pieces_landing')

    # Lists
    for slug, label in (
        ('pieces', 'Pieces'),
        ('artists', 'Artists'),
        ('mediums', 'Mediums'),
        ('locations', 'Locations'),
    ):
        page.goto(f'{BASE}/curate/{slug}/', wait_until='domcontentloaded')
        title = page.locator('h1.curate-page-title').inner_text()
        if title.strip() == label:
            ok(f'{label} list renders')
        else:
            fail(f'{label} list title was {title!r}')
        shot(f'smoke_03_{slug}')

    # Piece add form renders (no submit)
    page.goto(f'{BASE}/curate/pieces/add/', wait_until='domcontentloaded')
    expect(page.locator('select#id_medium')).to_be_visible()
    expect(page.locator('input#id_image')).to_have_count(1)
    ok('Piece add form renders (image dropzone + FK selects)')
    shot('smoke_04_piece_add')

    # Piece edit form: date_acquired prepopulation (read-only)
    href = page.goto(f'{BASE}/curate/pieces/', wait_until='domcontentloaded') and page.locator(
        'a.curate-piece__body'
    ).first.get_attribute('href')
    if href:
        page.goto(f'{BASE}{href}', wait_until='domcontentloaded')
        dtype = page.eval_on_selector('input#id_date_acquired', 'el => el.type')
        dval = page.eval_on_selector('input#id_date_acquired', 'el => el.value')
        if dtype == 'date':
            ok(f'Edit form date_acquired is type=date (value={dval!r})')
        else:
            fail(f'date_acquired type={dtype!r}')
        shot('smoke_05_piece_edit')

    browser.close()

print('\n===== CONSOLE / PAGE ERRORS =====')
print('\n'.join(errors) if errors else '(none)')
print('\n===== SUMMARY =====')
for k, m in results:
    print(f'{k}: {m}')
n_fail = sum(1 for k, _ in results if k == 'FAIL')
print(f'\n{len(results)} checks, {n_fail} FAIL — screenshots in {SHOTS}')
sys.exit(1 if n_fail else 0)
