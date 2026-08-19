.PHONY: install dev test test-unit test-integration test-e2e eval report logs clean deploy

PYTHON := python
PYTEST := pytest
UVICORN := uvicorn

install:
	pip install -e ".[dev]"

dev:
	$(UVICORN) astra.api.main:app --host 0.0.0.0 --port 8080 --reload

test:
	$(PYTEST) tests/ -v

test-unit:
	$(PYTEST) tests/unit/ -v

test-integration:
	$(PYTEST) tests/integration/ -v

test-e2e:
	$(PYTEST) tests/e2e/ -v -m e2e

eval:
	$(PYTHON) scripts/run_eval.py

eval-mock:
	$(PYTHON) scripts/run_eval.py --mock

logs:
	$(PYTHON) scripts/tail_logs.py --follow

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

deploy:
	bash deploy/deploy.sh
