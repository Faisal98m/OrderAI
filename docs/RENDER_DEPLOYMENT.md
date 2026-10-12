# Nova — Sani's Chicken Demo V1 Render deployment preparation

Nova is the customer-facing product, powered by the existing OrderAI backend.
The repository and `orderai` package retain their current names.

No deployment has been performed. Live voice is not verified. The observed
"Illegal invocation" has a plausible source in a receiver-sensitive timer call path: native
browser timer functions were copied onto the controller and invoked with the
controller as `this`. The browser adapter now calls timers through `window`, with
an offline receiver-checking regression. This removes that unsafe invocation; confirmation
that it explains the observed browser failure still requires a browser stack trace
and a separately authorised live voice test.

## Runtime and dependencies

Python is pinned to **3.12.13** in `.python-version` and the Blueprint. The inspected
local environment is Windows Python 3.12.6. Keep the 3.12 series while using the
newer security patch for deployment. Render supports released patch versions:
[Python version configuration](https://render.com/docs/python-version).

`requirements.in` records ten direct dependencies. `requirements.txt` pins their
complete runtime dependency closure, including Gunicorn 26.2.0, using the versions
already installed locally for existing packages. No optional model/audio SDK
extras, React, Redis, npm build or application WebSocket server is needed.
PyMuPDF, pypdf and BeautifulSoup are required by the registered menu-management
blueprint's startup imports even though its routes are disabled. Twilio is an
existing import dependency; it is never called by the demo. The importer is unchanged.
Unused `fonttools` is omitted. Linux x86_64 Python 3.12 wheel resolution was checked
without installing or calling external AI services.

## Production entry point

```sh
python -m pip install --only-binary=:all: -r requirements.txt
gunicorn --config gunicorn.conf.py orderai.services.production:app
```

Use the new production entry point, not `app.py`, `python web.py` or the bare
development Flask import. It validates configuration before SDK construction,
disables dotenv loading and applies the production security settings. Missing or
unsafe configuration raises an error naming variables without printing values.
SDK construction and `/healthz` make no paid requests. Demo startup skips database
initialisation entirely. The shared `initialise_storage(app)` startup step
initialises SQLite only for the existing non-demo local launcher.

Gunicorn binds to `0.0.0.0:$PORT` through the configuration file. It uses exactly
one `gthread` worker and four threads, no reload/preload, a 60-second timeout and
a 30-second graceful timeout. Four threads allow credential HTTP requests to wait
without blocking all heartbeats. Registry and per-call locks protect shared state;
credential network I/O occurs outside the call lock. More workers/instances would
split orders, duplicate caches and rate limits, so they are prohibited. The startup
hook rejects worker/thread/preload/reload overrides. Clear Render's default
`GUNICORN_CMD_ARGS`, which otherwise includes `--preload`.

## Exact Render dashboard setup — perform only after review and authorisation

1. Review and commit the deployment files **and all required Demo V1 files** to
   the intended Git repository/branch. Several Demo V1 files are currently
   untracked; Render cannot read local-only files. Do not stage `.env`, databases,
   virtual environments, generated drafts, test outputs or unnecessary PDF fixtures.
2. Create one **Web Service**, connect that repository and choose the approved
   branch. Leave Root Directory blank when these files are at its repository root.
3. Name: `nova-sanis-demo`; Runtime: **Python**; choose a nearby region.
   The Blueprint proposes **Starter** for an always-running demo. Confirm the
   hosting plan before creation. Set **one instance**, with no autoscaling.
4. Build command: `python -m pip install --only-binary=:all: -r requirements.txt`.
5. Start command: `gunicorn --config gunicorn.conf.py orderai.services.production:app`.
6. Health Check Path: `/healthz`. Disable automatic deploys while reviewing V1.
7. Configure these environment variables through Render; never commit values:

| Variable | Setting |
|---|---|
| `PYTHON_VERSION` | `3.12.13` |
| `ORDERAI_DEMO_ENABLED` | `1` |
| `ORDERAI_MENU_EDITOR_ENABLED` | `0` |
| `PYTHON_DOTENV_DISABLED` | `1` |
| `WEB_CONCURRENCY` | `1` |
| `GUNICORN_CMD_ARGS` | Empty; remove the platform preload default |
| `ORDERAI_DEMO_ACCESS_CODE` | Private random value, at least 16 characters |
| `FLASK_SECRET_KEY` | Stable random value, at least 32 characters |
| `OPENAI_API_KEY` | The server-only project key |

Render supplies `PORT`, `RENDER=true` and `RENDER_EXTERNAL_HOSTNAME` automatically.
Do not add Twilio credentials, persistent disks or a database for this simulated
demo. Keep secrets in Render's environment settings, not a repository `.env`.
The page accepts the access code entered by the user; it never includes the
configured server value in HTML/JavaScript or browser storage. Only an ephemeral
OpenAI credential is returned after protected call initiation.

Alternatively use the reviewed `render.yaml` as a Blueprint; its three secret
entries use `sync: false` so values must be entered privately. Creating a service
or Blueprint deploys it, so neither action has been performed during preparation.

## HTTPS proxy, cookies and rate limiting

Render terminates TLS before forwarding HTTP. Werkzeug ProxyFix trusts exactly
one forwarded **scheme** value, only in the production entry point. Forwarded
client IP, host, port and path-prefix values are ignored. `TRUSTED_HOSTS` accepts
the exact Render hostname. Foreign Host/Origin values are rejected; CSRF checks
still require the signed-session token. Cookies are Secure, HttpOnly and SameSite
Lax. Application HTTP requests are rejected, except the side-effect-free health
probe. Render redirects public HTTP to HTTPS.

Do not put another proxy/custom domain in front of V1 without reviewing these
assumptions and the host allowlist. Production middleware is intended only behind
Render's ingress; do not expose Gunicorn's port directly. See
[Flask proxy guidance](https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/)
and [Render web services](https://render.com/docs/web-services).

The exact client-IP forwarding chain has not been verified on a running Render
service, so rate limiting deliberately does not guess it. Enforce **30 start
attempts per minute service-wide**, plus ten per browser identity and ten per
socket peer. A peer limit can group visitors behind a proxy; this is a conservative
demo limit, not a claim of accurate per-visitor IP attribution. Header/cookie/IP
rotation cannot bypass the service-wide budget within the single process.
There are also eight active call slots, a 500-tool-action cap per call, a ten-minute
call lifetime and a 45-second idle lease. Limits reset on process restart.

Order admin, menu editor, text chat and uploaded transcription remain inaccessible
in demo mode. No public URL can select Burger & Sauce or request an unprotected
credential. Simulated submission neither saves SQLite orders nor notifies staff.
Access logs omit query strings, headers and bodies; tool logs omit arguments and
exception messages. Browser-facing responses contain no permanent API key.
Production responses add CSP, no-store, HSTS, framing, MIME, referrer and microphone
permission headers without relaxing CSRF or origin validation.

## Verification and remaining release gates

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -v
node --test tests/test_demo_frontend.cjs
.\.venv\Scripts\python.exe -B tests/production_smoke.py
git diff --check
```

The production smoke script uses the actual WSGI entry point and real SDK
construction with unusable fixture credentials, blocks external HTTP and Twilio,
and tests HTTPS-proxy routing, cookies, protected calls, isolation, simulated
submission, duplicate execution, origin rejection and forwarding-resistant limits.
Gunicorn is a Unix server. This Windows machine has no installed WSL distribution
or Docker, so its actual Linux process/worker smoke test is not supported here.
Do not call the WSGI smoke a Gunicorn runtime test. Before deploying, on an available
Linux Python 3.12.13 environment, install the lock and run:

```sh
gunicorn --check-config --config gunicorn.conf.py orderai.services.production:app
gunicorn --config gunicorn.conf.py orderai.services.production:app
# In a second terminal (use the configured host for production-style requests):
curl -f http://127.0.0.1:${PORT:-10000}/healthz
```

Use private environment configuration for that check; no voice endpoint needs to
be invoked. Verify one worker/four threads and stop the process after testing.
Before an owner demonstration, separately authorise a bounded paid live browser
test for microphone permissions, SDP, audio, interruptions, error recovery, refresh
and shutdown. "Illegal invocation" must be confirmed resolved in the target browser.
No end-to-end voice claim is made by these offline checks.

Active orders, call references and duplicate caches are in memory. Restart/redeploy
loses them; the UI returns explicit expiry errors. Database persistence is unnecessary
for the approved simulated demo. Backend admission limits and browser timers do
not provide a hard provider-side spending limit against a modified browser client.
