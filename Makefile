PYTHON ?= .venv/bin/python
export UV_CACHE_DIR := $(CURDIR)/.uv-cache

.PHONY: check verify http browser modules migrations package benchmark
check:
	$(PYTHON) -m pytest tests/ -q
	git diff --check

browser:
	$(PYTHON) scripts/verify.py --suite browser

http:
	$(PYTHON) scripts/verify.py --suite http

modules:
	$(PYTHON) scripts/verify.py --suite modules

verify:
	$(PYTHON) scripts/verify.py

migrations:
	$(PYTHON) -m pytest tests/test_migrations.py tests/test_manifest.py tests/test_backup_checks.py -q

package:
	uv build --out-dir data/packages

benchmark:
	$(PYTHON) -m tests.benchmarks.catalog --output data/benchmarks/catalog.json
	$(PYTHON) -m tests.benchmarks.pipeline --output data/benchmarks/pipeline.json
