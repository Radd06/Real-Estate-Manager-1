"""MOSR pages, grouped by the four workflow steps: Manage, Organize, Set, Remind."""
import html, os, sqlite3, subprocess, sys, uuid
from datetime import datetime, time, timedelta

import pandas as pd
import streamlit as st

import db

try:
    from streamlit_calendar import calendar as fullcalendar
except ImportError:  # the app still works; the Calendar page falls back to a table
    fullcalendar = None

LOCAL = os.environ.get("MOSR_LOCAL") == "1"  # browser automation only works on your own computer
INQ_STATUS = ["new", "replied", "scheduled", "closed"]
POST_STATUS = ["planned", "posted", "pending approval", "declined", "removed"]
SOURCES = ["Group comment", "Messenger", "Page comment", "Call / text", "Other"]
FMT = "%Y-%m-%d %H:%M:00"
STEPS = {  # one colour per step so you always know where you are
    "dash": ("Overview", "#1F2A28"), "manage": ("1  Manage", "#0F766E"), "organize": ("2  Organize", "#2F5D8A"),
    "set": ("3  Set", "#B7791F"), "remind": ("4  Remind", "#B4443A")}
sel = lambda options: st.column_config.SelectboxColumn(options=options)


def header(step, title, blurb):
    label, color = STEPS[step]
    st.markdown(f"<style>:root{{--page:{color}}}</style><div class='hd'><span class='chip'>{label}</span>"
                f"<h1>{title}</h1><p>{blurb}</p></div>", unsafe_allow_html=True)


def done(msg):
    """Show a toast on the next run, then refresh."""
    st.session_state["_flash"] = msg
    st.rerun()


def listings(active_only=True):
    return db.q("SELECT id,title,price FROM listings" + (" WHERE status='active'" if active_only else "") + " ORDER BY id DESC")


def fillers(listing_id=None):
    vals = {"{agent}": db.setting("agent"), "{phone}": db.setting("phone")}
    if listing_id:
        r = db.q("SELECT title,price FROM listings WHERE id=?", (int(listing_id),)).iloc[0]
        vals |= {"{listing}": r.title, "{price}": f"{(r.price or 0):,.0f}"}
    return {k: v for k, v in vals.items() if v}


def fill(text, vals):
    for k, v in vals.items():
        text = text.replace(k, v)
    return text


def bot(*args):
    with st.spinner("Browser automation running (watch the Chromium window)..."):
        r = subprocess.run([sys.executable, "fb_bot.py", *args], capture_output=True, text=True, cwd=db.ROOT)
    st.code((r.stdout + r.stderr).strip() or "(no output)")


def locked():
    if not LOCAL:
        st.info("This drives a real Chromium window, so it only runs on your own computer. "
                "Start the app locally with MOSR_LOCAL=1 to turn it on (see the README).")
    return not LOCAL


