# LAN HTTPS phone gateway

**Owner:** Me / Astra

**Current Status in Repo:** Not started

Opus already provides optional self-signed Vite HTTPS on the foundation branch. The base Compose file runs PostgreSQL only. This deliverable provides a trusted origin on the actual phone.

## Exact File Paths

Planned files owned by Me / Astra.

- `docker-compose.https.yml`
- `Caddyfile`
- `certs/README.md`

Read-only inputs are Opus’s `docker-compose.yml`, `frontend/vite.config.ts` and backend routes. Opus owns all application services, manifests, environment examples and PWA configuration.

## Inputs, Outputs & Contract

- Layer the override with `docker compose -f docker-compose.yml -f docker-compose.https.yml up -d`. Add a Caddy service publishing 443. Use configurable upstreams reachable **from the proxy container**, defaulting to `host.docker.internal:8000` and `host.docker.internal:5173` on the Windows Docker Desktop host. Expose HTTP APIs and WS upgrades at the same HTTPS origin with paths intact. Forward `/api/*`, `/ws/*` and `/openapi.json` to the backend and other paths to the frontend.
- The guide covers issuing a LAN hostname/IP SAN leaf using mkcert, mounting the leaf and key read-only from outside the checkout, installing only the public CA certificate as a trusted root on the test phone, then verifying normal TLS validation. Keep the CA private key on the issuing machine. Document certificate replacement after LAN address changes and removing phone trust after testing.
- Upstream failure must surface as a proxy error. Check the live API mode and the built PWA when testing offline. Development-only certificate warnings are insufficient for acceptance.

## Schema Dependencies

Zero tables, migrations, models or application registrations.

## Verification Criteria

Run Compose config with both files and verify base DB configuration is preserved. On the physical phone open the trusted origin without bypassing a warning, check the live health response and camera capture, receive an authenticated WebSocket event, install/reopen the production PWA offline, then reconnect and sync. Test an unreachable backend and an untrusted certificate separately. Record phone/browser and effective LAN hostname.
