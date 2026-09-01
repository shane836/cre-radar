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

APP_NAME = "CRE Events Radar"

# The heading over the listing, in all three channels. Separate from APP_NAME
# on purpose: the name reads as a name, but "Upcoming CRE Events Radar" does
# not read as English. What the page shows is upcoming events; what the product
# is called is the radar.
HEADING = "Upcoming SoCal CRE Events"
