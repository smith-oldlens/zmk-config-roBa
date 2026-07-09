"""Email digests of collected pins via Gmail SMTP.

Free to use: needs only a Gmail address and an app password (Google
account with 2-step verification → https://myaccount.google.com/apppasswords),
passed via the GMAIL_ADDRESS / GMAIL_APP_PASSWORD environment variables.

Send timing is controlled by config (notify.email):

    frequency: every_run | daily | weekly
    hour:      for daily/weekly, the first collector run at/after this
               local hour sends the digest
    min_items: skip sending (carry items over) until this many pins queued
    max_images: how many images to embed inline

Pins picked by each run are queued in state.json (pending_notify); a run
that satisfies the schedule emails everything queued since the last digest,
so nothing is lost between digests. Note: an email can only go out when the
collector actually runs — schedule your cron/Actions accordingly.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

_WEEK_SECONDS = 7 * 24 * 3600
_DAY_SECONDS = 24 * 3600


def credentials() -> tuple[str | None, str | None]:
    return os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")


def is_due(email_cfg: dict, last_notified: float, pending_count: int, now: dt.datetime) -> bool:
    if pending_count < int(email_cfg.get("min_items", 1)):
        return False
    frequency = email_cfg.get("frequency", "daily")
    if frequency == "every_run":
        return True

    hour = int(email_cfg.get("hour", 8))
    if now.hour < hour:
        return False
    # Elapsed-time guard so one run per period sends, regardless of how many
    # runs happen after `hour`. A small margin tolerates cron jitter.
    elapsed = now.timestamp() - last_notified
    if frequency == "weekly":
        return elapsed >= _WEEK_SECONDS - 3600
    return elapsed >= _DAY_SECONDS - 3600  # daily (default)


def _load_pending_items(pending_ids: list[str], download_dir: str | Path) -> list[dict]:
    items = []
    directory = Path(download_dir)
    for sidecar in directory.glob("*.json"):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and str(data.get("id")) in pending_ids:
            data["_image_path"] = directory / sidecar.name[:-5]
            items.append(data)
    items.sort(key=lambda d: float(d.get("score", 0)), reverse=True)
    return items


def _build_message(sender: str, to: str, items: list[dict], max_images: int) -> EmailMessage:
    msg = EmailMessage()
    today = dt.date.today().isoformat()
    msg["Subject"] = f"pinterest-collector: 新着 {len(items)} 件 ({today})"
    msg["From"] = sender
    msg["To"] = to

    lines = [f"新しく収集したピン {len(items)} 件:"]
    for item in items:
        lines.append(f"- [{float(item.get('score', 0)):.1f}] {item.get('title') or '(no title)'}")
        lines.append(f"  {item.get('link', '')}")
    msg.set_content("\n".join(lines))

    rows = []
    inline: list[tuple[str, bytes]] = []
    for item in items:
        title = html.escape(item.get("title") or "(no title)")
        link = html.escape(item.get("link") or "#")
        score = float(item.get("score", 0))
        img_html = ""
        image_path: Path = item.get("_image_path")
        if len(inline) < max_images and image_path and image_path.exists():
            cid = make_msgid()
            inline.append((cid, image_path.read_bytes()))
            img_html = f'<br><img src="cid:{cid[1:-1]}" style="max-width:320px;border-radius:8px">'
        rows.append(
            f'<p><a href="{link}"><b>{title}</b></a> '
            f'<span style="color:#888">score {score:.1f}</span>{img_html}</p>'
        )
    msg.add_alternative(
        f"<html><body><h2>新着 {len(items)} 件</h2>{''.join(rows)}</body></html>",
        subtype="html",
    )

    payload = msg.get_payload()[1]  # the HTML alternative
    for cid, data in inline:
        payload.add_related(data, maintype="image", subtype="jpeg", cid=cid)
    return msg


def send_digest(cfg: dict, state, now: dt.datetime | None = None) -> bool:
    """Send a digest if due. Returns True when an email went out."""
    email_cfg = (cfg.get("notify") or {}).get("email") or {}
    if not email_cfg.get("enabled"):
        return False

    sender, password = credentials()
    if not sender or not password:
        log.warning("Email notify is enabled but GMAIL_ADDRESS/GMAIL_APP_PASSWORD are not set.")
        return False

    now = now or dt.datetime.now()
    pending = state.pending_notify
    if not is_due(email_cfg, state.last_notified, len(pending), now):
        if pending:
            log.info("Email digest not due yet (%d pins queued).", len(pending))
        return False

    items = _load_pending_items(pending, cfg["output"]["download_dir"])
    if not items:
        state.pending_notify = []
        return False

    to = email_cfg.get("to") or sender
    msg = _build_message(sender, to, items, int(email_cfg.get("max_images", 5)))
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("Failed to send email digest (will retry next run): %s", exc)
        return False

    log.info("Emailed digest of %d pins to %s.", len(items), to)
    state.pending_notify = []
    state.last_notified = now.timestamp()
    return True
