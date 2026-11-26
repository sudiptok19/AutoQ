# Trusted LAN HTTPS for the AutoQ scanner

Caddy terminates HTTPS on port 443 and redirects port 80 to HTTPS. It forwards `/api/*`, `/ws/*`, and `/openapi.json` to FastAPI with paths and query strings intact. It forwards other requests to the built Vite app. Browser requests use the same origin, including `wss://` for events. Run this setup on a trusted LAN and restrict access to test devices. Keep ports 80 and 443 off public networks. [Caddy reverse proxy documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

## Issue a certificate on the desktop

Put the desktop and phone on the same LAN. Use the desktop's current LAN IPv4 address from `ipconfig` on Windows or **System Settings → Network** on macOS. Replace `192.168.1.50` below. If you use a LAN hostname, ensure the phone resolves it to the desktop and add that hostname to the end of the `mkcert` command. The address used in the browser must be in the certificate's Subject Alternative Names (SANs).

**Windows PowerShell**

```powershell
choco install mkcert
$env:AUTOQ_CERT_DIR = Join-Path $HOME '.autoq-certs'
New-Item -ItemType Directory -Path $env:AUTOQ_CERT_DIR -Force | Out-Null
$cert = Join-Path $env:AUTOQ_CERT_DIR 'tls.crt'
$key = Join-Path $env:AUTOQ_CERT_DIR 'tls.key'
mkcert -install
mkcert -cert-file $cert -key-file $key localhost 192.168.1.50
mkcert -CAROOT
```

**macOS**

```sh
brew install mkcert
export AUTOQ_CERT_DIR="$HOME/.autoq-certs"
mkdir -p -m 700 "$AUTOQ_CERT_DIR"
mkcert -install
mkcert -cert-file "$AUTOQ_CERT_DIR/tls.crt" -key-file "$AUTOQ_CERT_DIR/tls.key" localhost 192.168.1.50
mkcert -CAROOT
```

Keep the leaf directory outside the checkout. Compose defaults to `~/.autoq-certs` and mounts only `tls.crt` and `tls.key` as read-only files. Include `localhost` in the leaf even when the phone uses the LAN IP. Caddy uses it to select the leaf when an IP client omits TLS SNI. If you bring an existing leaf, set `AUTOQ_TLS_DEFAULT_NAME` to a name present in its SANs. The phone still verifies the SAN for the address in its URL. [mkcert instructions](https://github.com/FiloSottile/mkcert#installation) · [Caddy TLS options](https://caddyserver.com/docs/caddyfile/options#default-sni)

## Trust the public root on the test phone

Transfer only `rootCA.pem` from the directory printed by `mkcert -CAROOT`. Use a transfer method you control, such as AirDrop or USB. Keep `rootCA-key.pem` on the issuing desktop. Never copy it to a phone, container, shared drive, or repository. The leaf key `tls.key` stays on the desktop and in the Caddy mount. [mkcert mobile-device instructions](https://github.com/FiloSottile/mkcert#mobile-devices)

- **Android:** Open **Settings → Security & privacy → More security settings → Encryption & credentials → Install a certificate → CA certificate**. Exact names vary by manufacturer. Select the public `rootCA.pem`. If the file picker requires `.crt`, rename a copy of that public file to `rootCA.crt`. Confirm installation and check that it appears among the user credentials. [Google certificate settings](https://support.google.com/pixelphone/answer/2844832)
- **iOS:** Open the public certificate and install the downloaded profile in **Settings → Profile Downloaded** or **Settings → General → VPN & Device Management**. Then open **Settings → General → About → Certificate Trust Settings** and enable full trust for the installed mkcert root. [Apple certificate trust instructions](https://support.apple.com/en-us/102390)

## Start the live app and Caddy

Start PostgreSQL from the repository root, then prepare and run FastAPI in its own terminal. Bootstrap the signing key and dev users on a fresh checkout so the live health endpoint and sign-in are available.

```sh
docker compose up -d --wait db
cd backend
uv sync --locked
uv run alembic upgrade head
uv run python scripts/bootstrap_keys.py
uv run python scripts/seed_dev_users.py
uv run uvicorn app.main:app --host 0.0.0.0 --port 8010
```

In a second terminal, build the production PWA in live API mode and serve it over HTTP on port 5173. Caddy provides browser-facing TLS. Use the preview server for this LAN test.

**Windows PowerShell**

```powershell
cd frontend
npm ci
Remove-Item Env:HTTPS -ErrorAction SilentlyContinue
$env:VITE_API_MODE = 'live'
npm run build
npm run preview -- --host 0.0.0.0 --port 5173 --strictPort
```

**macOS**

```sh
cd frontend
npm ci
unset HTTPS
VITE_API_MODE=live npm run build
npm run preview -- --host 0.0.0.0 --port 5173 --strictPort
```

For a custom LAN hostname, set `__VITE_ADDITIONAL_SERVER_ALLOWED_HOSTS` to that hostname before running preview. An IPv4 address works with Vite's default host checks. [Vite build-time variables](https://vite.dev/guide/env-and-mode) · [Vite preview options](https://vite.dev/config/preview-options)

From the repository root, validate and start the Compose overlay. It adds Caddy alongside the base Postgres service.

```sh
docker compose -f docker-compose.yml -f docker-compose.https.yml config
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d
```

The proxy's default upstreams are `host.docker.internal:8010` and `host.docker.internal:5173`. Set `AUTOQ_BACKEND_UPSTREAM` or `AUTOQ_FRONTEND_UPSTREAM` to a reachable `host:port` before starting Compose if either HTTP service moves. Compose also maps `host.docker.internal` to `host-gateway` on Linux. Allow phone access to ports 80 and 443 on the trusted LAN firewall. Keep the upstream ports accessible to Docker. [Docker Compose networking](https://docs.docker.com/compose/how-tos/networking#custom-dns-with-extra_hosts)

## Verify the phone and upstream routing

1. Open `http://192.168.1.50/scanner` and check that it redirects to `https://192.168.1.50/scanner`. The browser should load HTTPS without a certificate warning. Open the scanner and grant camera permission.
2. Open `https://192.168.1.50/api/v1/health` and confirm HTTP 200 with the live service response. Check that `/openapi.json` reaches FastAPI.
3. With Opus's socket route integrated, sign in and verify that the one-use ticket from `POST /api/v1/ws/tickets` opens `wss://192.168.1.50/ws/events` and delivers a live event. Include the ticket as the `ticket` query parameter during the connection.
4. With offline snapshot and sync integrated, install the PWA, cache a valid live snapshot, reopen in airplane mode, and confirm the scanner can queue a provisional scan. Reconnect and confirm that sync reports the server outcome.
5. During a disposable smoke test, stop one host upstream and request a path routed to it. Caddy should return HTTP 502 with a plain-text gateway error. Start the upstream again and confirm recovery.

### Physical phone verification · 24 November 2026

Vivo X200t, OriginOS 7, Google Chrome 158.0.8127.42 (Official Build, 64-bit), with the gateway at `192.168.29.2`. The running `feature/astra-physical-eval` checkout served the live FastAPI and PostgreSQL stack through Caddy and a Vite production preview.

| Check | Observed result |
| --- | --- |
| Phone TLS and redirect | Chrome loaded `https://192.168.29.2/scanner` without a certificate warning. The HTTP redirect returned 308. |
| Live API and sign-in | `/api/v1/health` returned HTTP 200 with `status=ok`, `db=ok`, `key_kid=aq-dev-1`, `demo=false`. Dispatcher sign-in succeeded. |
| Camera and online load | The physical phone opened the camera with native `BarcodeDetector` and decoded the signed `PKG-0001` QR displayed on the laptop. The scanner showed **LOAD ACCEPTED**, all 11 dispatch gates passed, and `SHP-0001` reached 1/1 server-confirmed units at 19:02:05 IST. |
| WebSocket and ticket enforcement | The authenticated same-origin WebSocket received the `DISPATCH_STATE_CHANGED` frame with decision `ACCEPTED` and event ID `evt_01m86w3a34pwn2k8vxrhc8myqn`. Backend logs showed HTTP 403 during the upgrade for a missing ticket and for reuse of a consumed ticket. |
| Upstream outage and recovery | Stopping FastAPI produced HTTP 502 through Caddy with this exact plain-text response: `AutoQ gateway could not reach its upstream.` Restarting FastAPI restored HTTP 200 through Caddy. |
| Windows command-line TLS | Windows curl needed `--ssl-revoke-best-effort` because Schannel could not obtain revocation information for the local mkcert certificate. The phone Chrome session validated HTTPS without a warning. |

The camera test used the live phone QR decoder. Seal enrollment and recheck used `SYNTHETIC_TEST` bytes. Physical seal detection remains unverified by this run.

#### Persisted online event

A read-only, repeatable-read PostgreSQL check matched `SHP-0001` (`shp_01m86tqywszhsx61q1erzgxbx1`), `PKG-0001` (`pkg_01m86tqyn66wmwawkjqfzda48r`) and client event `dev-162d92:000001`.

- Scan `scan_01m86w3a2sdkf2pfq61mb430dk` was `ACCEPTED` at `2026-11-24T13:32:05.059268Z` by `disp.kiran`. It stored `offline=false`, `captured_at=NULL`, zero reasons and all 11 gate results as `PASS`. The duplicate gate reported “First accepted load,” the quantity gate reported “1 / 1 units,” and the seal gate cited the passing `INTACT` recheck `seal_01m86tqzbxd5cc6c8e1kg196v0` from 13:08:23 UTC.
- Load `load_01m86w3a2xvdhjvef8sef0eyyc` references that scan and remains the single active load for the package. The package is `LOADED`. Shipment `SHP-0001` was `OPEN` with one package, one loaded unit, one expected unit and zero movement events. There was one matching scan across all shipments, one load, one audit row and one outbox row for the event.
- Audit row `aud_01m86w3a330w2tkshm6604qfjk` recorded `LOAD_ACCEPTED` at sequence 30 and `13:32:05.091851Z`, with the client event ID, package and online flag. The audit hash chain verified through sequence 31 at the time of the check.
- Outbox row `evt_01m86w3a34pwn2k8vxrhc8myqn` carried the same scan ID, package and accepted decision with severity `INFO`, zero reason codes and no siren. Its `sent_at` was `13:32:05.616827Z`, about 558 ms after the scan decision. The relay sets `sent_at` when it claims the row. The received WebSocket frame independently confirms delivery.

#### Queue and upstream integration

After the successful online scan, the phone showed one unsynced entry and an intact device chain. The scanner appends an `ONLINE` event to IndexedDB with the next sequence and hash before posting the scan. The REST response updates the queue entry to `ACCEPTED`, and the sync response supplies its `synced_at`. The count therefore reflects an outstanding device checkpoint. PostgreSQL had zero offline device events, snapshots or device checkpoint rows for `dev-162d92` at the time of verification. The phone queue and original accepted scan were preserved.

The first online scan ran on a checkout that predates the integrated offline endpoints. Fetched `origin/main` at `3dbdc2f` includes the original Caddy gateway (`ea10182`, PR #9), WebSocket integration (`b1cddf3`, PR #12), signed snapshot and scan sync (`b6535c8`, PR #21), PR #22 phone-JPEG work, cached offline identity (`9e599c2`, PR #23) and the live frontend adapter (`3a6fdd0`, PR #25). On that version, `GET /api/v1/offline/snapshot` registers the phone device and issues a signed snapshot. `POST /api/v1/sync/scans` reconciles the existing online load as `DUPLICATE` while advancing the device chain. The client keeps the original accepted decision for the already-recorded scan.

#### Integrated physical airplane-mode run · 25 November 2026

The integrated backend and production PWA ran from a separate checkout at `3dbdc2f` with the original PostgreSQL data, signing key, evidence store, JWT configuration and HTTPS origin. The Vivo retained dispatcher `disp.kiran` and IndexedDB device `dev-162d92`. The production scanner and service worker served through Caddy matched the staged build byte for byte. The database remained at Alembic revision `0002_package_label_seq`.

- At 00:35 IST the phone downloaded the `SHP-0001` snapshot and synced `dev-162d92:000001`. The sync response showed “Online decision already recorded.” The original scan remained `ACCEPTED`, and the shipment remained at 1/1.
- The user installed the standalone PWA and verified a signed `SHP-0002` snapshot with a 240-minute lifetime, one allowed package, zero held packages and `aq-dev-1`. With airplane mode on and Wi-Fi off, the user closed and reopened the PWA. It retained the dispatcher and shipment, showed **OFFLINE** and **ID UNVERIFIED**, and opened the physical camera via `BarcodeDetector`.
- At 00:52:12 IST the camera decoded `PKG-0002` as event `000002`. The Ed25519 signature verified on-device. The scanner showed **PROVISIONAL · LOAD**, eight local checks with offline limitations, one provisional unit and one unsynced event.
- At 00:54:29 IST the same QR produced event `000003`, which showed **DO NOT LOAD**, `ALREADY_SCANNED_LOCALLY` and `ORDER_QTY_EXCEEDED`. The provisional count stayed at one and the unsynced count became two.
- At 00:57 IST the phone reconnected and **ID UNVERIFIED** cleared. One press of **Sync** processed both events. At 00:58 IST `SHP-0002` showed 1/1 server-confirmed with zero provisional units and zero unsynced events. The displayed chain head remained `b31f7994a4` with three events.

#### Persisted closeout evidence

A repeatable-read, read-only PostgreSQL check joined the dispatcher, signed snapshots, three device events, scans, active loads, audit entries and dispatch outbox rows. Both stored snapshots verified under `aq-dev-1` and matched their device, operator and shipment. The `SHP-0002` snapshot was valid for both offline captures.

| Device event suffix | Stored local decision | Sync outcome | Authoritative scan | Audit sequence and action |
| --- | --- | --- | --- | --- |
| `000001` | `ONLINE` | `DUPLICATE` | Original `ACCEPTED`, `scan_01m86w3a2sdkf2pfq61mb430dk` | 30 `LOAD_ACCEPTED` |
| `000002` | `PROVISIONAL_ACCEPT` | `ACCEPTED` | `ACCEPTED`, `scan_01m87ge21zagjp3r3rwgd1wxmt`, 11/11 gates passed | 56 `LOAD_ACCEPTED` |
| `000003` | `LOCAL_BLOCK` | `BLOCKED` | `BLOCKED`, `scan_01m87ge2569a60fe2x6m69mg1m`, `ALREADY_LOADED` | 57 `LOAD_BLOCKED` |

Both shipments remained open at 1/1 with exactly one active load each. `SHP-0001` retained `load_01m86w3a2xvdhjvef8sef0eyyc`. `SHP-0002` had `load_01m87ge2283bq5re5wm60j97aw`, linked only to event `000002`. The accepted scans used `INTACT` rechecks `seal_01m86tqzbxd5cc6c8e1kg196v0` and `seal_01m87ev3jntsae8d79v4ghy75z`, both `SYNTHETIC_TEST`. The two offline decisions generated dispatch outbox rows `evt_01m87ge22rwe1qqtncq7g7v7zr` and `evt_01m87ge25kgjh21dvsns00eh5b`.

Recomputing all three SHA-256 links from the six canonical scan fields matched each persisted hash, starting from the all-zero genesis. The server checkpoint is sequence 3 with head `b31f7994a405a34d82b1350bd3238191748d868d815f9745dde8c3b46638459a`. The audit hash chain also verified across all 58 rows.

**A7 passed on the Vivo X200t.** The second physical capture verified duplicate rejection with a distinct event ID. The automated identical-request replay below separately verified idempotency. Seal optics remain outside this physical acceptance because both seal checks used synthetic evidence.

#### Automated verification on the upstream build

- Backend + disposable PostgreSQL 16, with `AUTOQ_DISABLE_RELAY=1`: `python -m pytest -v -p no:cacheprovider tests/test_offline_scanner.py` passed 7 tests in 64.30 seconds, with one Starlette/AnyIO deprecation warning. The run covered signed snapshot capacity and authorization, acceptance through the live dispatch gate, identical replay, rejection of a changed already-synced event and a chain gap without advancing the checkpoint, post-snapshot holds with durable segregation, two devices, a lost online response and shipment close.
- Frontend: `npm.cmd test -- src/auth/session.test.ts src/scanner/offline.test.ts` passed 8 tests in 1.11 seconds. The run covered cached offline identity, genuine auth rejection and local provisional decisions.
- A mobile-sized Chromium smoke test used the production PWA in a secure loopback context with real FastAPI and disposable PostgreSQL. Signed synthetic labels were submitted through the scanner controls. The online scan brought the shipment to 4/8 and left one device event pending. After Chromium networking was disabled, the second 4-unit scan stayed provisional and the server count remained at 4. An offline reload retained the signed snapshot, the dispatcher session marked `ID UNVERIFIED`, and both IndexedDB chain entries. Reconnecting revalidated dispatcher auth, and **Sync** processed two events and brought the server to 8/8 with two active loads and an empty queue.
- Replaying the identical sync request returned `DUPLICATE` for both events, reflecting the original outcomes `DUPLICATE` for the previously accepted online event and `ACCEPTED` for the provisional event. The sequence remained 2, the head was unchanged and the load total remained 8. The shipment's snapshot advisory still showed one device after the UI's automatic snapshot refresh. This advisory tracks a newly issued snapshot and the local queue showed zero pending entries.

#### Opus handoff

- The Scan tab retained the last offline decision card after sync even though Queue showed the reconciled decision and the counters were correct. In `frontend/src/pages/Scanner.tsx`, sync updates the queued entry while the in-memory `shown` card keeps its offline data. After persisting a sync result, clear the displayed offline card only when its event ID matches the reconciled event. Cover a reconnect and sync on a mounted scanner, then return to Scan and verify the stale card is gone. If the server records a conflict, the Queue's physical-action instruction must remain visible.

- `frontend/src/pages/Scanner.tsx` submits one snapshot ID for the whole pending batch. The physical test reconciled each shipment before switching to the next one. For general use, retain the capture snapshot on each event or reconcile each shipment's pending batch with its associated snapshot.
- `backend/app/features/offline_scanner/service.py` should require a snapshot for provisional events, bind it to the event shipment and operator, and validate the capture against its validity period. For first reconciliation of an already-recorded `ONLINE` scan, compare shipment and token with the persisted scan content fingerprint before linking it by client event ID. Keep the already-checkpointed replay comparison.
- Clarify the snapshot advisory used by shipment close after the scanner automatically refreshes a snapshot. The advisory and IndexedDB queue count can legitimately diverge, as observed in the browser smoke test.

#### Verification ownership

Astra led the integration and independently verified the database, signing key, LAN TLS identity and candidate build through Caddy. GPT-6.2 Sol at High effort verified the persisted scan, prepared the synthetic fixture and drafted this report. GPT-6.2 Sol at Extra High effort ran the isolated backend, frontend and Chromium checks. GPT-6.2 Sol independently recomputed the persisted device chain for closeout. GPT-6.2 Astra at High effort reviewed the scanner decision-card lifecycle and the snapshot integration boundaries.

## Renew the leaf and remove test trust

When the LAN IP or hostname changes, rerun the `mkcert` leaf command with the new SANs, then recreate Caddy so Docker mounts the current files. Phones can keep the same root if the leaf was signed by the same mkcert CA.

```sh
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --force-recreate caddy
```

After testing, stop Caddy and remove the test root from each phone.

```sh
docker compose -f docker-compose.yml -f docker-compose.https.yml stop caddy
```

- **Android:** In **Encryption & credentials**, open **User credentials** or the **User** tab under **Trusted credentials**. Select and remove only the mkcert root you installed. [Google removal instructions](https://support.google.com/pixelphone/answer/2844832)
- **iOS:** Disable the mkcert root in **Certificate Trust Settings**, then remove its profile in **Settings → General → VPN & Device Management**. [Apple profile removal instructions](https://support.apple.com/en-gb/guide/personal-safety/ips327569a75/web)
