# Legacy UI archive

`dashboard_legacy.py` is the original CRM dashboard exactly as it was before
the 2026 design-system UI was promoted on 2026-07-07. Same functionality,
old styling.

## Roll back to the old UI

Option A — swap it back permanently:

    copy legacy_ui\dashboard_legacy.py dashboard.py

then restart the CRM (close the supervisor window and run `start.bat`, or just
double-click `start.bat`).

Option B — run it side-by-side to compare (no changes to the main app):

    python -m streamlit run legacy_ui/dashboard_legacy.py --server.port 8505

The old UI is also preserved in git history (every commit up to
"Add dashboard_v2" used it), so this folder is a convenience copy.
