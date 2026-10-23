"""`run` as a sequence of deliveries that fail independently.

The 07:00 run on 2026-09-17 is what these pin. Dropbox left the vault's parent
unreadable, `obsidian.write` raised a PermissionError, and because that
exception escaped the digest it took two further deliveries with it: Shane's
daily email never went out, and the Thursday issue never reached the list.
Three audiences, three providers, one unreadable folder.

A failed stage must still make the run exit non-zero — the wrapper in
`scripts/daily.sh` logs that code, and a delivery that quietly did not happen
is the failure this whole module exists to prevent.
"""
from __future__ import annotations

import pytest
import typer

from cre_radar import cli


@pytest.fixture()
def calls(monkeypatch):
    """Record which stages ran, with every stage stubbed to a no-op."""
    seen: list[str] = []

    monkeypatch.setattr(cli, "collect", lambda **_: seen.append("collect"))
    monkeypatch.setattr(cli, "score_cmd", lambda: seen.append("score"))
    monkeypatch.setattr(cli, "publish_cmd", lambda **_: seen.append("publish"))
    monkeypatch.setattr(cli, "digest_cmd", lambda **_: seen.append("digest"))
    monkeypatch.setattr(cli, "newsletter_cmd", lambda **_: seen.append("newsletter"))
    return seen


def _fails(exception: BaseException):
    def raiser(*_args, **_kwargs):
        raise exception
    return raiser


# --- one stage down must not take the others with it ------------------------

def test_newsletter_still_runs_when_the_digest_raises(calls, monkeypatch):
    monkeypatch.setattr(
        cli, "digest_cmd",
        _fails(PermissionError(1, "Operation not permitted", "/Team Folder MEP")),
    )
    with pytest.raises(typer.Exit):
        cli.run_all(no_email=False, publish=False)
    assert "newsletter" in calls


def test_deliveries_still_run_when_the_publish_fails(calls, monkeypatch):
    monkeypatch.setattr(cli, "publish_cmd", _fails(typer.Exit(1)))
    with pytest.raises(typer.Exit):
        cli.run_all(no_email=False, publish=True)
    assert calls == ["collect", "score", "digest", "newsletter"]


def test_digest_still_runs_when_the_newsletter_raises(calls, monkeypatch):
    monkeypatch.setattr(cli, "newsletter_cmd", _fails(RuntimeError("MailerLite is down")))
    with pytest.raises(typer.Exit):
        cli.run_all(no_email=False, publish=False)
    assert "digest" in calls


# --- but a failure is still a failed run ------------------------------------

def test_a_failed_stage_exits_non_zero(calls, monkeypatch):
    monkeypatch.setattr(cli, "digest_cmd", _fails(RuntimeError("no vault")))
    with pytest.raises(typer.Exit) as raised:
        cli.run_all(no_email=False, publish=False)
    assert raised.value.exit_code == 1


def test_a_clean_run_exits_zero_and_runs_everything(calls):
    cli.run_all(no_email=False, publish=True)
    assert calls == ["collect", "score", "publish", "digest", "newsletter"]


def test_the_newsletter_runs_after_the_digest(calls):
    """Ordering is still deliberate: a MailerLite outage must not cost Shane
    the vault note and the daily email."""
    cli.run_all(no_email=False, publish=False)
    assert calls.index("newsletter") > calls.index("digest")


# --- the vault is not the email ---------------------------------------------

@pytest.fixture()
def digest_stubs(monkeypatch):
    """Stub the digest's dependencies. Returns what each one was handed."""
    sent: dict[str, object] = {"emails": [], "marked": []}

    monkeypatch.setattr(cli.db, "connect", lambda *a, **k: object())
    monkeypatch.setattr(cli.db, "pending_events", lambda *a, **k: [{"id": 7}])
    monkeypatch.setattr(cli.db, "mark_surfaced",
                        lambda _conn, _table, ids: sent["marked"].extend(ids))
    monkeypatch.setattr(cli.render, "markdown", lambda *a, **k: "# note")
    monkeypatch.setattr(cli.render, "html_email", lambda *a, **k: "<p>note</p>")
    monkeypatch.setattr(cli.email_digest, "send", lambda *a, **k: (
        sent["emails"].append("sent") or "msg-1"))
    return sent


def test_an_unwritable_vault_still_sends_the_email(digest_stubs, monkeypatch):
    """2026-09-17: Dropbox left the vault's parent unreadable and the email,
    which goes nowhere near Dropbox, never went out."""
    monkeypatch.setattr(cli.obsidian, "write", _fails(
        PermissionError(1, "Operation not permitted", "/Team Folder MEP")))

    with pytest.raises(typer.Exit) as raised:
        cli.digest_cmd(dry_run=False, no_email=False, no_obsidian=False)

    assert digest_stubs["emails"] == ["sent"]
    assert raised.value.exit_code == 1


def test_an_unwritable_vault_still_marks_the_events_surfaced(digest_stubs, monkeypatch):
    """They reached Shane by email. Sending them again tomorrow is the worse bug."""
    monkeypatch.setattr(cli.obsidian, "write", _fails(OSError("vault gone")))

    with pytest.raises(typer.Exit):
        cli.digest_cmd(dry_run=False, no_email=False, no_obsidian=False)

    assert digest_stubs["marked"] == [7]


@pytest.mark.parametrize("no_email", [True, False])
def test_failed_vault_keeps_events_pending_without_email(digest_stubs, monkeypatch, no_email):
    monkeypatch.setattr(cli.obsidian, "write", _fails(OSError("vault gone")))
    monkeypatch.setattr(cli.email_digest, "send", lambda *a, **k: None)
    with pytest.raises(typer.Exit):
        cli.digest_cmd(dry_run=False, no_email=no_email, no_obsidian=False)
    assert digest_stubs["marked"] == []


def test_collection_reports_failure_after_processing_all_sources(monkeypatch, capsys):
    from cre_radar.events import SourceResult
    monkeypatch.setattr(cli.db, "connect", lambda: object())
    monkeypatch.setattr(cli.events, "run", lambda *a, **k: [
        SourceResult(slug="blocked", ok=False, error="403 Forbidden"),
        SourceResult(slug="working", found=2, inserted=2),
    ])
    with pytest.raises(typer.Exit):
        cli.collect(only=None, force=False)
    assert "403 Forbidden" in capsys.readouterr().out


def test_collection_failure_still_runs_deliveries(calls, monkeypatch):
    monkeypatch.setattr(cli, "collect", _fails(typer.Exit(1)))
    with pytest.raises(typer.Exit):
        cli.run_all(no_email=False, publish=True)
    assert calls == ["score", "publish", "digest", "newsletter"]


def test_rebuild_scores_successful_sources_even_when_collection_fails(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    original = Path(__file__).resolve().parents[1] / "scripts" / "rebuild.sh"
    script = scripts / "rebuild.sh"
    script.write_text(original.read_text())
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/sh\necho "$*" >> calls\n[ "$3" != "collect" ]\n')
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setenv("CRE_DB", str(tmp_path / "test.db"))
    result = subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, check=False,
    )
    assert (tmp_path / "calls").read_text().splitlines() == [
        "run cre-radar collect", "run cre-radar score",
    ]
    assert result.returncode == 1