# =============================================================== Dashboard
def dashboard():
    header("dash", "Dashboard", "Your inquiries and schedule at a glance.")
    now = datetime.now()
    inq = db.q("SELECT * FROM inquiries")
    if inq.empty:
        st.info("No data yet. Add your listings and groups under **Manage**, log inquiries under **Organize**, or explore with sample data.")
        if st.button("Load sample data", type="primary"):
            db.seed_demo()
            done("Sample data loaded")
        return
    inq["received_at"] = pd.to_datetime(inq["received_at"])
    n_appt = len(db.q("SELECT id FROM appointments"))
    c = st.columns(4)
    c[0].metric("Inquiries", len(inq))
    c[1].metric("Awaiting reply", int((inq.status == "new").sum()))
    c[2].metric("Answered", f"{(inq.status != 'new').mean():.0%}")
    c[3].metric("Inquiry to appointment", f"{n_appt / len(inq):.0%}")

    hours = inq.received_at.dt.hour.value_counts().reindex(range(24), fill_value=0)
    daily = inq.set_index("received_at").resample("D").size()
    g = st.columns(3)
    g[0].markdown("**Inquiries per day**")
    g[0].bar_chart(daily, height=220)
    g[1].markdown("**What people ask about**")
    g[1].bar_chart(inq.category.value_counts(), horizontal=True, height=220)
    g[2].markdown("**Inquiries by hour**")
    g[2].bar_chart(hours, height=220)
    st.success(f"Most inquiries arrive around {hours.idxmax():02d}:00. Schedule posts 1 to 2 hours earlier to catch that peak.")

    st.subheader("Next 3 days")
    appts = db.q("""SELECT a.*, l.title AS ltitle FROM appointments a LEFT JOIN listings l ON l.id=a.listing_id
                    WHERE a.status='pending'""")
    plan = db.q("""SELECT p.posted_at, l.title AS ltitle, g.name AS grp FROM post_log p
                   LEFT JOIN listings l ON l.id=p.listing_id LEFT JOIN fb_groups g ON g.id=p.group_id WHERE p.status='planned'""")
    rows = [(r.start_at, "Viewing", f"{r.client}: {r.ltitle or r.title}") for r in appts.itertuples()]
    rows += [(r.posted_at, "Post", f"{r.ltitle} to {r.grp}") for r in plan.itertuples()]
    rows = sorted(r for r in rows if r[0] <= (now + timedelta(days=3)).strftime(FMT))
    if rows:
        st.dataframe(pd.DataFrame(rows, columns=["When", "Type", "Details"]), hide_index=True, width="stretch")
    else:
        st.caption("Nothing scheduled.")


# =============================================================== 1 Manage
def library():
    header("manage", "Listings and groups", "The properties you advertise and the Facebook groups you post in.")
    st.subheader("Listings")
    with st.expander("Add a listing", icon=":material/add:"):
        with st.form("listing", clear_on_submit=True):
            t = st.text_input("Title", placeholder="2BR Condo, Tanauan")
            p = st.number_input("Price (₱)", 0.0, step=10000.0, format="%.0f")
            d = st.text_area("Description")
            if st.form_submit_button("Save listing", type="primary") and t:
                db.run("INSERT INTO listings(title,description,price) VALUES(?,?,?)", (t, d, p))
                done("Listing added")
    ls = db.q("SELECT id,title,price,status,description FROM listings ORDER BY id DESC")
    if not ls.empty:
        ed = st.data_editor(ls, key="ls_ed", hide_index=True, width="stretch", disabled=["id"],
                            column_config={"status": sel(["active", "sold", "archived"])})
        if st.button("Save listing changes"):
            db.many("UPDATE listings SET title=?,price=?,status=?,description=? WHERE id=?",
                    [(r.title, r.price, r.status, r.description, r.id) for r in ed.itertuples()])
            done("Listings saved")

    st.subheader("Facebook groups and pages")
    with st.expander("Add groups", icon=":material/add:"):
        names = st.text_area("One group per line", placeholder="Lipa Buy & Sell\nBatangas Real Estate")
        if st.button("Add groups"):
            rows = [(n.strip(),) for n in names.splitlines() if n.strip()]
            db.many("INSERT OR IGNORE INTO fb_groups(name) VALUES(?)", rows)
            done(f"Added {len(rows)} group(s)")
    g = db.q("SELECT id,name,url,notes,active FROM fb_groups ORDER BY name")
    if g.empty:
        st.caption("No groups yet. Add your Page and the groups you post in.")
    else:
        g["active"] = g["active"].fillna(1).astype(bool)
        ed = st.data_editor(g, key="g_ed", hide_index=True, width="stretch", disabled=["id"],
                            column_config={"notes": "rules / notes", "active": st.column_config.CheckboxColumn()})
        if st.button("Save group changes"):
            try:
                db.many("UPDATE fb_groups SET name=?,url=?,notes=?,active=? WHERE id=?",
                        [(r.name, r.url, r.notes, int(r.active), r.id) for r in ed.itertuples()])
                done("Groups saved")
            except sqlite3.IntegrityError:
                st.error("Two groups have the same name. Rename one and save again.")


