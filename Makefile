.PHONY: install lint format test test-unit test-integration test-property cov run docker-build docker-run clean

PYTHON ?= python3

install:
	$(PYTHON) -m pip install -r requirements-dev.txt
	$(PYTHON) -m pip install -e .

lint:
	ruff check src tests

format:
	ruff check --fix src tests
	ruff format src tests

test:
	pytest

test-unit:
	pytest tests/unit

test-integration:
	pytest tests/integration

test-property:
	pytest tests/property

cov:
	pytest --cov=ledgerlite --cov-branch --cov-report=term-missing --cov-report=html --cov-report=xml --cov-fail-under=90

run:
	LEDGERLITE_DB=ledgerlite.db flask --app ledgerlite.wsgi run --debug

docker-build:
	docker build -t ledgerlite:dev .

docker-run:
	docker run --rm -p 8000:8000 -v ledgerlite-data:/data ledgerlite:dev

clean:
	rm -rf .pytest_cache .hypothesis .ruff_cache htmlcov .coverage coverage.xml build dist *.egg-info
