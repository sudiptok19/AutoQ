.PHONY: db migrate keys seed-users seed-demo backend backend-test contracts frontend frontend-test e2e e2e-mock

API_PORT ?= 8010

db:            ; docker compose up -d --wait db
migrate:       ; cd backend && uv run alembic upgrade head
keys:          ; cd backend && uv run python scripts/bootstrap_keys.py
seed-users:    ; cd backend && uv run python scripts/seed_dev_users.py
backend:       ; cd backend && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port $(API_PORT)
backend-test:  ; cd backend && uv run pytest
contracts:     ; cd backend && uv run python scripts/export_contracts.py
frontend:      ; cd frontend && npm run dev
frontend-test: ; cd frontend && npm test
e2e:           ; cd e2e && npx playwright test
e2e-mock:      ; cd e2e && npx playwright test --project=mock
seed-demo:     ; cd backend && uv run python scripts/seed_demo.py --reset
