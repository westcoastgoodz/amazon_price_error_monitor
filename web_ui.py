"""Web UI — scan controls for the Amazon Price Error Monitor.

Local:  python web_ui.py  → http://127.0.0.1:8787
Render: uvicorn web_ui:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
import uvicorn

from config import load_settings
from ui_settings import load_ui_settings, save_ui_settings

_monitor = None
_thread: threading.Thread | None = None
_status = {"running": False, "stopping": False, "last_message": "Idle"}

AUTH_COOKIE = "apem_session"


def _is_running() -> bool:
    return bool(_thread and _thread.is_alive())


def _is_stopping() -> bool:
    return bool(_status.get("stopping")) and _is_running()


def _is_cloud() -> bool:
    """True on Render (or when bound for remote access)."""
    if os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"):
        return True
    host = (os.getenv("HOST") or "").strip()
    return host in ("0.0.0.0", "::")


def _ui_password() -> str:
    """Read UI_PASSWORD from env. Strip spaces and accidental wrapping quotes."""
    raw = os.getenv("UI_PASSWORD")
    if raw is None:
        return ""
    pw = str(raw).strip()
    # Render / copy-paste often stores "secret" or 'secret' with quotes included.
    if len(pw) >= 2 and pw[0] == pw[-1] and pw[0] in ("'", '"'):
        pw = pw[1:-1].strip()
    return pw


def _auth_token(password: str) -> str:
    return hashlib.sha256(f"apem:{password}".encode("utf-8")).hexdigest()


def _password_ok(given: str, expected: str) -> bool:
    a = (given or "").strip().encode("utf-8")
    b = (expected or "").strip().encode("utf-8")
    if not a or not b:
        return False
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def _is_authed(request: Request) -> bool:
    pw = _ui_password()
    if not pw:
        return True
    token = request.cookies.get(AUTH_COOKIE) or ""
    return bool(token and hmac.compare_digest(token, _auth_token(pw)))


def _try_start_monitor(*, message: str = "Started.") -> str:
    """Start background monitor if credentials exist. Returns status code string."""
    global _monitor, _thread
    if _is_running():
        return "already_running"
    from monitor import Monitor

    s = load_settings()
    if not s.keepa_api_key or not s.tiers:
        return "missing_credentials"

    _monitor = Monitor(s)

    def _run() -> None:
        _status["running"] = True
        _status["stopping"] = False
        _status["last_message"] = "Monitor running…"
        try:
            _monitor.run_forever()
        finally:
            _status["running"] = False
            _status["stopping"] = False
            _status["last_message"] = "Stopped."
            try:
                _monitor.close()
            except Exception:
                pass

    _thread = threading.Thread(target=_run, name="apem-monitor", daemon=True)
    _thread.start()
    _status["stopping"] = False
    _status["last_message"] = message
    return "ok"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    result = _try_start_monitor(message="Auto-started on boot.")
    if result == "missing_credentials":
        _status["last_message"] = (
            "Idle — set Keepa API key + Discord webhooks, then Start monitor."
        )
    yield
    if _monitor and _is_running():
        _monitor.request_stop()


app = FastAPI(title="Amazon Price Error Monitor", lifespan=_lifespan)


@app.middleware("http")
async def _password_gate(request: Request, call_next):
    path = request.url.path
    if path in ("/health", "/login", "/favicon.ico"):
        return await call_next(request)
    if not _ui_password() or _is_authed(request):
        return await call_next(request)
    return RedirectResponse("/login", status_code=303)


# Placeholders use __NAME__ so CSS braces stay normal (no .format doubling).
PAGE = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Amazon Price Error Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=Sora:wght@600;700&display=swap" rel="stylesheet"/>
  <style>
    :root, [data-theme="light"] {
      --bg: #f0f2f5;
      --bg-elev: #ffffff;
      --bg-soft: #e8ecf1;
      --border: #8b97a8;
      --border-strong: #5f6b7c;
      --text: #10151c;
      --muted: #4a5565;
      --accent: #ff9900;
      --accent-text: #1a1200;
      --accent-soft: rgba(255, 153, 0, 0.16);
      --ok: #0b7a42;
      --ok-soft: rgba(11, 122, 66, 0.12);
      --danger: #c62828;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.06), 0 10px 28px rgba(16, 24, 40, 0.08);
      --header-bg: rgba(255, 255, 255, 0.94);
      --ring: rgba(255, 153, 0, 0.38);
      --step-line: #8b97a8;
    }
    [data-theme="dark"] {
      --bg: #0e1116;
      --bg-elev: #171b22;
      --bg-soft: #1e2430;
      --border: #556174;
      --border-strong: #7b879c;
      --text: #eef1f5;
      --muted: #a0aec0;
      --accent: #ff9900;
      --accent-text: #1a1200;
      --accent-soft: rgba(255, 153, 0, 0.16);
      --ok: #3dd68c;
      --ok-soft: rgba(61, 214, 140, 0.14);
      --danger: #ff6b5a;
      --shadow: 0 1px 2px rgba(0, 0, 0, 0.4), 0 14px 36px rgba(0, 0, 0, 0.4);
      --header-bg: rgba(14, 17, 22, 0.92);
      --ring: rgba(255, 153, 0, 0.4);
      --step-line: #556174;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; }
    body {
      font-family: "IBM Plex Sans", system-ui, sans-serif;
      background:
        radial-gradient(900px 420px at 8% -8%, var(--accent-soft), transparent 50%),
        var(--bg);
      color: var(--text);
      line-height: 1.45;
    }
    .shell { max-width: 880px; margin: 0 auto; padding: 0 20px 56px; }
    .topbar {
      position: sticky; top: 0; z-index: 20;
      backdrop-filter: blur(14px);
      background: var(--header-bg);
      border-bottom: 2px solid var(--border);
    }
    .topbar-inner {
      max-width: 880px; margin: 0 auto; padding: 16px 20px;
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
    }
    .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
    .brand-mark {
      width: 42px; height: 42px; border-radius: 11px;
      background: linear-gradient(145deg, #ffb84d, #ff9900 55%, #e88700);
      color: #1a1200; font-family: Sora, sans-serif; font-weight: 700;
      display: grid; place-items: center; font-size: 1.1rem;
      border: 2px solid #b86d00;
      flex-shrink: 0;
    }
    .brand h1 {
      font-family: Sora, sans-serif; font-size: 1.08rem; font-weight: 700;
      margin: 0; letter-spacing: -0.02em;
    }
    .brand p { margin: 3px 0 0; font-size: 0.8rem; color: var(--muted); }
    .top-actions { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
    .theme-btn {
      appearance: none; border: 2px solid var(--border); background: var(--bg-elev);
      color: var(--text); border-radius: 999px; padding: 8px 14px; font: inherit;
      font-size: 0.82rem; font-weight: 600; cursor: pointer;
    }
    .theme-btn:hover { border-color: var(--border-strong); }
    .flow-intro { margin: 28px 0 8px; }
    .flow-intro h2 {
      font-family: Sora, sans-serif; font-size: 1.35rem; font-weight: 700;
      margin: 0; letter-spacing: -0.03em;
    }
    .flow-intro p { margin: 8px 0 0; color: var(--muted); font-size: 0.94rem; max-width: 36rem; }
    .flow { margin-top: 20px; display: flex; flex-direction: column; }
    .flow-step {
      display: grid;
      grid-template-columns: 44px 1fr;
      gap: 14px;
      padding-bottom: 22px;
    }
    .flow-step:last-child { padding-bottom: 0; }
    .rail { position: relative; display: flex; flex-direction: column; align-items: center; }
    .step-num {
      width: 36px; height: 36px; border-radius: 50%;
      background: var(--bg-elev); border: 2px solid var(--border-strong);
      color: var(--text); font-family: Sora, sans-serif; font-weight: 700; font-size: 0.9rem;
      display: grid; place-items: center; z-index: 1;
      box-shadow: var(--shadow);
    }
    .flow-step.is-run .step-num {
      background: var(--accent); border-color: #b86d00; color: var(--accent-text);
    }
    .rail::after {
      content: "";
      flex: 1;
      width: 2px;
      margin-top: 6px;
      background: var(--step-line);
      min-height: 28px;
      border-radius: 2px;
    }
    .flow-step:last-child .rail::after { display: none; }
    .panel {
      background: var(--bg-elev);
      border: 2px solid var(--border);
      border-radius: 16px;
      padding: 20px 22px 22px;
      box-shadow: var(--shadow);
    }
    .panel-head {
      display: flex; align-items: baseline; justify-content: space-between; gap: 10px;
      margin-bottom: 14px; padding-bottom: 12px;
      border-bottom: 2px solid var(--border);
    }
    .panel-head h3 {
      font-family: Sora, sans-serif; font-size: 1.02rem; font-weight: 700;
      margin: 0; letter-spacing: -0.02em;
    }
    .panel-head .tag {
      font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
      color: var(--muted); border: 2px solid var(--border); border-radius: 999px; padding: 3px 9px;
    }
    .lead { margin: 0 0 16px; color: var(--muted); font-size: 0.88rem; }
    .status-strip {
      display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap;
      padding: 12px 14px; border-radius: 12px; background: var(--bg-soft);
      border: 2px solid var(--border); margin-bottom: 16px;
    }
    .status-strip .msg { margin: 0; font-size: 0.92rem; font-weight: 500; }
    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 5px 11px; border-radius: 999px; font-size: 0.75rem; font-weight: 700;
      border: 2px solid var(--border);
    }
    .pill::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
    .on { background: var(--ok-soft); color: var(--ok); border-color: var(--ok); }
    .off { background: var(--bg-elev); color: var(--muted); }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 16px; }
    @media (max-width: 720px) {
      .grid { grid-template-columns: 1fr; }
      .flow-step { grid-template-columns: 32px 1fr; gap: 10px; }
      .step-num { width: 30px; height: 30px; font-size: 0.8rem; }
    }
    label.field { display: flex; flex-direction: column; gap: 6px; }
    label.field > span { font-size: 0.8rem; font-weight: 700; color: var(--muted); }
    input, select {
      width: 100%; padding: 11px 12px; border-radius: 10px;
      border: 2px solid var(--border); background: #fafbfc;
      color: var(--text); font: inherit; font-size: 0.95rem;
    }
    [data-theme="dark"] input, [data-theme="dark"] select { background: var(--bg-soft); }
    input:focus, select:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--ring); }
    .hint { font-size: 0.74rem; color: var(--muted); margin: 0; }
    .check {
      display: flex; align-items: flex-start; gap: 10px; margin-top: 14px;
      padding: 12px 14px; border-radius: 12px; background: var(--bg-soft);
      border: 2px solid var(--border);
    }
    .check input { width: auto; margin-top: 3px; accent-color: var(--accent); }
    .check span { font-size: 0.9rem; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
    button {
      appearance: none; border: 0; border-radius: 10px; padding: 11px 16px;
      font: inherit; font-weight: 700; cursor: pointer; display: inline-flex; align-items: center;
    }
    button:disabled { opacity: 0.45; cursor: not-allowed; }
    .primary { background: var(--accent); color: var(--accent-text); border: 2px solid #b86d00; }
    .primary:hover:not(:disabled) { filter: brightness(0.97); }
    .danger { background: var(--danger); color: #fff; border: 2px solid transparent; }
    .ghost { background: var(--bg-elev); border: 2px solid var(--border); color: var(--text); }
    .ghost:hover:not(:disabled) { border-color: var(--border-strong); }
    .tier-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }
    @media (max-width: 720px) { .tier-grid { grid-template-columns: 1fr; } }
    .tier {
      border: 2px solid var(--border); border-radius: 12px; padding: 13px 14px;
      background: var(--bg-soft); display: flex; justify-content: space-between; gap: 10px; align-items: center;
    }
    .tier strong { display: block; font-size: 0.95rem; }
    .tier small { display: block; color: var(--muted); font-size: 0.78rem; margin-top: 2px; }
    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.78rem; background: var(--bg-soft); border: 1.5px solid var(--border);
      padding: 2px 7px; border-radius: 6px;
    }
    .foot-note { margin: 12px 0 0; font-size: 0.78rem; color: var(--muted); }
    details.secret-box {
      border: 2px solid var(--border);
      border-radius: 12px;
      background: var(--bg-soft);
      padding: 0;
      overflow: hidden;
    }
    details.secret-box > summary {
      list-style: none;
      cursor: pointer;
      padding: 12px 14px;
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      user-select: none;
    }
    details.secret-box > summary::-webkit-details-marker { display: none; }
    details.secret-box > summary::after {
      content: "Show";
      font-size: 0.75rem;
      font-weight: 700;
      color: var(--muted);
      border: 2px solid var(--border);
      border-radius: 999px;
      padding: 3px 10px;
      background: var(--bg-elev);
    }
    details.secret-box[open] > summary::after { content: "Hide"; }
    .secret-body { padding: 0 14px 14px; border-top: 2px solid var(--border); padding-top: 14px; }
    .key-row { display: flex; gap: 8px; align-items: stretch; }
    .key-row input { flex: 1; }
    .webhook-list { display: flex; flex-direction: column; gap: 12px; }
    .webhook-item {
      border: 2px solid var(--border);
      border-radius: 12px;
      padding: 12px 14px;
      background: var(--bg-soft);
    }
    .webhook-item .wh-top {
      display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 8px;
    }
    .webhook-item strong { font-size: 0.92rem; }
    .webhook-item small { color: var(--muted); font-size: 0.78rem; }
    .webhook-item.disabled-ch {
      opacity: 0.72;
      border-style: dashed;
    }
    .ch-enable {
      display: inline-flex; align-items: center; gap: 8px;
      font-size: 0.82rem; font-weight: 700; color: var(--text);
      white-space: nowrap;
    }
    .ch-enable input { width: auto; accent-color: var(--accent); }
    input.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; }
  </style>
  <script>
    (function () {
      var saved = localStorage.getItem("apem-theme");
      if (saved === "dark" || saved === "light") document.documentElement.setAttribute("data-theme", saved);
    })();
    function toggleTheme() {
      var html = document.documentElement;
      var next = html.getAttribute("data-theme") === "dark" ? "light" : "dark";
      html.setAttribute("data-theme", next);
      localStorage.setItem("apem-theme", next);
      var btn = document.getElementById("themeBtn");
      if (btn) btn.textContent = next === "dark" ? "Light mode" : "Dark mode";
    }
    document.addEventListener("DOMContentLoaded", function () {
      var btn = document.getElementById("themeBtn");
      if (!btn) return;
      btn.textContent = document.documentElement.getAttribute("data-theme") === "dark" ? "Light mode" : "Dark mode";
    });
  </script>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">a</div>
        <div>
          <h1>Amazon Price Error Monitor</h1>
          <p>Keepa → Discord price-error alerts</p>
        </div>
      </div>
      <div class="top-actions">
        <span class="pill __STATUS_CLASS__">__STATUS_LABEL__</span>
        <button type="button" class="theme-btn" id="themeBtn" onclick="toggleTheme()">Dark mode</button>
      </div>
    </div>
  </header>

  <div class="shell">
    <div class="flow-intro">
      <h2>Monitor setup</h2>
      <p>Add your Keepa key and Discord webhooks here, tune scan rules, then start monitoring. You can change them anytime.</p>
    </div>

    <div class="flow">
      <div class="flow-step">
        <div class="rail"><div class="step-num">1</div></div>
        <form class="panel" method="post" action="/save-key">
          <div class="panel-head">
            <h3>Keepa API key</h3>
            <span class="tag">Credentials</span>
          </div>
          <p class="lead">Stored locally on this PC. Overrides <code>.env</code> when saved here.</p>
          <details class="secret-box">
            <summary>API key (__KEY_STATUS__)</summary>
            <div class="secret-body">
              <label class="field">
                <span>Keepa API key</span>
                <div class="key-row">
                  <input class="mono" id="keepaKey" type="text" name="keepa_api_key" value="" placeholder="__KEEPA_PLACEHOLDER__" autocomplete="off" spellcheck="false"/>
                </div>
                <p class="hint">__KEEPA_HINT__ Paste a new key only when changing it (blank Save clears UI override).</p>
              </label>
              <div class="actions">
                <button class="primary" type="submit">Save API key</button>
              </div>
            </div>
          </details>
        </form>
      </div>

      <div class="flow-step">
        <div class="rail"><div class="step-num">2</div></div>
        <form class="panel" method="post" action="/save-webhooks">
          <div class="panel-head">
            <h3>Discord webhooks</h3>
            <span class="tag">Channels</span>
          </div>
          <p class="lead">Paste each channel webhook. Uncheck a channel to pause alerts for that group (webhook stays saved).</p>
          <div class="webhook-list">
            <div class="webhook-item __DIS50__">
              <div class="wh-top">
                <label class="ch-enable">
                  <input type="checkbox" name="channel_enabled_50" value="1" __EN50__/>
                  <span><strong>Amazon-50</strong> <small>50–59% off</small></span>
                </label>
                __W50__
              </div>
              <input class="mono" type="url" name="discord_webhook_50" value="__WH50__" placeholder="https://discord.com/api/webhooks/..."/>
            </div>
            <div class="webhook-item __DIS60__">
              <div class="wh-top">
                <label class="ch-enable">
                  <input type="checkbox" name="channel_enabled_60" value="1" __EN60__/>
                  <span><strong>Amazon-60</strong> <small>60–69% off</small></span>
                </label>
                __W60__
              </div>
              <input class="mono" type="url" name="discord_webhook_60" value="__WH60__" placeholder="https://discord.com/api/webhooks/..."/>
            </div>
            <div class="webhook-item __DIS70__">
              <div class="wh-top">
                <label class="ch-enable">
                  <input type="checkbox" name="channel_enabled_70" value="1" __EN70__/>
                  <span><strong>Amazon-70</strong> <small>70–79% off</small></span>
                </label>
                __W70__
              </div>
              <input class="mono" type="url" name="discord_webhook_70" value="__WH70__" placeholder="https://discord.com/api/webhooks/..."/>
            </div>
            <div class="webhook-item __DIS80__">
              <div class="wh-top">
                <label class="ch-enable">
                  <input type="checkbox" name="channel_enabled_80" value="1" __EN80__/>
                  <span><strong>Amazon-80</strong> <small>80–89% off</small></span>
                </label>
                __W80__
              </div>
              <input class="mono" type="url" name="discord_webhook_80" value="__WH80__" placeholder="https://discord.com/api/webhooks/..."/>
            </div>
          </div>
          <div class="actions">
            <button class="primary" type="submit">Save webhooks</button>
            <button class="ghost" type="submit" formaction="/test-alert">Send test alert</button>
          </div>
          <p class="hint">Test alert goes to Amazon-50 only (sample embed, no Keepa tokens).</p>
        </form>
      </div>

      <div class="flow-step">
        <div class="rail"><div class="step-num">3</div></div>
        <form class="panel" method="post" action="/save">
          <div class="panel-head">
            <h3>Configure scan</h3>
            <span class="tag">Settings</span>
          </div>
          <div class="status-strip">
            <p class="msg">__LAST_MESSAGE__</p>
            <span class="pill __STATUS_CLASS__">__STATUS_LABEL__</span>
          </div>
          <p class="lead">How often Keepa is polled and which drops become Discord alerts.</p>
          <div class="grid">
            <label class="field">
              <span>Scan interval (minutes)</span>
              <input type="number" name="scan_interval_min" min="5" max="120" value="__SCAN_INTERVAL__"/>
              <p class="hint">Typical 10–15. Each scan uses Keepa tokens (~5 for deals).</p>
            </label>
            <label class="field">
              <span>Max alerts per channel / scan</span>
              <input type="number" name="max_alerts_per_tier" min="1" max="15" value="__MAX_ALERTS__"/>
              <p class="hint">1 = best deal only for each Amazon-50 / 60 / 70 / 80.</p>
            </label>
            <label class="field">
              <span>Min discount %</span>
              <input type="number" name="min_discount" min="40" max="95" value="__MIN_DISCOUNT__"/>
            </label>
            <label class="field">
              <span>Min original price ($)</span>
              <input type="number" step="0.01" name="min_original_price" value="__MIN_PRICE__"/>
            </label>
            <label class="field">
              <span>Same-day no-repeat</span>
              <select name="no_repeat_same_day">
                <option value="true" __NR_ON__>On — same ASIN once per day</option>
                <option value="false" __NR_OFF__>Off — only cooldown hours</option>
              </select>
              <p class="hint">Still re-alerts if the price drops further.</p>
            </label>
          </div>
          <label class="check">
            <input type="checkbox" name="enrich_on_alert" value="1" __ENRICH__/>
            <span>Enrich seller / business only for alerts we send (recommended — saves tokens)</span>
          </label>
          <div class="actions">
            <button class="primary" type="submit">Save settings</button>
          </div>
        </form>
      </div>

      <div class="flow-step is-run">
        <div class="rail"><div class="step-num">4</div></div>
        <section class="panel">
          <div class="panel-head">
            <h3>Run the monitor</h3>
            <span class="tag">Live</span>
          </div>
          <p class="lead">Start continuous scanning, stop it, or run a single cycle now.</p>
          <div class="actions">
            <form method="post" action="/start" style="display:inline">__START_BTN__</form>
            <form method="post" action="/stop" style="display:inline">__STOP_BTN__</form>
            <form method="post" action="/once" style="display:inline">
              <button class="ghost" type="submit">Run 1 scan now</button>
            </form>
          </div>
        </section>
      </div>
    </div>
  </div>
</body>
</html>
"""


