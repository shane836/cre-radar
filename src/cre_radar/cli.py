"""The command surface. `cre-radar run` is the one a cron job calls."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

import typer

from . import HEADING, db, events, score
from .config import min_score
from .digest import email as email_digest
from .digest import obsidian, render
from .fetch import condense, fetch_rendered, fetch_static
from .sources.registry import load as load_sources

app = typer.Typer(
    help="Find Southern California commercial real estate and real estate investment events.",
    no_args_is_help=True,
)
sources_app = typer.Typer(help="Inspect and check the event source registry.")

# Below this many condensed characters, a source is almost certainly returning a
# bot interstitial or an unrendered shell rather than a real calendar.
THIN = 800
app.add_typer(sources_app, name="sources")


def _echo_ok(message: str) -> None:
    typer.secho(message, fg=typer.colors.GREEN)


def _echo_bad(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED)


def _stage(label: str, action: Callable[[], object]) -> bool:
    """Run one stage of `run`, reporting a failure instead of raising it.

    Each stage after this one reaches a different audience through a different
    provider, so an exception escaping one silences deliveries that had nothing
    to do with it. On 2026-09-17 Dropbox left the vault's parent unreadable,
    `obsidian.write` raised, and that one folder cost Shane the daily email and
    the list its Thursday issue.

    Reported, never swallowed: `run` exits non-zero when any stage returns
    False, and `scripts/daily.sh` logs that code.
    """
    try:
        action()
    except typer.Exit as exited:
        # The stage already printed why; anything but 0 is still a failure.
        if exited.exit_code == 0:
            return True
        _echo_bad(f"{label} failed (exit {exited.exit_code})")
        return False
    except Exception as error:  # noqa: BLE001 - a stage boundary catches everything
        _echo_bad(f"{label} failed: {type(error).__name__}: {error}")
        return False
    return True


def collect(
    only: list[str] = typer.Option(None, "--only", help="Limit to these source slugs."),
    force: bool = typer.Option(
        False, "--force", help="Re-extract even if the calendar is unchanged."
    ),
) -> None:
    """Fetch every source, condense it, and extract its events.

    Calendars whose link set is unchanged since the last successful extraction
    are skipped. Use --force after changing extraction rules.
    """
    conn = db.connect()
    failed = False
    for result in events.run(conn, only or None, force=force):
        if result.unchanged:
            typer.secho(f"{result.slug:<18} {result.summary}", fg=typer.colors.BLUE)
        elif result.ok:
            _echo_ok(f"{result.slug:<18} {result.summary}")
        else:
            failed = True
            _echo_bad(f"{result.slug:<18} {result.error}")
    if failed:
        raise typer.Exit(1)


app.command("collect")(collect)


@app.command("score")
def score_cmd() -> None:
    """Score everything not yet judged, against `scoring.toml`."""
    _echo_ok(f"scored {score.run(db.connect())} events")


@app.command("rescore")
def rescore_cmd() -> None:
    """Re-score every event after editing `scoring.toml`.

    Already-delivered events stay delivered — this changes their score, not
    whether you have seen them.
    """
    _echo_ok(f"re-scored {score.rescore(db.connect())} events")


@app.command("digest")
def digest_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the note; deliver nothing."),
    no_email: bool = typer.Option(False, "--no-email"),
    no_obsidian: bool = typer.Option(False, "--no-obsidian"),
) -> None:
    """Deliver everything scored above the floor, then mark it delivered."""
    conn = db.connect()
    floor = min_score()
    day = date.today()

    pending = db.pending_events(conn, floor)
    note = render.markdown(pending, day=day)

    if not pending and not dry_run:
        typer.secho(
            f"nothing new at score >= {floor} — no note, no email.",
            fg=typer.colors.YELLOW,
        )
        return

    if dry_run:
        typer.echo(note)
        typer.secho(
            f"\n[dry run] {len(pending)} events at score >= {floor}. "
            "Nothing delivered or marked.",
            fg=typer.colors.YELLOW,
        )
        return

    vault_failed = False
    delivered = False
    if not no_obsidian:
        try:
            _echo_ok(f"note written: {obsidian.write(note, day=day)}")
            delivered = True
        except (OSError, RuntimeError) as error:
            # Dropbox can leave the vault's parent unreadable for a whole run,
            # and an unset OBSIDIAN_DIGEST_DIR raises here too. Neither is a
            # reason to skip the email — that is the delivery Shane actually
            # reads, and it goes somewhere Dropbox cannot touch. The non-zero
            # exit below still calls this a failed run.
            _echo_bad(f"note not written: {type(error).__name__}: {error}")
            vault_failed = True

    if not no_email:
        message_id = email_digest.send(render.html_email(pending, day=day), day=day)
        if message_id:
            delivered = True
            _echo_ok(f"email sent: {message_id}")
        else:
            typer.secho("email skipped (Resend not configured)", fg=typer.colors.YELLOW)

    if delivered:
        db.mark_surfaced(conn, "events", [row["id"] for row in pending])

    if vault_failed:
        raise typer.Exit(1)


@app.command("newsletter")
def newsletter_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the HTML; send nothing."),
    force: bool = typer.Option(False, "--force", help="Send even if today is not the send day."),
    no_report: bool = typer.Option(False, "--no-report", help="Skip Shane's copy."),
) -> None:
    """Send the public weekly issue to the MailerLite list.

    Runs inside the daily job rather than on a scheduler of its own: there is
    exactly one scheduler by design, and a second launchd agent firing at the
    same 07:00 would race the daily run for the lock on one SQLite file. The
    day gate below is what makes a daily invocation weekly.
    """
    from datetime import date as date_cls

    from . import site
    from .config import (
        newsletter_day,
        newsletter_horizon_days,
        newsletter_postal_address,
    )
    from .digest import newsletter

    day = date_cls.today()
    if not force and day.weekday() != newsletter_day():
        typer.secho("not the send day — nothing to do.", fg=typer.colors.BLUE)
        return

    horizon = newsletter_horizon_days()
    rows = db.newsletter_events(db.connect(), min_score(), horizon)
    issue = render.html_newsletter(
        rows,
        day=day,
        horizon_days=horizon,
        site_url=site.SITE_URL,
        firm=site.FIRM,
        firm_url=site.FIRM_URL,
        postal_address=newsletter_postal_address(),
    )
    subject = f"{HEADING} — {day.strftime('%-d %b')}"

    if dry_run:
        typer.echo(issue)
        typer.secho(
            f"\n[dry run] {len(rows)} events in the next {horizon} days. Nothing sent.",
            fg=typer.colors.YELLOW,
        )
        return

    # An empty issue is worse than no issue: it trains subscribers to ignore
    # the sender and costs deliverability on a domain that is still warming.
    # Shane still hears about it below — a quiet week must not look like a
    # broken pipeline.
    people: list[dict] = []
    if not newsletter.configured():
        typer.secho("newsletter skipped (MailerLite not configured)", fg=typer.colors.YELLOW)
    elif not rows:
        typer.secho(
            f"no events in the next {horizon} days — nothing sent to the list.",
            fg=typer.colors.YELLOW,
        )
    else:
        people = newsletter.subscribers()
        campaign = newsletter.send_campaign(
            issue, subject=subject, name=f"cre-radar {day.isoformat()}"
        )
        _echo_ok(f"campaign sent to {len(people)} subscribers: {campaign}")

    if no_report:
        return

    # Shane's copy: the issue exactly as it went out, plus who joined. One
    # email, so the thing he is asked to check and the list it went to are
    # never a tab apart.
    if newsletter.configured() and not people:
        people = newsletter.subscribers()
    report = render.html_signup_report(
        newsletter.new_since(people, 7), day=day, total=len(people)
    )
    message_id = email_digest.send(issue + report, day=day, subject=f"[copy] {subject}")
    if message_id:
        _echo_ok(f"your copy sent: {message_id}")
    else:
        typer.secho("your copy skipped (Resend not configured)", fg=typer.colors.YELLOW)


def _page_is_live(body: str) -> bool:
    """Is this response the actual listing, rather than an error page?

    Deliberately checks for the page's own furniture, not just a 200. Vercel's
    404 is a 200-shaped HTML document from the same host, and a deployment that
    ships the function without the static output serves exactly that.
    """
    from . import APP_NAME

    titled = f"<title>{APP_NAME}</title>" in body
    # Either a week of events, or the honest "nothing cleared the floor" note.
    # Both are the page working; only neither means it is not there.
    return titled and ('class="week"' in body or 'class="empty"' in body)


@app.command("publish")
def publish_cmd(
    deploy: bool = typer.Option(
        False, "--deploy", help="Push to Vercel after generating (needs `vercel login`)."
    ),
    prod: bool = typer.Option(True, "--prod/--preview", help="Deploy target."),
) -> None:
    """Generate the public site into `public/`, optionally deploying it."""
    import re
    import shutil
    import subprocess
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from . import site
    from .config import REPO_ROOT

    conn = db.connect()
    rows = db.upcoming_events(conn, min_score())
    generated = datetime.now(ZoneInfo("America/Los_Angeles"))

    out = REPO_ROOT / "public"
    out.mkdir(exist_ok=True)
    page = out / "index.html"
    page.write_text(site.render(rows, generated=generated))
    _echo_ok(f"{len(rows)} upcoming events -> {page}")

    # The one asset that is not inlined into the page: `og:image` has to be a
    # URL a scraper can fetch. Copied from `assets/` rather than generated
    # here, so a stale card is a file someone can look at and re-shoot rather
    # than a silent step inside the build.
    card = REPO_ROOT / "assets" / site.OG_IMAGE
    if card.exists():
        shutil.copyfile(card, out / site.OG_IMAGE)
        _echo_ok(f"link-preview card -> {out / site.OG_IMAGE}")
    else:
        typer.secho(
            f"no assets/{site.OG_IMAGE} — link previews will render no image",
            fg=typer.colors.YELLOW,
        )

    if not deploy:
        typer.secho("not deployed (pass --deploy)", fg=typer.colors.YELLOW)
        return

    # Build here, then ship the output — never a bare `vercel deploy`.
    #
    # A bare deploy uploads the sources and builds them on Vercel, and the
    # upload skips gitignored paths. `public/` is gitignored (it is generated),
    # so the page never reaches the builder and the deployment comes out with
    # `api/subscribe` and no site at all: `/` returns 404 while the function
    # still answers. Verified against a preview deploy, Aug 2026 — the build
    # listed one lambda and zero static files.
    #
    # `vercel build` runs against this working tree, where the page was written
    # four lines ago, and `--prebuilt` ships that output verbatim.
    #
    # Both run from the repo root, not `public/`: the root carries vercel.json
    # (which turns framework detection off) and the project link.
    target = ["--prod"] if prod else []

    def _vercel(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["vercel", *args], cwd=REPO_ROOT,
            capture_output=True, text=True, check=False,
        )

    build = _vercel("build", "--yes", *target)
    if build.returncode != 0:
        _echo_bad(build.stderr.strip() or "vercel build failed")
        raise typer.Exit(1)

    # Retried once. The 07:00 run on 2026-08-31 failed here with a bare
    # "Error: Not authorized" that never reproduced — not in the same stripped
    # environment launchd uses, not since. An unattended daily job should
    # survive one transient refusal rather than skip a day's publish over it.
    result = _vercel("deploy", "--prebuilt", "--yes", *target)
    if result.returncode != 0:
        first = result.stderr.strip()
        result = _vercel("deploy", "--prebuilt", "--yes", *target)
        if result.returncode == 0:
            _echo_bad(f"deploy retried after: {first}")
    if result.returncode != 0:
        _echo_bad(result.stderr.strip() or "vercel deploy failed")
        raise typer.Exit(1)
    # Vercel prints progress on stderr and a JSON blob on stdout; the alias is
    # the only line that is actually useful here.
    urls = re.findall(r"https://[\w.-]+\.vercel\.app", result.stdout + result.stderr)
    _echo_ok(f"deployed: {urls[-1] if urls else 'ok'}")

    # A deploy can succeed and still leave the site down. On 2026-08-30 a plain
    # `vercel deploy` shipped `api/subscribe` and zero static files, and Vercel
    # reported it Ready and aliased it — the page 404'd for eleven hours and
    # nothing in this pipeline noticed. Exit codes are not evidence the site
    # works; the site is.
    if prod:
        import urllib.request

        try:
            with urllib.request.urlopen(site.SITE_URL, timeout=20) as response:
                body = response.read().decode("utf-8", "replace")
        except Exception as error:
            _echo_bad(f"deployed, but {site.SITE_URL} did not answer: {error}")
            raise typer.Exit(1) from error

        if not _page_is_live(body):
            _echo_bad(
                f"deployed, but {site.SITE_URL} is not serving the page. "
                "The deployment most likely shipped the function without the "
                "static output."
            )
            raise typer.Exit(1)
        _echo_ok(f"verified live: {site.SITE_URL}")


@app.command("run")
def run_all(
    no_email: bool = typer.Option(False, "--no-email"),
    publish: bool = typer.Option(
        True, "--publish/--no-publish", help="Also deploy the site to Vercel."
    ),
) -> None:
    """Everything, in order: collect, score, deliver. The cron entry point.

    Fully unattended. No API key, no model, no network beyond fetching the
    sources themselves.
    """
    collected = _stage("collect", lambda: collect(only=None, force=False))
    score_cmd()

    # Every stage below is its own delivery to its own audience, so each runs
    # whatever the one before it did. The order is still deliberate: the
    # newsletter goes last so that a MailerLite outage cannot cost Shane the
    # vault note and the daily email. The day gate inside it makes a daily call
    # weekly.
    ok = True
    if publish:
        ok = _stage("publish", lambda: publish_cmd(deploy=True, prod=True))
    digest_ok = _stage(
        "digest",
        lambda: digest_cmd(dry_run=False, no_email=no_email, no_obsidian=False),
    )
    issue_ok = _stage(
        "newsletter",
        lambda: newsletter_cmd(dry_run=False, force=False, no_report=no_email),
    )
    if not (collected and ok and digest_ok and issue_ok):
        raise typer.Exit(1)


@sources_app.command("list")
def sources_list() -> None:
    """Show the registry."""
    for source in load_sources():
        typer.echo(f"{source.slug:<18} {source.mode:<8} {source.url}")


@sources_app.command("check")
def sources_check(
    browser: bool = typer.Option(
        False, "--browser", help="Also render 'browser' sources in Chromium (slower)."
    ),
) -> None:
    """Fetch every source and report whether it still responds.

    Reports the **condensed** size, not the HTTP status, because a 200 that
    renders a bot interstitial or an empty JS shell looks identical to a healthy
    fetch at the transport layer. Anything under ``THIN`` is flagged: that is a
    source that will silently return no events.
    """
    for source in load_sources():
        if source.mode == "browser" and not browser:
            typer.secho(f"{source.slug:<18} skipped (needs --browser)", fg=typer.colors.YELLOW)
            continue
        try:
            html = fetch_rendered(source.url) if source.mode == "browser" else fetch_static(source.url)
            text, truncated = condense(html, source.url)
            detail = f"{len(html):>9,} bytes -> {len(text):>7,} chars"
            if truncated:
                detail += " (truncated)"
            if len(text) < THIN:
                _echo_bad(f"{source.slug:<18} THIN  {detail} — few events, or a blocked page. Check it.")
            else:
                _echo_ok(f"{source.slug:<18} ok    {detail}")
        except Exception as exc:  # noqa: BLE001 — a check reports, never aborts
            _echo_bad(f"{source.slug:<18} {type(exc).__name__}: {exc}")


@app.command("doctor")
def doctor_cmd() -> None:
    """Check every dependency and say exactly what is missing."""
    from . import doctor

    blocking = 0
    for check in doctor.run():
        if check.ok:
            _echo_ok(f"  ok    {check.name:<16} {check.detail}")
            continue
        label = "FAIL " if check.required else "warn "
        colour = typer.colors.RED if check.required else typer.colors.YELLOW
        typer.secho(f"  {label} {check.name:<16} {check.detail}", fg=colour)
        if check.fix:
            typer.secho(f"        -> {check.fix}", fg=typer.colors.WHITE)
        blocking += check.required

    typer.echo("")
    if blocking:
        typer.secho(f"{blocking} blocking issue(s) — `cre-radar run` will not work.",
                    fg=typer.colors.RED)
        raise typer.Exit(1)
    _echo_ok("Ready.")


@app.command("status")
def status(limit: int = typer.Option(20, help="How many recent runs to show.")) -> None:
    """Recent run history plus what is sitting in the database."""
    conn = db.connect()
    for row in conn.execute(
        "SELECT * FROM runs ORDER BY ran_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall():
        mark = "ok " if row["ok"] else "FAIL"
        detail = row["error"] or f"{row['found']} found, +{row['inserted']}"
        typer.echo(f"{row['ran_at']}  {mark}  {row['source']:<18} {detail}")

    floor = min_score()
    typer.echo("")
    typer.echo(f"pending at score >= {floor}: {len(db.pending_events(conn, floor))} events")
    waiting = len(db.unscored(conn, "events"))
    if waiting:
        typer.echo(f"waiting to be scored: {waiting}")


if __name__ == "__main__":
    app()
