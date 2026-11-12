"""MOSR: Manage, Organize, Set, Remind. Run locally: streamlit run app.py"""
import hmac

import streamlit as st

import db, views

st.set_page_config(page_title="MOSR Real Estate Hub", page_icon="🏡", layout="wide")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:wght@600;700&family=Manrope:wght@400;500;600;700&display=swap');
.stApp :is(h1,h2,h3,h4,p,li,label,div,input,textarea,button,td,th) { font-family: 'Manrope', system-ui, sans-serif; }
.stApp :is(h2,h3), .hd h1, [data-testid="stMetricValue"] { font-family: 'Bricolage Grotesque', 'Manrope', sans-serif; letter-spacing: -0.01em; }
.block-container { padding-top: 2.2rem; max-width: 1180px; }
.hd { border-left: 5px solid var(--page); padding: .15rem 0 .15rem 1rem; margin: 0 0 1.4rem; }
.hd .chip { background: var(--page); color: #fff; font-size: .8rem; font-weight: 600; padding: .15rem .7rem; border-radius: 999px; }
.hd h1 { margin: .55rem 0 .15rem; padding: 0; font-size: 2.1rem; line-height: 1.15; }
.hd p { margin: 0; color: #5B6B68; font-size: 1rem; }
[data-testid="stMetric"] { background: #fff; border: 1px solid #E1E8E6; border-top: 3px solid var(--page); border-radius: 8px; padding: .8rem 1rem; }
[data-testid="stMetricValue"] { font-weight: 700; }
[data-testid="stExpander"] { border-color: #E1E8E6; border-radius: 8px; }
.stApp h3 { margin-top: 1.4rem; }
.muted { color: #5B6B68; font-size: .9rem; }
.dot { display: inline-block; width: .65rem; height: .65rem; border-radius: 50%; margin-right: .4rem; }
</style>
"""


def gate():
    """Optional password: set APP_PASSWORD in Streamlit Cloud > Settings > Secrets."""
    try:
        pw = st.secrets.get("APP_PASSWORD")
    except Exception:
        pw = None
    if not pw or st.session_state.get("ok"):
        return
    st.title("MOSR")
    if hmac.compare_digest(st.text_input("Password", type="password").encode(), str(pw).encode()):
        st.session_state["ok"] = True
        st.rerun()
    st.stop()


st.markdown(CSS, unsafe_allow_html=True)
st.logo("assets/logo.svg", size="large")
gate()
db.init()
db.run_reminders()  # REMIND runs on every load, so due items always reach the log
if msg := st.session_state.pop("_flash", None):
    st.toast(msg)

st.navigation({
    "": [st.Page(views.dashboard, title="Dashboard", icon=":material/dashboard:", url_path="dashboard", default=True)],
    "MANAGE": [
        st.Page(views.library, title="Listings & Groups", icon=":material/home_work:", url_path="listings"),
        st.Page(views.tracker, title="Post Tracker", icon=":material/campaign:", url_path="posts"),
        st.Page(views.settings, title="Settings & Backup", icon=":material/settings:", url_path="settings"),
    ],
    "ORGANIZE": [
        st.Page(views.inbox, title="Inbox", icon=":material/inbox:", url_path="inbox"),
        st.Page(views.quick_copy, title="Quick Copy", icon=":material/content_copy:", url_path="copy"),
    ],
    "SET": [st.Page(views.calendar_page, title="Calendar", icon=":material/event:", url_path="calendar")],
    "REMIND": [st.Page(views.reminders, title="Reminders", icon=":material/notifications:", url_path="reminders")],
}).run()