def _webhook_badge(has_url: bool, enabled: bool = True) -> str:
    if not has_url:
        return '<span class="pill off">missing</span>'
    if not enabled:
        return '<span class="pill off">paused</span>'
    return '<span class="pill on">active</span>'


def _display_webhook(ui: dict, tier: int) -> str:
    """Webhook URL for the form (UI override, else .env) — even if channel is paused."""
    import os

    v = str(ui.get(f"discord_webhook_{tier}") or "").strip()
    if v:
        return v
    return (os.getenv(f"DISCORD_WEBHOOK_{tier}") or "").strip()


def _render_page(**kwargs: str) -> str:
    html = PAGE
    for key, value in kwargs.items():
        html = html.replace(f"__{key}__", str(value))
    return html


@app.get("/", response_class=HTMLResponse)
def home(_: Request) -> str:
    ui = load_ui_settings()
    s = load_settings()
    running = _is_running()
    stopping = _is_stopping()
    key = s.keepa_api_key or ""
    if stopping:
        status_class, status_label = "off", "STOPPING…"
    elif running:
        status_class, status_label = "on", "RUNNING"
    else:
        status_class, status_label = "off", "STOPPED"

    def en(tier: int) -> bool:
        return bool(ui.get(f"channel_enabled_{tier}", True))

    wh = {t: _display_webhook(ui, t) for t in (50, 60, 70, 80)}
    if key:
        masked = (key[:4] + "…" + key[-4:]) if len(key) > 12 else "••••••••"
        keepa_placeholder = f"Saved ({masked}) — paste new key to replace"
        keepa_hint = "Key is already saved (env/UI). Field stays empty so browser autofill cannot overwrite it."
    else:
        keepa_placeholder = "Paste Keepa API key"
        keepa_hint = "No key saved yet."

    return _render_page(
        STATUS_CLASS=status_class,
        STATUS_LABEL=status_label,
        LAST_MESSAGE=html.escape(_status.get("last_message", "") or "Idle"),
        SCAN_INTERVAL=str(ui["scan_interval_min"]),
        MAX_ALERTS=str(ui["max_alerts_per_tier"]),
        MIN_DISCOUNT=str(ui["min_discount"]),
        MIN_PRICE=str(ui["min_original_price"]),
        NR_ON="selected" if ui["no_repeat_same_day"] else "",
        NR_OFF="selected" if not ui["no_repeat_same_day"] else "",
        ENRICH="checked" if ui["enrich_on_alert"] else "",
        KEEPA_PLACEHOLDER=html.escape(keepa_placeholder),
        KEEPA_HINT=html.escape(keepa_hint),
        KEY_STATUS="saved" if key else "not set",
        WH50=html.escape(wh[50]),
        WH60=html.escape(wh[60]),
        WH70=html.escape(wh[70]),
        WH80=html.escape(wh[80]),
        EN50="checked" if en(50) else "",
        EN60="checked" if en(60) else "",
        EN70="checked" if en(70) else "",
        EN80="checked" if en(80) else "",
        DIS50="" if en(50) else "disabled-ch",
        DIS60="" if en(60) else "disabled-ch",
        DIS70="" if en(70) else "disabled-ch",
        DIS80="" if en(80) else "disabled-ch",
        W50=_webhook_badge(bool(wh[50]), en(50)),
        W60=_webhook_badge(bool(wh[60]), en(60)),
        W70=_webhook_badge(bool(wh[70]), en(70)),
        W80=_webhook_badge(bool(wh[80]), en(80)),
        START_BTN='<button class="primary" type="submit">Start monitor</button>'
        if not running
        else '<button class="ghost" type="button" disabled>Start monitor</button>',
        STOP_BTN='<button class="danger" type="submit">Stop</button>'
        if running and not stopping
        else (
            '<button class="ghost" type="button" disabled>Stopping…</button>'
            if stopping
            else '<button class="ghost" type="button" disabled>Stop</button>'
        ),
    )


