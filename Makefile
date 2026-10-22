.PHONY: collect process run test validate

PYTHON ?= python3

collect:
	$(PYTHON) -m src.main collect --dry-run

process:
	$(PYTHON) -m src.main process

run:
	$(PYTHON) -m src.main run-daily --dry-run

test:
	$(PYTHON) -m pytest

validate:
	$(PYTHON) scripts/validate_imports.py
	$(PYTHON) -m pytest
