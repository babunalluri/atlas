.PHONY: up down logs test-backend test-migrations test-web typecheck smoke

up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f

test-backend:
	cd apps/backend && python -m pip install -e ".[dev]" && pytest -q

test-migrations:
	cd apps/backend && python -m pip install -e ".[dev]" && alembic heads && pytest -q tests/test_migrations.py

test-web:
	npm install && npm run test:web

typecheck:
	npm run typecheck:web

smoke:
	curl -fsS http://localhost:7777/health
	curl -fsS http://localhost:3000 >/dev/null