@app.post("/save-key")
def save_key(keepa_api_key: str = Form("")):
    key = (keepa_api_key or "").strip()
    # Empty submit = clear UI override only (env key still works).
    save_ui_settings({"keepa_api_key": key})
    if key:
        _status["last_message"] = "Keepa API key saved."
    else:
        _status["last_message"] = "UI API key cleared (Render/.env key still used if set)."
    return RedirectResponse("/", status_code=303)


@app.post("/save-webhooks")
def save_webhooks(
    discord_webhook_50: str = Form(""),
    discord_webhook_60: str = Form(""),
    discord_webhook_70: str = Form(""),
    discord_webhook_80: str = Form(""),
    channel_enabled_50: str | None = Form(None),
    channel_enabled_60: str | None = Form(None),
    channel_enabled_70: str | None = Form(None),
    channel_enabled_80: str | None = Form(None),
):
    save_ui_settings(
        {
            "discord_webhook_50": (discord_webhook_50 or "").strip(),
            "discord_webhook_60": (discord_webhook_60 or "").strip(),
            "discord_webhook_70": (discord_webhook_70 or "").strip(),
            "discord_webhook_80": (discord_webhook_80 or "").strip(),
            "channel_enabled_50": channel_enabled_50 == "1",
            "channel_enabled_60": channel_enabled_60 == "1",
            "channel_enabled_70": channel_enabled_70 == "1",
            "channel_enabled_80": channel_enabled_80 == "1",
        }
    )
    _status["last_message"] = "Discord webhooks & channel toggles saved."
    return RedirectResponse("/", status_code=303)