def tracker():
    header("manage", "Post tracker", "Log where each listing was posted and spot the groups you haven't covered.")
    ls = listings()
    gs = db.q("SELECT id,name FROM fb_groups WHERE active=1 ORDER BY name")
    if ls.empty or gs.empty:
        st.info("Add at least one listing and one group under **Listings and groups** first.")
        return
    title_of = dict(zip(ls.id, ls.title))

    with st.expander("Log a post", expanded=True, icon=":material/edit_note:"):
        when = st.radio("When", ["Just now", "Pick date & time"], horizontal=True, label_visibility="collapsed")
        with st.form("logpost", clear_on_submit=True):
            lid = st.selectbox("Listing", ls.id, format_func=title_of.get)
            picked = st.multiselect("Groups or Page", gs.name, help="Select every group you posted this in")
            c = st.columns(3)
            status = c[0].selectbox("Status", POST_STATUS, index=1)
            if when != "Just now":
                day, tm = c[1].date_input("Date"), c[2].time_input("Time", value=time(9, 0))
            url = st.text_input("Link to the post (optional)")
            note = st.text_input("Note (optional)")
            if st.form_submit_button("Save", type="primary"):
                if not picked:
                    st.warning("Select at least one group.")
                else:
                    ts = (datetime.now() if when == "Just now" else datetime.combine(day, tm)).strftime(FMT)
                    ids = dict(zip(gs.name, gs.id))
                    db.many("INSERT INTO post_log(listing_id,group_id,posted_at,status,post_url,notes) VALUES(?,?,?,?,?,?)",
                            [(int(lid), int(ids[n]), ts, status, url, note) for n in picked])
                    done(f"Logged {len(picked)} post(s)")

    st.subheader("Where is each listing posted?")
    lid = st.selectbox("Listing", ls.id, format_func=title_of.get, key="cov")
    cov = db.q("""SELECT g.name AS "Group", COUNT(p.id) AS "Times posted", MAX(p.posted_at) AS last_at
                  FROM fb_groups g LEFT JOIN post_log p ON p.group_id=g.id AND p.listing_id=? AND p.status='posted'
                  WHERE g.active=1 GROUP BY g.id""", (int(lid),))
    days = (datetime.now() - pd.to_datetime(cov["last_at"])).dt.days
    cov["Last posted"] = days.map(lambda d: "never" if pd.isna(d) else "today" if d == 0 else f"{int(d)} days ago")
    cov = cov.assign(_d=days.fillna(10**6)).sort_values("_d", ascending=False)
    st.caption(f"Not posted yet in {int((cov['Times posted'] == 0).sum())} of {len(cov)} groups. Never and oldest are at the top.")
    st.dataframe(cov[["Group", "Times posted", "Last posted"]], hide_index=True, width="stretch")

    st.subheader("History")
    h = db.q("""SELECT p.id, p.posted_at, l.title AS listing, g.name AS grp, p.status, p.post_url, p.notes
                FROM post_log p LEFT JOIN listings l ON l.id=p.listing_id LEFT JOIN fb_groups g ON g.id=p.group_id
                ORDER BY p.posted_at DESC LIMIT 300""")
    h["delete"] = False
    ed = st.data_editor(h, key="hist", hide_index=True, width="stretch", disabled=["id", "posted_at", "listing", "grp"],
                        column_config={"status": sel(POST_STATUS), "grp": "group", "post_url": "link"})
    if st.button("Save changes", key="save_hist"):
        db.many("DELETE FROM post_log WHERE id=?", [(r.id,) for r in ed.itertuples() if r.delete])
        db.many("UPDATE post_log SET status=?,post_url=?,notes=? WHERE id=?",
                [(r.status, r.post_url, r.notes, r.id) for r in ed.itertuples() if not r.delete])
        done("History updated")

    with st.expander("Schedule through Meta Business Suite (own computer only)", icon=":material/smart_toy:"):
        if locked():
            return
        opts = {"(none)": None, **{f"#{r.id} {r.title}": r for r in ls.itertuples()}}
        pick = opts[st.selectbox("Listing", list(opts), key="q_l")]
        body = st.text_area("Post text", f"{pick.title}\nPrice: {pick.price:,.0f}\nMessage us to book a viewing!" if pick else "", height=120)
        c = st.columns(3)
        day = c[0].date_input("Date", datetime.now().date() + timedelta(days=1), key="q_d")
        tm = c[1].time_input("Time", time(18, 0), key="q_t")
        img = c[2].file_uploader("Image (optional)", type=["png", "jpg", "jpeg"])
        if st.button("Queue post") and body.strip():
            path = None
            if img:
                up = db.ROOT / "data" / "uploads"
                up.mkdir(parents=True, exist_ok=True)
                path = str(up / img.name)
                open(path, "wb").write(img.getbuffer())
            db.run("INSERT INTO posts(listing_id,body,image_path,scheduled_for) VALUES(?,?,?,?)",
                   (pick.id if pick else None, body, path, datetime.combine(day, tm).strftime(FMT)))
            done("Post queued")
        st.dataframe(db.q("SELECT id,scheduled_for,status,body,note FROM posts ORDER BY scheduled_for DESC"),
                     hide_index=True, width="stretch")
        c = st.columns(3)
        auto = c[2].checkbox("Auto-click Schedule (off = I review in the browser)")
        if c[0].button("Log in to Meta (once)"):
            bot("login")
        if c[1].button("Send queued posts"):
            bot("publish", *(["--auto"] if auto else []))


