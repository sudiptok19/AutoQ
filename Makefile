.PHONY: db backend frontend backend-test frontend-test e2e demo-reset

db:            ; docker compose up -d db
backend:       ; cd backend && uv run uvicorn app.main:app --reload --port 8000
frontend:      ; cd frontend && npm run dev
backend-test:  ; cd backend && uv run pytest -q
frontend-test: ; cd frontend && npm test
e2e:           ; cd e2e && npx playwright test
demo-reset:    ; curl -s -X POST http://localhost:8000/api/v1/demo/reset -H "Authorization: Bearer $$AUTOQ_ADMIN_TOKEN"
