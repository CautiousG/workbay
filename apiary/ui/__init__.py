"""The web UI: python -m apiary ui (or ui.bat).

    api.py      Api: plain Python in, JSON-ready dicts out; ApiError for what it refuses
    server.py   the local HTTP server (127.0.0.1 only): /api/* -> Api, /api/events as
                server-sent events, / and /static/* the page

The page (its html shell in server.py; static/app.css) is plain JS, one file per part:
app.js (helpers, API calls, shared state, tabs, job events), ws.js (Workspaces view:
cards, repo tables), dialogs.js (batch bar, west update, command, manifest panel,
auto refresh), drawer.js (one repo: changes, diff, commit), jobs.js, settings.js.
"""
