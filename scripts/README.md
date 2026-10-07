# Scripts

Run them by hand; they are not part of the application or of any hook.

| Script | Purpose |
|---|---|
| [`verify_reference.py`](verify_reference.py) | Re-checks the arithmetic in [`data/reference/expected.json`](../data/reference/expected.json) with `fractions`. It imports nothing from the app, so the expected results stay independent of the code under test. `uv run python scripts/verify_reference.py` |
| [`render_attachments.py`](render_attachments.py) | Renders the image order form for request R11 with Pillow, deterministically. `uv run python scripts/render_attachments.py` |
