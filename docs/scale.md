# Load test results (`docs/scale.md`)

Measured on 2026-10-17 with `python -m backend.loadtest`: two short runs of each set-up (the second run is the one tabulated below). These are real numbers from one machine, so read them as a starting point, not as a promise.

## Setup (what these numbers depend on)

- **Machine:** Apple M4 Pro laptop, 48 GB. The load generator (Python threads) and the API ran on the same machine and competed for the same CPU, so the API alone would likely do better.
- **Database:** PostgreSQL 15.19 in Docker on the same machine, default settings, freshly created, **1,200 decisions** (about 3 evidence items each). Tables this small say nothing about behaviour at millions of rows.
- **API:** `uvicorn decision_api.app:app`, `ENV=dev`, fake model (no real LLM calls), explanation rate limit and cache switched off so the work is really done. Only an HTTP 200 counts as success, and every step below shows 0 errors.
- **Lender:** one throwaway lender with no encryption, except where stated. Sandbox decisions (`backend/sandbox.py`), each with 3 pieces of evidence.
- **Ingest:** a decision is accepted, hash-chained and stored in one transaction, with its access-log row.
- **Length:** each step is a short burst (200 to 600 requests, a fraction of a second to a few seconds), not a sustained load.

## What the three set-ups showed

"New connection" means every request opens its own database connection (the default). "Reused connections" means `DT_DB_POOL_SIZE=8`: each worker keeps up to 8 open and resets them so one lender's identity can never reach the next request (tested, including 48 simultaneous requests from two lenders). With reused connections the total open to the database is workers times pool size (4 x 8 = 32 here), which must stay under the database's own limit.

| Measure | New connection, 1 worker | 8 reused connections, 1 worker | 8 reused connections, 4 workers |
|---|---|---|---|
| Read a decision, 1 at a time (p50) | 19.3 ms | 5.9 ms | 6.3 ms |
| Read a decision, 8 at once (per second / p50) | 262 / 29 ms | 377 / 21 ms | 460 / 17 ms |
| Read a decision, 32 at once (per second / p50) | 158 / 194 ms | 363 / 87 ms | 465 / 68 ms |
| Ingest, 8 at once (per second / p50) | 248 / 31 ms | 313 / 25 ms | 422 / 18 ms |
| Ingest, 32 at once (per second / p50) | 165 / 184 ms | 300 / 103 ms | 440 / 69 ms |
| List the 100 newest, 8 at once (per second) | 278 | 420 | 599 |
| Seal proof, 32 at once (per second) | 164 | 300 | 547 |
| Explain a decision (no model), 8 at once (per second / p50) | 75 / 105 ms | 110 / 70 ms | 139 / 57 ms |

