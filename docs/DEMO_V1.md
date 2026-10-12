# Nova — Sani's Chicken local voice demo V1

Nova is the customer-facing product and AI receptionist, powered by the existing
OrderAI backend. Repository, package, imports and environment variable names are
unchanged. Run from the existing OrderAI repository with its virtual environment:

```powershell
$env:ORDERAI_DEMO_ENABLED = '1'
$env:ORDERAI_DEMO_ACCESS_CODE = '<choose a private demonstration code>'
$env:FLASK_SECRET_KEY = '<choose a stable random session secret>'
$env:ORDERAI_MENU_EDITOR_ENABLED = '0'
.\.venv\Scripts\python.exe web.py
```

Open `http://127.0.0.1:5000`. Browsing is public; creating a call requires the
code and same-session CSRF token. Configure OPENAI_API_KEY only in the server
environment for a separately authorised live rehearsal. No live services are
needed for offline tests. Access codes are not saved in browser storage.

Nova demo mode always selects `sanis`, uses published `restaurants/sanis/menu.json`,
preserves review labels, and makes no claim that prices are owner-approved.
Submission is simulated in memory: no SQLite save, Twilio notification, payment
or fulfilment. Text chat, transcription and order-admin routes are disabled in
demo mode. The existing interface and multi-restaurant selection remain available
when demo mode is off. Keep the menu editor disabled; its local guards are unchanged.

The shared runtime iterator supports category maps and category lists without
rewriting menus or touching ingestion. Ingredient customisations are deliberately
unavailable in the Sani's demo because the published menu lacks ingredient data.

## Lifecycle and transport

- `POST /demo/calls`: `{access_code}`; creates one fresh order/call per browser session.
- `GET /demo/calls/<id>`: owned snapshot, including an ended call while retained.
- `POST /demo/calls/<id>/heartbeat`: refreshes the active lease.
- `POST /demo/calls/<id>/end`: idempotent closure under the order lock.
- `POST /realtime-session`: `{call_id}`; one credential request per protected call.
- `POST /realtime-tool`: `{call_id, tool_call_id, name, arguments}`; validates,
  serialises and caches execution and returns an authoritative snapshot/revision.

POSTs require `X-Demo-CSRF`, and reject foreign origins. Identity is bound to the
signed cookie; call IDs alone confer no access. Tool call IDs cannot be reused
with different arguments. Failed execution is cached too, preventing a partial
mutation from being applied again. Revisions advance when order/pending state
changes; the browser ignores old revisions and previous call generations.

Active calls expire after 10 minutes or 45 seconds without a heartbeat/tool
request. The browser heartbeats every 10 seconds and closes audio at 10 minutes.
Ended/expired carts are retained for 30 minutes then pruned on subsequent
requests. Up to eight active calls and ten start attempts per IP per minute are
allowed. A service-wide cap of 30 attempts per minute also applies, plus ten
attempts per browser identity. Forwarded client IP headers are not trusted;
the peer limit can group visitors behind a reverse proxy. Repeated refreshes do not
mint credentials; refresh recovers the cart and closes the old call without
requesting microphone permission. Missing cookies/process state return explicit
errors. Page exit sends a best-effort end request; leases handle abrupt exits.

Use one server process. Calls, deduplication and orders are in memory and do not
survive restart. Stable FLASK_SECRET_KEY is needed for cookie continuity, but it
cannot preserve lost in-memory carts. A second tab shares the browser session and
cannot start until the first call ends/expires. Multiple independent browsers
can call concurrently. An already accepted tool may complete before End Call
obtains the lock; subsequent tools are rejected.

The permanent key remains server-side; only an ephemeral credential is returned,
with no-store caching. WebRTC connects directly to OpenAI. Browser timers and
backend leases are not a hard provider spend limit for a modified client holding
a valid credential. Public cost enforcement/provider call termination and
hard provider call termination remain outside this demo scope.

## Offline verification

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_demo -v
node --test tests/test_demo_frontend.cjs
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -t . -v
```

SDK construction, HTTP and fulfilment are mocked in demo tests. Frontend tests
use Node's built-in runner and fake media, peer, channel, network and storage;
they never contact OpenAI. No npm packages are required.

## Manual checks before a separately authorised paid rehearsal

Check desktop/mobile layout, menu content, review labels, category links, tab
order, focus, expandable cart and screen-reader feedback. Check permission denial
and autoplay behaviour on actual desktop Chrome/Edge and mobile Safari/Chrome.
Then, only when authorised, test a live connection, actual Realtime event ordering,
audio, interruptions, clarification, quantities, removal, confirmation/simulated
submission, End Call, refresh, two browsers, second-tab rejection and network loss.
Verify the browser's microphone indicator goes off after every termination path.
No end-to-end WebRTC claim is made by mocked tests.

Render preparation and production startup are documented in [RENDER_DEPLOYMENT.md](RENDER_DEPLOYMENT.md).
No live end-to-end voice verification or deployment has been performed.
