Amazon Price Error Monitor

Keepa to Discord price-error alerts for Amazon-60, Amazon-70, Amazon-80, and Amazon-90 channels.


What you need

- Keepa API key
- 4 Discord webhook URLs (one per channel)
- For local use: Windows PC + Python 3 (Node.js is not required)
- For cloud: a Render account (paid/Starter recommended so the service does not sleep)


A. Run on your Windows PC

1. Install Python (Windows)

Direct download (64-bit installer):

https://www.python.org/ftp/python/3.14.6/python-3.14.6-amd64.exe

Or open the official downloads page and use the yellow Download button:

https://www.python.org/downloads/

Important during install:

1. Run the installer.
2. On the first screen, check the box at the bottom that says: Add python.exe to PATH
3. Click Install Now.
4. When finished, close the installer.

If you skip Add to PATH, run_ui.bat will not find Python. Re-run the installer with that box checked, or reinstall.

Quick check: open a new Command Prompt and type:

python --version

You should see something like Python 3.14.x.


2. Start the monitor (important)

Only run this file:

run_ui.bat

Do not double-click web_ui.py or main.py. Those will fail with errors like "No module named fastapi".

Steps:

1. Unzip this folder.
2. Double-click run_ui.bat
3. Wait while packages install (first run needs internet, may take 1-2 minutes).
4. Browser opens http://127.0.0.1:8787
5. Leave the black command window open while using the monitor.

If you already saw "No module named fastapi":

1. Delete the venv folder inside this project (if it exists).
2. Double-click run_ui.bat again and wait for install to finish.


3. Configure in the UI

1. Paste your Keepa API key, then Save.
2. Paste the 4 Discord webhooks (Amazon-60 / 70 / 80 / 90), then Save.
3. Optional: Send test alert (uses Amazon-60 only, no Keepa tokens).
4. Adjust scan settings if needed, then Save.
5. Click Start monitor.


B. Deploy on Render (always-on monitoring)

Use a Render Web Service so the monitor keeps running in the cloud.

1. Push this project to a GitHub repo (do not commit .env, venv, or secrets).
2. In Render: New → Blueprint, connect the repo (uses render.yaml), or New → Web Service.
3. Settings used by render.yaml:
   - Build: pip install -r requirements.txt
   - Start: uvicorn web_ui:app --host 0.0.0.0 --port $PORT
   - Health check: /health
   - Disk: mount at /var/data (keeps alert history)
   - Env DATA_DIR=/var/data
4. In Render Environment, set at least:
   - KEEPA_API_KEY
   - DISCORD_WEBHOOK_60
   - DISCORD_WEBHOOK_70
   - DISCORD_WEBHOOK_80
   - DISCORD_WEBHOOK_90
   - UI_PASSWORD (recommended — locks the public dashboard)
5. Choose a plan that does not sleep (Starter or higher). Free tier sleeps and stops monitoring.
6. After deploy, open your Render URL.
   - If UI_PASSWORD is set, log in first.
   - If env webhooks + Keepa key are set, the monitor auto-starts on boot.
   - You can still use Start / Stop / Run 1 scan in the UI.


Notes

- Local: keep the PC online while the monitor is running.
- Render: keep the Web Service running; use a persistent disk so same-day no-repeat history survives restarts.
- Discord webhooks and API keys should live in Render env vars or the UI — never commit them to git.
- Node.js is not required for this app.