def settings():
    header("manage", "Settings and backup", "Your details, and a copy of your data you can keep.")
    with st.form("profile"):
        a = st.text_input("Your name", db.setting("agent"))
        p = st.text_input("Phone / Viber / WhatsApp", db.setting("phone"))
        st.caption("These fill {agent} and {phone} in Quick copy.")
        if st.form_submit_button("Save details", type="primary"):
            db.set_setting("agent", a)
            db.set_setting("phone", p)
            done("Details saved")

    st.subheader("Backup")
    st.warning("Streamlit Cloud erases saved files when the app restarts or is redeployed. "
               "Download a backup regularly, and restore it here if your data disappears.")
    st.download_button("Download backup", db.DB_PATH.read_bytes(), f"mosr-backup-{datetime.now():%Y%m%d}.db",
                       "application/octet-stream", icon=":material/download:")
    up = st.file_uploader("Restore from a backup (.db)", type="db")
    if up and st.button("Restore backup (replaces current data)"):
        try:
            db.restore(up.getvalue())
            done("Backup restored")
        except ValueError as e:
            st.error(str(e))

    with st.expander("Sample data and reset"):
        if st.button("Load sample data"):
            db.seed_demo()
            done("Sample data loaded")
        sure = st.checkbox("I understand this deletes all listings, groups, posts, inquiries and appointments")
        if st.button("Delete all data", disabled=not sure):
            db.wipe()
            done("All data deleted")


