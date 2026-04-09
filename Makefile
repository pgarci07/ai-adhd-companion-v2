dev:
	docker compose -f infra/docker/docker-compose.dev.yml up --build

stop:
	docker compose -f infra/docker/docker-compose.dev.yml down

run:
	streamlit run app/ui/main.py

test:
	pytest

format:
	python -m black app tests

lint:
	python -m ruff check app tests
