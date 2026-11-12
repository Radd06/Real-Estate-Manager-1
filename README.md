# MOSR Real Estate Hub

Track where you posted, schedule viewings, and copy-paste your usual replies.

| Step | Pages |
|---|---|
| Dashboard | Inquiry stats and the next 3 days |
| MANAGE | Listings & Groups, Post Tracker, Settings & Backup |
| ORGANIZE | Inbox, Quick Copy |
| SET | Calendar |
| REMIND | Reminders (live to-do list plus the reminder log) |

## Deploy on Streamlit Community Cloud
1. Push this folder to a GitHub repo (the `data/` folder is git-ignored on purpose; it holds client names).
2. At share.streamlit.io choose **New app**, pick the repo, set the main file to `app.py`.
3. Optional, recommended: in **Settings > Secrets** add `APP_PASSWORD = "your-password"`.
   You can also restrict viewers under **Share** to keep the app private.
4. Optional: timezone defaults to Asia/Manila. Change it with the env var `MOSR_TZ`.

## Important: saving your data
Streamlit Cloud wipes local files when the app restarts or redeploys. Use **Settings > Download backup**
regularly and **Restore** after a reset. For permanent storage, move `db.py` to a hosted database
(Supabase, Turso) later; the rest of the app does not need to change.

## Run locally
    pip install -r requirements.txt
    streamlit run app.py

## Business Suite automation (fb_bot.py), own computer only
The original Playwright bot is still wired in (Post Tracker > Schedule through Meta Business Suite, and Inbox > Sync).
It needs a visible browser, so on Streamlit Cloud those panels just explain that. To use them locally:

    pip install -r requirements-local.txt
    playwright install chromium
    MOSR_LOCAL=1 streamlit run app.py      (Windows PowerShell: $env:MOSR_LOCAL=1; streamlit run app.py)
