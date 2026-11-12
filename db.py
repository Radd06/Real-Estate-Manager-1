"""SQLite layer for MOSR. Cloud-friendly: timezone-safe, self-migrating, no browser automation."""
import hashlib, os, random, sqlite3, time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

os.environ["TZ"] = os.environ.get("MOSR_TZ", "Asia/Manila")  # Streamlit Cloud servers run on UTC
if hasattr(time, "tzset"):
    time.tzset()

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "mosr.db"
LOG_PATH = ROOT / "logs" / "reminders.log"
NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT, price REAL,
  status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS posts(  -- legacy queue, only used by the local fb_bot.py
  id INTEGER PRIMARY KEY, listing_id INTEGER, body TEXT NOT NULL, image_path TEXT,
  scheduled_for TEXT, status TEXT DEFAULT 'queued', note TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')));
CREATE TABLE IF NOT EXISTS inquiries(
  id INTEGER PRIMARY KEY, sender TEXT, message TEXT, received_at TEXT,
  category TEXT, status TEXT DEFAULT 'new', uniq TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS appointments(
  id INTEGER PRIMARY KEY, inquiry_id INTEGER, client TEXT, title TEXT,
  start_at TEXT NOT NULL, notes TEXT, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS fb_groups(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, url TEXT, notes TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS post_log(
  id INTEGER PRIMARY KEY, listing_id INTEGER, group_id INTEGER, posted_at TEXT NOT NULL,
  status TEXT DEFAULT 'posted', post_url TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS snippets(id INTEGER PRIMARY KEY, category TEXT, title TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS reminder_logs(
  id INTEGER PRIMARY KEY, kind TEXT, ref_id INTEGER, tag TEXT, message TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime')), UNIQUE(kind, ref_id, tag));
"""
MIGRATE = {  # columns added after the first version, so older databases keep working
    "inquiries": [("source", "TEXT")],
    "appointments": [("location", "TEXT"), ("listing_id", "INTEGER")],
}
CATEGORIES = {  # auto-tag inquiries by keyword
    "Pricing": ("price", "how much", "magkano", "discount", "payment", "installment"),
    "Appointment": ("visit", "viewing", "schedule", "appointment", "tour", "see it"),
    "Availability": ("available", "still for sale", "still open", "avail"),
}
# {agent} {phone} {listing} {price} are filled in automatically; [brackets] are for you to edit.
DEFAULT_SNIPPETS = [
    ("About me", "Intro", "Hi! This is {agent}. You can message me here or call/text {phone}."),
    ("Replies", "Greeting", "Hi! Thanks for your interest in {listing}. How can I help you today?"),
    ("Replies", "Still available", "Yes, {listing} is still available! Would you like to schedule a viewing?"),
    ("Replies", "Price", "{listing} is listed at ₱{price}. I can send the full payment breakdown. Are you planning cash or financing?"),
    ("Replies", "Viewing", "We can arrange a viewing. What day and time work for you? Please bring a valid ID."),
    ("Replies", "Magkano? (Tagalog)", "Hello po! Salamat sa interest sa {listing}. Ang price po ay ₱{price}. Gusto niyo po bang mag-schedule ng viewing?"),
    ("Payment & financing", "Payment options", "Payment options: cash, bank financing, Pag-IBIG, or in-house installment. Reservation fee: ₱[amount]. Balance: [terms]."),
    ("Payment & financing", "Reservation requirements", "To reserve: 2 valid IDs, TIN, proof of income, and the reservation fee. I'll send the reservation form once you confirm."),
    ("Viewing", "Viewing details", "Viewings are [days] from [time] to [time]. Meeting point: [address]. Please message me when you're on the way."),
    ("Post templates", "For sale post", "🏡 FOR SALE: {listing}\n💰 ₱{price}\n📍 [location]\n🛏 [beds] | 🛁 [baths] | 📐 [floor area]\n✅ [title status / turnover]\n📞 {agent}: {phone}\nMessage us to book a viewing!"),
    ("Post templates", "For rent post", "🏠 FOR RENT: {listing}\n💰 ₱{price}/month\n📍 [location]\n🛏 [beds] | 🛁 [baths]\n✅ [deposit / advance terms]\n📞 {agent}: {phone}"),
]


@contextmanager
def conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for table, cols in MIGRATE.items():
            have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            for name, typ in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
        if not c.execute("SELECT 1 FROM snippets LIMIT 1").fetchone():
            c.executemany("INSERT INTO snippets(category,title,body) VALUES(?,?,?)", DEFAULT_SNIPPETS)


def q(sql, params=()) -> pd.DataFrame:
    with conn() as c:
        return pd.read_sql_query(sql, c, params=params)


def run(sql, params=()) -> int:
    with conn() as c:
        return c.execute(sql, params).lastrowid


def many(sql, rows):
    with conn() as c:
        c.executemany(sql, rows)


def categorize(text: str) -> str:
    t = (text or "").lower()
    return next((c for c, kws in CATEGORIES.items() if any(k in t for k in kws)), "General")


def add_inquiry(sender, message, received_at=None, uniq=None, source=None) -> int:
    """Insert once; returns 1 if new, 0 if already stored (safe to re-sync)."""
    uniq = uniq or hashlib.sha1(f"{sender}|{message}".encode()).hexdigest()
    with conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO inquiries(sender,message,received_at,category,uniq,source) VALUES(?,?,?,?,?,?)",
            (sender, message, received_at or NOW(), categorize(message), uniq, source))
        return cur.rowcount


def _log(kind, ref_id, tag, msg) -> int:
    with conn() as c:
        cur = c.execute("INSERT OR IGNORE INTO reminder_logs(kind,ref_id,tag,message) VALUES(?,?,?,?)",
                        (kind, int(ref_id), tag, msg))
    if cur.rowcount:
        LOG_PATH.parent.mkdir(exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{NOW()} [{kind}/{tag}] {msg}\n")
    return cur.rowcount


def run_reminders() -> int:
    """REMIND step: log due items once per item and window (24h, 1h, overdue inquiry)."""
    now, made = datetime.now(), 0
    pick = lambda h: "1h" if h <= 1 else "24h" if h <= 24 else None
    for r in q("SELECT * FROM appointments WHERE status='pending'").itertuples():
        h = (datetime.fromisoformat(r.start_at) - now).total_seconds() / 3600
        if h >= 0 and pick(h):
            made += _log("appointment", r.id, pick(h), f"{r.title} with {r.client} at {r.start_at} (in {h:.1f}h)")
    for r in q("""SELECT p.id, p.posted_at, l.title AS ltitle, g.name AS grp FROM post_log p
                  LEFT JOIN listings l ON l.id=p.listing_id LEFT JOIN fb_groups g ON g.id=p.group_id
                  WHERE p.status='planned'""").itertuples():
        h = (datetime.fromisoformat(r.posted_at) - now).total_seconds() / 3600
        if h >= 0 and pick(h):
            made += _log("post", r.id, pick(h), f"Post {r.ltitle} to {r.grp} at {r.posted_at} (in {h:.1f}h)")
    for r in q("SELECT * FROM inquiries WHERE status='new'").itertuples():
        if now - datetime.fromisoformat(r.received_at) > timedelta(hours=24):
            made += _log("inquiry", r.id, "overdue", f"Unanswered >24h: {r.sender} - {str(r.message)[:60]}")
    return made


def setting(k, default=""):
    r = q("SELECT v FROM settings WHERE k=?", (k,))
    return r.v[0] if len(r) else default


def set_setting(k, v):
    run("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def save_snippets(df):
    rows = [(str(r.category).strip(), str(r.title).strip(), str(r.body)) for r in df.itertuples()
            if str(r.title).strip() not in ("", "None") and str(r.body).strip() not in ("", "None")]
    with conn() as c:
        c.execute("DELETE FROM snippets")
        c.executemany("INSERT INTO snippets(category,title,body) VALUES(?,?,?)", rows)


def restore(data: bytes):
    """Replace the database with an uploaded backup (validated first)."""
    tmp = DB_PATH.with_suffix(".tmp")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        c = sqlite3.connect(tmp)
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        c.close()
    except sqlite3.DatabaseError:
        names = set()
    if not {"listings", "inquiries", "appointments"} <= names:
        tmp.unlink(missing_ok=True)
        raise ValueError("That file is not a MOSR backup.")
    tmp.replace(DB_PATH)
    init()


def wipe():
    with conn() as c:
        for t in ("listings", "posts", "inquiries", "appointments", "fb_groups", "post_log", "reminder_logs"):
            c.execute(f"DELETE FROM {t}")


def seed_demo():
    """Sample data so every page has something to show before you enter real data."""
    rnd, now = random.Random(7), datetime.now()
    fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:00")
    lids = [run("INSERT INTO listings(title,description,price) VALUES(?,?,?)", (t, "Sample listing", p))
            for t, p in [("2BR Condo, Tanauan", 2_500_000.0), ("Lot for sale, Sto. Tomas", 1_200_000.0),
                         ("Townhouse, Lipa", 3_800_000.0)]]
    names = ["Lipa Buy & Sell", "Batangas Real Estate", "Tanauan Marketplace", "Sto. Tomas Properties", "Calabarzon Homes"]
    many("INSERT OR IGNORE INTO fb_groups(name) VALUES(?)", [(n,) for n in names])
    gids = q("SELECT id FROM fb_groups")["id"].tolist()
    for lid in lids:
        for gid in rnd.sample(gids, 3):
            run("INSERT INTO post_log(listing_id,group_id,posted_at,status) VALUES(?,?,?,?)",
                (lid, gid, fmt(now - timedelta(days=rnd.randint(0, 14), hours=rnd.randint(0, 12))),
                 rnd.choice(["posted", "posted", "posted", "pending approval"])))
    for i in range(2):
        run("INSERT INTO post_log(listing_id,group_id,posted_at,status) VALUES(?,?,?,'planned')",
            (lids[i], gids[-1 - i], fmt(now + timedelta(hours=2 + 20 * i))))
    msgs = ["Hi, how much is the unit?", "Is this still available?", "Can I schedule a viewing this weekend?",
            "Hello, can you send more details?"]
    who = ["Ana Cruz", "Ben Reyes", "Carla Lim", "Dan Santos", "Ella Tan", "Fred Uy"]
    weights = [1] * 12 + [3, 3] + [1] * 4 + [7] * 4 + [1] * 2
    for i in range(40):
        ts = now - timedelta(days=rnd.randint(0, 25))
        ts = ts.replace(hour=rnd.choices(range(24), weights)[0], minute=rnd.randint(0, 59))
        status = rnd.choices(["new", "replied", "scheduled", "closed"], [3, 4, 1, 2])[0]
        add_inquiry(rnd.choice(who), rnd.choice(msgs), fmt(ts), uniq=f"demo{i}",
                    source=rnd.choice(["Group comment", "Messenger"]))
        run("UPDATE inquiries SET status=? WHERE uniq=?", (status, f"demo{i}"))
    for i in range(4):
        run("INSERT INTO appointments(client,title,start_at,listing_id,location) VALUES(?,?,?,?,?)",
            (rnd.choice(who), "Property viewing", fmt(now + timedelta(hours=3 + 22 * i)), rnd.choice(lids), "Meet at the gate"))
