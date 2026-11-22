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

Physical-phone acceptance is pending. Record the phone model, OS, browser, LAN address, and results of the camera, health, WebSocket, and offline checks in the PR.

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
