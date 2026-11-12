"""Playwright bridge to Meta Business Suite.

CLI (the Streamlit app calls these as subprocesses, which avoids event-loop clashes):
    python fb_bot.py login              # one-time manual login, session is kept in data/fb_profile
    python fb_bot.py inbox              # pull inbox conversations into the inquiries table
    python fb_bot.py publish [--auto]   # fill the composer for every 'queued' post

Meta changes its UI often. Every selector lives in SEL below so you can tune them in one place.
Failures save a screenshot to data/screens/.
"""
import re, sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import db

PROFILE = db.ROOT / "data" / "fb_profile"
SHOTS = db.ROOT / "data" / "screens"
BASE = "https://business.facebook.com/latest"

SEL = {  # best-effort defaults, verify against your own Business Suite UI
    "composer_box": 'div[role="textbox"][contenteditable="true"]',
    "file_input": 'input[type="file"]',
    "schedule_toggle": "Set date and time",   # visible text
    "date_input": "mm/dd/yyyy",               # placeholder
    "hour": "hours", "minute": "minutes", "meridian": "meridiem",  # aria-labels
    "submit": "Schedule",                     # button text
    "inbox_row": 'div[role="row"]',
}


def _ctx(p):
    PROFILE.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport={"width": 1366, "height": 850})


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / f"{name}_{datetime.now():%H%M%S}.png"
    page.screenshot(path=str(path))
    return path.name


def login():
    with sync_playwright() as p:
        ctx = _ctx(p)
        page = ctx.new_page()
        page.goto(BASE)
        print("Log in in the opened window (5 min limit)...")
        page.wait_for_url(re.compile(r"business\.facebook\.com/latest"), timeout=300_000)
        page.wait_for_timeout(3000)
        ctx.close()
    print("Session saved.")


def sync_inbox(limit=40):
    with sync_playwright() as p:
        ctx = _ctx(p)
        page = ctx.new_page()
        try:
            page.goto(f"{BASE}/inbox/all", wait_until="domcontentloaded")
            page.wait_for_selector(SEL["inbox_row"], timeout=45_000)
            rows, new = page.locator(SEL["inbox_row"]), 0
            for i in range(min(rows.count(), limit)):
                lines = [l.strip() for l in rows.nth(i).inner_text().split("\n") if l.strip()]
                if len(lines) >= 2:
                    new += db.add_inquiry(lines[0], lines[1])  # sender, last message preview
            print(f"Inbox synced: {new} new inquiries.")
        except Exception as e:
            print(f"Inbox sync failed: {e}\nScreenshot: {_shot(page, 'inbox')}")
        finally:
            ctx.close()


def _schedule(page, post, auto):
    page.goto(f"{BASE}/composer", wait_until="domcontentloaded")
    box = page.locator(SEL["composer_box"]).first
    box.wait_for(timeout=30_000)
    box.click()
    box.fill(post.body)
    if post.image_path:
        page.locator(SEL["file_input"]).first.set_input_files(post.image_path)
    page.get_by_text(SEL["schedule_toggle"]).first.click()
    d = datetime.fromisoformat(post.scheduled_for)
    page.get_by_placeholder(SEL["date_input"]).first.fill(d.strftime("%m/%d/%Y"))
    for label, fmt in ((SEL["hour"], "%I"), (SEL["minute"], "%M"), (SEL["meridian"], "%p")):
        page.get_by_label(label).first.fill(d.strftime(fmt))
    if auto:
        page.get_by_role("button", name=SEL["submit"]).last.click()
        page.wait_for_timeout(4000)
        return "scheduled"
    page.wait_for_timeout(60_000)  # leave the form open so you can review and click Schedule yourself
    return "review"


def publish_queued(auto=False):
    posts = db.q("SELECT * FROM posts WHERE status='queued' ORDER BY scheduled_for")
    if posts.empty:
        return print("Nothing queued.")
    with sync_playwright() as p:
        ctx = _ctx(p)
        for post in posts.itertuples():
            page = ctx.new_page()
            try:
                status, note = _schedule(page, post, auto), None
            except Exception as e:
                status, note = "failed", f"{e} | {_shot(page, f'post{post.id}')}"
            db.run("UPDATE posts SET status=?, note=? WHERE id=?", (status, note, post.id))
            print(f"Post #{post.id}: {status}" + (f" ({note})" if note else ""))
            page.close()
        ctx.close()


if __name__ == "__main__":
    db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login": login()
    elif cmd == "inbox": sync_inbox()
    elif cmd == "publish": publish_queued("--auto" in sys.argv)
    else: print(__doc__)