# =============================================================== 2 Organize
def inbox():
    header("organize", "Inbox", "Every inquiry in one place, tagged and tracked until it's closed.")
    with st.expander("Log an inquiry (comment, Messenger, call)", icon=":material/add:"):
        with st.form("inq", clear_on_submit=True):
            c = st.columns(2)
            who, src = c[0].text_input("From"), c[1].selectbox("Source", SOURCES)
            msg = st.text_area("Their message")
            if st.form_submit_button("Save inquiry", type="primary") and who and msg:
                db.add_inquiry(who, msg, uniq=uuid.uuid4().hex, source=src)
                done("Inquiry logged")

    inq = db.q("SELECT id,received_at,sender,source,message,category,status FROM inquiries ORDER BY received_at DESC")
    if inq.empty:
        st.info("No inquiries yet. Log the first one above.")
    else:
        f = st.columns(2)
        sts = f[0].multiselect("Status", INQ_STATUS, default=["new", "replied"])
        cats = sorted(inq.category.unique())
        cat = f[1].multiselect("Category", cats, default=cats)
        view = inq[inq.status.isin(sts) & inq.category.isin(cat)]
        ed = st.data_editor(view, key="inq_ed", hide_index=True, width="stretch", column_config={"status": sel(INQ_STATUS)},
                            disabled=["id", "received_at", "sender", "source", "message", "category"])
        if st.button("Save status changes"):
            db.many("UPDATE inquiries SET status=? WHERE id=?", [(r.status, r.id) for r in ed.itertuples()])
            done("Statuses updated")

    st.subheader("Quick reply")
    sn = db.q("SELECT title,body FROM snippets WHERE category='Replies' ORDER BY id")
    if sn.empty:
        st.caption("Add snippets in the “Replies” category under Quick copy to see them here.")
    else:
        ls = listings()
        title_of = dict(zip(ls.id, ls.title))
        c = st.columns(2)
        lid = c[0].selectbox("About listing", [None, *ls.id], format_func=lambda i: "(none)" if i is None else title_of[i])
        pick = c[1].selectbox("Reply", sn.index, format_func=lambda i: sn.title[i])
        st.code(fill(sn.body[pick], fillers(lid)), language=None, wrap_lines=True)

    with st.expander("Sync the Meta inbox (own computer only)", icon=":material/smart_toy:"):
        if not locked() and st.button("Sync Meta inbox"):
            bot("inbox")


def quick_copy():
    header("organize", "Quick copy", "Your usual answers and post templates, ready to paste.")
    ls = listings()
    title_of = dict(zip(ls.id, ls.title))
    c = st.columns(2)
    lid = c[0].selectbox("Fill in for listing (optional)", [None, *ls.id],
                         format_func=lambda i: "(none)" if i is None else title_of[i])
    term = c[1].text_input("Search", placeholder="price, viewing, reservation...")
    vals = fillers(lid)
    sn = db.q("SELECT category,title,body FROM snippets ORDER BY category, id")
    if term:
        sn = sn[sn.title.str.contains(term, case=False, regex=False) | sn.body.str.contains(term, case=False, regex=False)]
    st.caption("Open a section, then click the copy icon at the top-right of any block.")
    for cat, g in sn.groupby("category", sort=False):
        with st.expander(f"{cat} ({len(g)})", expanded=bool(term)):
            for r in g.itertuples():
                st.caption(r.title)
                st.code(fill(r.body, vals), language=None, wrap_lines=True)
    if sn.empty:
        st.info("No snippets match.")

    with st.expander("Edit snippets", icon=":material/edit:"):
        st.caption("Use {agent}, {phone}, {listing} and {price} to fill details automatically. Add rows at the bottom; select a row and press Delete to remove it.")
        ed = st.data_editor(db.q("SELECT category,title,body FROM snippets ORDER BY category, id"), key="snip_ed",
                            num_rows="dynamic", hide_index=True, width="stretch")
        if st.button("Save snippets"):
            db.save_snippets(ed)
            done("Snippets saved")


