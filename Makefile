.PHONY: setup run-backend run-frontend test-extraction test-structuring test-matching test-api test-all

setup:
	pip install -r requirements.txt
	playwright install chromium
	cd frontend && npm install

run-backend:
	uvicorn backend.api:app --reload --port 8000 --app-dir .

run-frontend:
	cd frontend && npm run dev

test-extraction:
	python3 backend/pipeline/test_extraction.py

test-structuring:
	python3 backend/pipeline/test_structuring.py

test-matching:
	python3 backend/matching/test_matching.py

test-api:
	python3 backend/test_api.py

test-all:
	python3 backend/pipeline/test_extraction.py
	python3 backend/pipeline/test_structuring.py
	python3 backend/matching/test_matching.py
	python3 backend/test_api.py
