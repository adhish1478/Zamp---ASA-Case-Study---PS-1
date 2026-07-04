.PHONY: setup run-backend test-extraction

setup:
	pip install -r requirements.txt
	playwright install chromium

run-backend:
	uvicorn backend.main:app --reload --port 8000

test-extraction:
	python3 backend/pipeline/test_extraction.py