# =============================================================== 3 Set
def calendar_page():
    header("set", "Calendar", "Set viewings and see them next to your planned posts.")
    ap = db.q("""SELECT a.*, l.title AS ltitle FROM appointments a LEFT JOIN listings l ON l.id=a.listing_id
                 WHERE a.status!='cancelled'""")
    pl = db.q("""SELECT p.id, p.posted_at, l.title AS ltitle, g.name AS grp FROM post_log p
                 LEFT JOIN listings l ON l.id=p.listing_id LEFT JOIN fb_groups g ON g.id=p.group_id
                 WHERE p.status='planned'""")
    ev = []
    for r in ap.itertuples():
        s = datetime.fromisoformat(r.start_at)
        ev.append({"id": f"a{r.id}", "title": f"{r.client}: {r.ltitle or r.title}", "start": s.isoformat(),
                   "end": (s + timedelta(hours=1)).isoformat(), "color": "#B7791F" if r.status == "pending" else "#9CA3AF"})
    for r in pl.itertuples():
        s = datetime.fromisoformat(r.posted_at)
        ev.append({"id": f"p{r.id}", "title": f"Post: {r.ltitle} to {r.grp}", "start": s.isoformat(),
                   "end": (s + timedelta(minutes=15)).isoformat(), "color": "#0F766E"})

    with st.expander("Add appointment", expanded=ap.empty, icon=":material/add:"):
        inq = db.q("SELECT id,sender FROM inquiries WHERE status IN ('new','replied') ORDER BY received_at DESC")
        ls = listings()
        who_of, title_of = dict(zip(inq.id, inq.sender)), dict(zip(ls.id, ls.title))
        with st.form("appt", clear_on_submit=True):
            who = st.selectbox("From inquiry", [None, *inq.id],
                               format_func=lambda i: "(walk-in / manual)" if i is None else f"#{i} {who_of[i]}")
            lid = st.selectbox("Listing", [None, *ls.id], format_func=lambda i: "(none)" if i is None else title_of[i])
            client = st.text_input("Client (leave blank to use the inquiry sender)")
            title = st.text_input("What", "Property viewing")
            c = st.columns(2)
            day = c[0].date_input("Date", datetime.now().date() + timedelta(days=1))
            tm = c[1].time_input("Time", time(10, 0))
            loc, notes = st.text_input("Meeting place"), st.text_area("Notes")
            if st.form_submit_button("Set appointment", type="primary"):
                start = datetime.combine(day, tm)
                clash = db.q("SELECT client FROM appointments WHERE status='pending' AND start_at BETWEEN ? AND ?",
                             ((start - timedelta(minutes=59)).strftime(FMT), (start + timedelta(minutes=59)).strftime(FMT)))
                db.run("INSERT INTO appointments(inquiry_id,listing_id,client,title,start_at,location,notes) VALUES(?,?,?,?,?,?,?)",
                       (int(who) if who else None, int(lid) if lid else None, client or who_of.get(who, "Unknown"),
                        title, start.strftime(FMT), loc, notes))
                if who:
                    db.run("UPDATE inquiries SET status='scheduled' WHERE id=?", (int(who),))
                done("Appointment set" + (f". Heads up: it overlaps with {clash.client[0]}" if len(clash) else ""))

    st.markdown("<span class='dot' style='background:#B7791F'></span>Viewing &nbsp;&nbsp;"
                "<span class='dot' style='background:#0F766E'></span>Planned post &nbsp;&nbsp;"
                "<span class='dot' style='background:#9CA3AF'></span>Done. Click an event to update it.", unsafe_allow_html=True)
    if fullcalendar:
        state = fullcalendar(events=ev, key="cal", options={
            "initialView": "dayGridMonth", "height": 640,
            "headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth,timeGridWeek,listWeek"},
            "eventTimeFormat": {"hour": "numeric", "minute": "2-digit", "meridiem": "short"}})
        click = ((state or {}).get("eventClick") or {}).get("event")
        if click:
            kind, rid = click["id"][0], int(click["id"][1:])
            with st.container(border=True):
                st.markdown(f"**{click['title']}**  \n{click['start'][:16].replace('T', ' ')}")
                if kind == "a":
                    r = db.q("SELECT * FROM appointments WHERE id=?", (rid,))
                    if len(r):
                        st.write(f"Where: {r.location[0] or '-'}  \nNotes: {r.notes[0] or '-'}  \nStatus: {r.status[0]}")
                        b = st.columns(3)
                        for col, label, new in zip(b, ["Done", "Reopen", "Cancel"], ["done", "pending", "cancelled"]):
                            if col.button(label, key=f"ap_{new}"):
                                db.run("UPDATE appointments SET status=? WHERE id=?", (new, rid))
                                done(f"Marked {new}")
                elif st.button("Mark as posted"):
                    db.run("UPDATE post_log SET status='posted', posted_at=? WHERE id=?", (db.NOW(), rid))
                    done("Marked as posted")
    else:
        st.warning("Add `streamlit-calendar` to requirements.txt for the month view.")

    st.subheader("Upcoming viewings")
    up = ap[(ap.status == "pending") & (ap.start_at >= datetime.now().strftime(FMT))].sort_values("start_at")
    if up.empty:
        st.caption("No upcoming viewings.")
    else:
        st.dataframe(up[["start_at", "client", "ltitle", "location", "notes"]].rename(columns={"ltitle": "listing"}),
                     hide_index=True, width="stretch")