What this suggests: reusing connections cuts the time of a single read by about two thirds (19 ms to 6 ms) and removes the sharp slowdown at 32 at once for reads, ingest, lists and proofs; more workers add throughput. The explanation step is different: it is CPU-bound, stays slow at 32 at once whatever the connection set-up, and also got slower between my two runs because the second run includes extra checks added in between (a lookup of the customer's choice and of whether the decision is protected on every call). It does **not** show how the system behaves with a large database, many lenders, a real model, a real network, or hardware other than this laptop.

Sealing 1,200 records took about 0.3 to 0.4 s, and checking every seal for those 1,200 records took about 0.3 s.

## How much runs vary

Each set-up was run twice. Read, 8 at once, per second, first run then second: new connection 257 then 263, reused 401 then 377, reused with 4 workers 522 then 460, protected evidence 348 then 342. So two runs of the same set-up differed by up to 12%, and any difference smaller than that between set-ups should be treated as noise.

## Protected (encrypted) evidence

With protected evidence on (1 worker, 8 reused connections), the second run gave ingest 299/s at 8 at once (against 313/s without) and reads 342/s (against 377/s), about 5 to 9% lower, which is inside the 12% spread above. Nothing here shows a meaningful cost, and nothing shows there is none.

## Against the volumes in the design

The design assumed an average of about 35 decisions a second and bursts of about 350 a second across all lenders. One worker with reused connections ingested about 300 a second here, and four workers about 420 to 440 a second, so the target peak is within reach of a small group of workers **on this set-up**, with small tables. That is a reason to run a bigger test, not evidence that the target is met. Things not tested: millions of rows (index size, vacuum; searching by customer reference is not index-backed, so a search for one customer's decisions reads the lender's whole list), many lenders at once, long-running batches, a real model, failover.

## How to repeat it

```
python -m data.seed --reset           # or any database that has had the updates applied
DT_DB_POOL_SIZE=8 uvicorn decision_api.app:app --port 8010 --workers 4
python -m backend.loadtest --api-url http://localhost:8010 --ingest 400 --concurrency 1,8,32 --label "my setup"
```

Add `--encrypt` to test a lender with protected evidence. Set `RECON_RATE_LIMIT_PER_MINUTE=0` and `RECON_CACHE_TTL_SECONDS=0` on the API to measure explanation work rather than the limiter.

## Raw output (second run)

### 1 worker, new connection per request

Only an HTTP 200 counts as success; the explanation rate limit and cache were switched off.

1200 decisions sent. Sealing 1200 records took 0.39 s; checking all seals (1200 records) took 0.3 s (ok: True).

| Step | Concurrency | Requests | Errors | Per second | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| ingest one decision | 1 | 400 | 0 | 51.9 | 18.9 | 22.4 | 26.8 |
| ingest one decision | 8 | 400 | 0 | 247.5 | 30.8 | 42.5 | 56.2 |
| ingest one decision | 32 | 400 | 0 | 165.0 | 183.6 | 346.6 | 359.8 |
| read a decision | 1 | 600 | 0 | 50.9 | 19.3 | 23.6 | 27.3 |
| check a record's chain | 1 | 600 | 0 | 52.8 | 18.6 | 22.7 | 26.2 |
| list 100 newest | 1 | 200 | 0 | 61.6 | 15.8 | 19.2 | 23.2 |
| read a decision | 8 | 600 | 0 | 262.5 | 28.9 | 42.2 | 51.1 |
| check a record's chain | 8 | 600 | 0 | 270.5 | 28.0 | 40.6 | 47.3 |
| list 100 newest | 8 | 200 | 0 | 277.8 | 27.0 | 38.8 | 45.5 |
| read a decision | 32 | 600 | 0 | 157.5 | 194.2 | 342.1 | 381.7 |
| check a record's chain | 32 | 600 | 0 | 181.1 | 163.4 | 292.0 | 338.6 |
| list 100 newest | 32 | 200 | 0 | 156.3 | 205.8 | 274.2 | 303.8 |
| get a seal proof | 1 | 600 | 0 | 54.0 | 18.3 | 21.4 | 23.9 |
| explain a decision (no model) | 1 | 200 | 0 | 19.5 | 50.8 | 57.9 | 69.1 |
| get a seal proof | 8 | 600 | 0 | 228.7 | 33.2 | 46.5 | 60.6 |
| explain a decision (no model) | 8 | 200 | 0 | 75.2 | 104.7 | 132.5 | 141.0 |
| get a seal proof | 32 | 600 | 0 | 164.5 | 178.3 | 327.8 | 386.9 |
| explain a decision (no model) | 32 | 200 | 0 | 46.3 | 680.1 | 860.0 | 932.8 |

### 1 worker, 8 reused connections

Only an HTTP 200 counts as success; the explanation rate limit and cache were switched off.

1200 decisions sent. Sealing 1200 records took 0.31 s; checking all seals (1200 records) took 0.3 s (ok: True).

| Step | Concurrency | Requests | Errors | Per second | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| ingest one decision | 1 | 400 | 0 | 143.0 | 6.4 | 9.2 | 12.1 |
| ingest one decision | 8 | 400 | 0 | 313.0 | 25.2 | 28.6 | 34.8 |
| ingest one decision | 32 | 400 | 0 | 299.9 | 102.7 | 115.5 | 133.1 |
| read a decision | 1 | 600 | 0 | 165.3 | 5.9 | 7.2 | 8.4 |
| check a record's chain | 1 | 600 | 0 | 177.4 | 5.6 | 6.4 | 7.9 |
| list 100 newest | 1 | 200 | 0 | 210.9 | 4.6 | 5.1 | 5.8 |
| read a decision | 8 | 600 | 0 | 377.1 | 20.9 | 24.2 | 26.4 |
| check a record's chain | 8 | 600 | 0 | 385.4 | 20.5 | 24.2 | 25.8 |
| list 100 newest | 8 | 200 | 0 | 420.5 | 18.4 | 21.7 | 25.3 |
| read a decision | 32 | 600 | 0 | 363.1 | 87.0 | 93.8 | 95.7 |
| check a record's chain | 32 | 600 | 0 | 386.2 | 81.8 | 86.0 | 88.4 |
| list 100 newest | 32 | 200 | 0 | 410.4 | 73.8 | 78.3 | 84.4 |
| get a seal proof | 1 | 600 | 0 | 149.3 | 6.3 | 8.6 | 9.2 |
| explain a decision (no model) | 1 | 200 | 0 | 24.8 | 39.7 | 46.6 | 53.8 |
| get a seal proof | 8 | 600 | 0 | 322.9 | 24.6 | 28.0 | 30.1 |
| explain a decision (no model) | 8 | 200 | 0 | 109.7 | 70.5 | 94.0 | 100.6 |
| get a seal proof | 32 | 600 | 0 | 300.0 | 104.3 | 114.3 | 127.6 |
| explain a decision (no model) | 32 | 200 | 0 | 66.2 | 488.8 | 676.0 | 786.0 |

### 4 workers, 8 reused connections each

Only an HTTP 200 counts as success; the explanation rate limit and cache were switched off.

1200 decisions sent. Sealing 1200 records took 0.35 s; checking all seals (1200 records) took 0.3 s (ok: True).

| Step | Concurrency | Requests | Errors | Per second | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| ingest one decision | 1 | 400 | 0 | 149.2 | 6.4 | 7.7 | 9.3 |
| ingest one decision | 8 | 400 | 0 | 422.0 | 18.0 | 22.4 | 51.8 |
| ingest one decision | 32 | 400 | 0 | 439.6 | 68.9 | 88.8 | 108.0 |
| read a decision | 1 | 600 | 0 | 153.7 | 6.3 | 7.7 | 11.4 |
| check a record's chain | 1 | 600 | 0 | 157.7 | 6.1 | 7.7 | 9.0 |
| list 100 newest | 1 | 200 | 0 | 187.1 | 5.1 | 6.6 | 7.6 |
| read a decision | 8 | 600 | 0 | 460.5 | 16.8 | 21.0 | 23.9 |
| check a record's chain | 8 | 600 | 0 | 468.8 | 16.6 | 20.2 | 25.5 |
| list 100 newest | 8 | 200 | 0 | 598.9 | 12.8 | 16.2 | 18.0 |
| read a decision | 32 | 600 | 0 | 464.6 | 67.6 | 91.7 | 102.0 |
| check a record's chain | 32 | 600 | 0 | 492.1 | 58.9 | 91.6 | 102.5 |
| list 100 newest | 32 | 200 | 0 | 624.7 | 45.3 | 64.2 | 70.3 |
| get a seal proof | 1 | 600 | 0 | 148.3 | 6.4 | 9.0 | 11.6 |
| explain a decision (no model) | 1 | 200 | 0 | 22.6 | 42.6 | 57.6 | 67.0 |
| get a seal proof | 8 | 600 | 0 | 527.8 | 14.8 | 17.8 | 19.6 |
| explain a decision (no model) | 8 | 200 | 0 | 138.9 | 56.7 | 62.7 | 68.4 |
| get a seal proof | 32 | 600 | 0 | 546.7 | 55.6 | 72.9 | 77.3 |
| explain a decision (no model) | 32 | 200 | 0 | 145.4 | 212.9 | 241.5 | 255.1 |

### 1 worker, 8 reused connections, protected (encrypted) evidence

Only an HTTP 200 counts as success; the explanation rate limit and cache were switched off.

1200 decisions sent. Sealing 1200 records took 0.32 s; checking all seals (1200 records) took 0.3 s (ok: True).

| Step | Concurrency | Requests | Errors | Per second | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| ingest one decision | 1 | 400 | 0 | 137.6 | 6.8 | 8.5 | 10.3 |
| ingest one decision | 8 | 400 | 0 | 298.9 | 26.4 | 30.2 | 34.7 |
| ingest one decision | 32 | 400 | 0 | 285.7 | 108.1 | 118.0 | 153.7 |
| read a decision | 1 | 600 | 0 | 159.5 | 6.2 | 7.2 | 8.1 |
| check a record's chain | 1 | 600 | 0 | 138.1 | 6.8 | 8.8 | 10.5 |
| list 100 newest | 1 | 200 | 0 | 192.7 | 4.9 | 6.8 | 7.3 |
| read a decision | 8 | 600 | 0 | 341.9 | 23.1 | 26.7 | 28.4 |
| check a record's chain | 8 | 600 | 0 | 358.7 | 22.1 | 25.5 | 26.9 |
| list 100 newest | 8 | 200 | 0 | 415.8 | 18.7 | 23.1 | 24.3 |
| read a decision | 32 | 600 | 0 | 341.1 | 91.8 | 99.3 | 102.7 |
| check a record's chain | 32 | 600 | 0 | 345.6 | 91.1 | 96.1 | 99.2 |
| list 100 newest | 32 | 200 | 0 | 388.0 | 76.4 | 85.9 | 88.2 |
| get a seal proof | 1 | 600 | 0 | 144.5 | 6.4 | 9.2 | 11.6 |
| explain a decision (no model) | 1 | 200 | 0 | 24.1 | 40.9 | 47.6 | 53.2 |
| get a seal proof | 8 | 600 | 0 | 319.1 | 24.9 | 28.1 | 30.0 |
| explain a decision (no model) | 8 | 200 | 0 | 104.4 | 73.8 | 97.6 | 108.0 |
| get a seal proof | 32 | 600 | 0 | 314.9 | 100.5 | 106.0 | 107.9 |
| explain a decision (no model) | 32 | 200 | 0 | 60.3 | 525.6 | 760.2 | 821.8 |

