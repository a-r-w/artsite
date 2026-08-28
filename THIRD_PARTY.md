# Third-party assets

artsite vendors a couple of third-party front-end files under `art/static/art/`
(bundled, not loaded from a CDN, so the site has no external runtime dependency
and the Content-Security-Policy can stay `script-src 'self'`). Their licenses:

| Asset | Version | License | Source |
| --- | --- | --- | --- |
| `art/static/art/js/htmx.min.js` | 2.0.4 | Zero-Clause BSD (0BSD) | <https://htmx.org> · <https://github.com/bigskysoftware/htmx> |
| `art/static/art/css/normalize.css` | 5.0.0 | MIT | <https://github.com/necolas/normalize.css> |

All other CSS and JavaScript under `art/static/art/` is part of this project and
covered by the repository [LICENSE](LICENSE) (MIT).

To update a vendored asset, drop in the new minified release, keep its banner
comment, and bump the version in this table.