# =============================================================== 4 Remind
ACTIONS = {
    "appt": ("Mark done", "UPDATE appointments SET status='done' WHERE id=?"),
    "post": ("Mark posted", "UPDATE post_log SET status='posted', posted_at=datetime('now','localtime') WHERE id=?"),
    "inq": ("Mark replied", "UPDATE inquiries SET status='replied' WHERE id=?"),
}


def reminders():
    header("remind", "Reminders", "Everything that needs you right now, most urgent first.")
    if st.button("Run reminder check", icon=":material/refresh:"):
        st.toast(f"{db.run_reminders()} new reminder(s) logged")
    now = datetime.now()
    hrs = lambda s: (datetime.fromisoformat(s) - now).total_seconds() / 3600
    bucket = lambda h: "Overdue" if h < 0 else "Within the hour" if h <= 1 else "Next 24 hours" if h <= 24 else None
    groups = {k: [] for k in ["Overdue", "Within the hour", "Next 24 hours", "Unanswered for over 24 hours", "Groups gone quiet"]}

    for r in db.q("""SELECT a.*, l.title AS ltitle FROM appointments a LEFT JOIN listings l ON l.id=a.listing_id
                     WHERE a.status='pending'""").itertuples():
        if b := bucket(hrs(r.start_at)):
            groups[b].append(("appt", r.id, f"Viewing with {r.client}: {r.ltitle or r.title}", r.start_at))
    for r in db.q("""SELECT p.id, p.posted_at, l.title AS ltitle, g.name AS grp FROM post_log p
                     LEFT JOIN listings l ON l.id=p.listing_id LEFT JOIN fb_groups g ON g.id=p.group_id
                     WHERE p.status='planned'""").itertuples():
        if b := bucket(hrs(r.posted_at)):
            groups[b].append(("post", r.id, f"Post {r.ltitle} to {r.grp}", r.posted_at))
    for r in db.q("SELECT * FROM inquiries WHERE status='new' ORDER BY received_at").itertuples():
        waited = -hrs(r.received_at)
        if waited > 24:
            groups["Unanswered for over 24 hours"].append(("inq", r.id, f"{r.sender}: {str(r.message)[:90]}", f"waiting {int(waited)} hours"))
    last = db.q("""SELECT g.name, MAX(p.posted_at) AS last_at FROM fb_groups g
                   LEFT JOIN post_log p ON p.group_id=g.id AND p.status='posted' WHERE g.active=1 GROUP BY g.id""")
    for r in last.itertuples():
        d = None if r.last_at is None else (now - datetime.fromisoformat(r.last_at)).days
        if d is None or d >= 7:
            groups["Groups gone quiet"].append(("grp", r.name, r.name, "never posted in" if d is None else f"last post {d} days ago"))

    if not any(groups.values()):
        st.success("Nothing needs you right now.")
    for name, items in groups.items():
        if not items:
            continue
        st.subheader(f"{name} ({len(items)})")
        for kind, rid, text, when in items:
            with st.container(border=True):
                a, b = st.columns([5, 1], vertical_alignment="center")
                a.markdown(f"**{html.escape(str(text))}**  \n<span class='muted'>{html.escape(str(when))}</span>", unsafe_allow_html=True)
                if kind in ACTIONS and b.button(ACTIONS[kind][0], key=f"rm_{kind}_{rid}"):
                    db.run(ACTIONS[kind][1], (rid,))
                    done("Updated")

    with st.expander("Reminder log"):
        st.caption(f"Due items are also written to {db.LOG_PATH.relative_to(db.ROOT)}: 24h and 1h before viewings and posts, "
                   "and when an inquiry has waited over 24 hours.")
        st.dataframe(db.q("SELECT created_at,kind,tag,message FROM reminder_logs ORDER BY id DESC LIMIT 200"),
                     hide_index=True, width="stretch")
