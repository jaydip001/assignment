PY := .venv/bin/python

.PHONY: install run-simulator run-service loadgen-demo \
        scenario1 scenario2 scenario3 stream-demo bench bench-stretch

install:
	python3 -m venv .venv
	.venv/bin/pip install --quiet --upgrade pip
	.venv/bin/pip install --quiet -r requirements.txt

run-simulator:
	$(PY) -m uvicorn simulator:app --port 9000 --log-level warning

run-service:
	$(PY) -m uvicorn service:app --port 8000 --log-level warning

loadgen-demo:
	$(PY) loadgen.py --rate 200 --duration 30 --model model-a=1.0

stream-demo:
	curl -N -X POST 127.0.0.1:8000/v1/requests/stream -H 'content-type: application/json' \
	  -d '{"model": "model-a", "estimated_tokens": 500, "payload": {"prompt": "hi"}}'

scenario1:
	$(PY) scenarios.py 1

scenario2:
	$(PY) scenarios.py 2

scenario3:
	$(PY) scenarios.py 3

bench:
	$(PY) bench.py --target-rps 300000 --duration 30 --out reports/bench_300k.json

bench-stretch:
	$(PY) bench.py --target-rps 1000000 --duration 30 --out reports/bench_1m.json
