"""Package root. Deliberately holds one thing: the product's public name.

`cre-radar` is what the repo, the CLI and the cron entry are called. `APP_NAME`
is what a reader sees — on the page, in the email heading, in the Obsidian
note. They are not the same string and should not be conflated: renaming the
product must never mean renaming the command.

It lives here because both `site.py` and `digest/render.py` need it and
`site.py` imports from `digest.render`, so anything lower would be a cycle.
This module imports nothing, which is what keeps that true.
"""
from __future__ import annotations

APP_NAME = "SoCal CRE Events"