@app.post("/test-alert")
def test_alert(
    discord_webhook_50: str = Form(""),
    discord_webhook_60: str = Form(""),
    discord_webhook_70: str = Form(""),
    discord_webhook_80: str = Form(""),
    channel_enabled_50: str | None = Form(None),
    channel_enabled_60: str | None = Form(None),
    channel_enabled_70: str | None = Form(None),
    channel_enabled_80: str | None = Form(None),
):
    """Sample Discord alert via Amazon-50 webhook — no Keepa tokens."""
    from discord_notifier import send_alert
    from keepa_client import amazon_image_url
    from models import AlertItem

    # Persist current form values so a fresh paste works without a separate Save.
    save_ui_settings(
        {
            "discord_webhook_50": (discord_webhook_50 or "").strip(),
            "discord_webhook_60": (discord_webhook_60 or "").strip(),
            "discord_webhook_70": (discord_webhook_70 or "").strip(),
            "discord_webhook_80": (discord_webhook_80 or "").strip(),
            "channel_enabled_50": channel_enabled_50 == "1",
            "channel_enabled_60": channel_enabled_60 == "1",
            "channel_enabled_70": channel_enabled_70 == "1",
            "channel_enabled_80": channel_enabled_80 == "1",
        }
    )

    ui = load_ui_settings()
    webhook = _display_webhook(ui, 50)
    if not webhook:
        _status["last_message"] = "Test alert failed — paste an Amazon-50 webhook first."
        return RedirectResponse("/", status_code=303)

    asin = "B00005QJ1S"
    host = "www.amazon.com"
    item = AlertItem(
        asin=asin,
        title="TEST ALERT — Anker 735 Charger (GaNPrime 65W) sample embed",
        image_url=amazon_image_url("31RmBhqOkZL.jpg", size=120),
        new_price=3.66,
        old_price=13.61,
        discount=55,
        seller="Amazon",
        promotion=False,
        business_required=False,
        brand="Anker",
        review_count=1200,
        rating=4.7,
        categories=[],
        root_cat=0,
        amazon_url=f"https://{host}/dp/{asin}",
        keepa_url=f"https://keepa.com/#!product/1-{asin}",
        google_url="https://www.google.com/search?q=Anker+735+Charger",
        sas_url=f"https://sas.selleramp.com/sas/lookup?search_term={asin}&sas_cost_price=3.66",
        ebay_url="https://www.ebay.com/sch/i.html?_nkw=Anker+735+Charger",
        atc_url=f"https://{host}/gp/aws/cart/add.html?ASIN.1={asin}&Quantity.1=1",
        graph_url="",
        recent_discount=55,
    )
    ok, err = send_alert(webhook, "", item, 50)
    if ok:
        _status["last_message"] = "Test alert sent to Amazon-50 Discord webhook."
    else:
        _status["last_message"] = f"Test alert failed: {err}"
    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health():
    return PlainTextResponse("ok")


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Login — Amazon Price Error Monitor</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 360px; margin: 64px auto; padding: 0 16px; }
  label { display: block; margin-bottom: 8px; font-weight: 600; }
  input { width: 100%; padding: 10px; box-sizing: border-box; margin-bottom: 12px; }
  button { padding: 10px 16px; cursor: pointer; }
  .err { color: #c62828; margin-bottom: 12px; }
</style>
</head><body>
  <h1>Amazon Price Error Monitor</h1>
  <p>Enter the UI password to continue.</p>
  __ERROR__
  <form method="post" action="/login">
    <label>Password</label>
    <input type="password" name="password" autofocus required/>
    <button type="submit">Log in</button>
  </form>
</body></html>
"""


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if not _ui_password():
        return RedirectResponse("/", status_code=303)
    if _is_authed(request):
        return RedirectResponse("/", status_code=303)
    return LOGIN_PAGE.replace("__ERROR__", "")


@app.post("/login")
def login_post(password: str = Form("")):
    pw = _ui_password()
    if not pw:
        return RedirectResponse("/", status_code=303)
    if not _password_ok((password or "").strip(), pw):
        return HTMLResponse(
            LOGIN_PAGE.replace("__ERROR__", '<p class="err">Wrong password.</p>'),
            status_code=401,
        )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        AUTH_COOKIE,
        _auth_token(pw),
        httponly=True,
        samesite="lax",
        secure=bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL")),
        max_age=60 * 60 * 24 * 30,
    )
    return resp


@app.post("/save")
def save(
    scan_interval_min: int = Form(10),
    max_alerts_per_tier: int = Form(8),
    min_discount: int = Form(50),
    min_original_price: float = Form(4.0),
    no_repeat_same_day: str = Form("true"),
    enrich_on_alert: str | None = Form(None),
):
    save_ui_settings(
        {
            "scan_interval_min": scan_interval_min,
            "max_alerts_per_tier": max_alerts_per_tier,
            "min_discount": min_discount,
            "min_original_price": min_original_price,
            "no_repeat_same_day": no_repeat_same_day.lower() == "true",
            "enrich_on_alert": enrich_on_alert == "1",
        }
    )
    _status["last_message"] = f"Saved. Scan every {scan_interval_min} min."
    return RedirectResponse("/", status_code=303)


@app.post("/start")
def start():
    result = _try_start_monitor(message="Started.")
    if result == "missing_credentials":
        _status["last_message"] = (
            "Cannot start — set Keepa API key + Discord webhooks in the UI (or Render env)."
        )
    elif result == "already_running":
        pass
    return RedirectResponse("/", status_code=303)


@app.post("/stop")
def stop():
    global _monitor
    if _monitor and _is_running():
        _status["stopping"] = True
        _status["last_message"] = "Stopping… finishing current Keepa call, then idle."
        _monitor.request_stop()
        try:
            # Unblock an in-flight HTTP wait if possible.
            _monitor.keepa.close()
        except Exception:
            pass
    elif not _is_running():
        _status["stopping"] = False
        _status["last_message"] = "Already stopped."
    return RedirectResponse("/", status_code=303)


@app.post("/once")
def once():
    from monitor import Monitor

    s = load_settings()
    if not s.keepa_api_key or not s.tiers:
        _status["last_message"] = (
            "Cannot scan — set Keepa API key + Discord webhooks in the UI (or Render env)."
        )
        return RedirectResponse("/", status_code=303)

    def _run() -> None:
        mon = Monitor(s)
        try:
            n = mon.run_once()
            _status["last_message"] = f"One-shot scan done — {n} alert(s) sent."
        except Exception as exc:  # noqa: BLE001
            _status["last_message"] = f"Scan error: {exc}"
        finally:
            mon.close()

    threading.Thread(target=_run, daemon=True).start()
    _status["last_message"] = "One-shot scan started…"
    return RedirectResponse("/", status_code=303)


def main() -> None:
    host = (os.getenv("HOST") or "").strip() or ("0.0.0.0" if _is_cloud() else "127.0.0.1")
    try:
        port = int((os.getenv("PORT") or "8787").strip())
    except ValueError:
        port = 8787

    # Local only: open browser. On Render, uvicorn is usually started via startCommand.
    open_browser = not _is_cloud() and host in ("127.0.0.1", "localhost")
    if open_browser:
        import time
        import webbrowser

        def _open() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://127.0.0.1:{port}")

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
