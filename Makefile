.PHONY: run test docker docker-build

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

test:
	python3 -m pytest

docker:
	docker compose up --build

docker-build:
	docker compose build
