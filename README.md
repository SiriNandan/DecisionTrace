# DecisionTrace

> **ALL DATA IS SYNTHETIC (`SYNTHETIC DATA - FOR DEMONSTRATION.`)**

DecisionTrace is a local-first reference implementation that records loan decisions with hash-chained, decision-time evidence and later reconstructs a cited explanation from the preserved record.

The system **supports audit and explanation** by preserving immutable decision snapshots that are **tamper-evident against a retained reference**.

## Stack

- **Backend**: Python 3.11, FastAPI, PostgreSQL 15 (Docker), `psycopg` 3, Pydantic v2, `pytest`, `scikit-learn`
- **Frontend**: Vite + React + TypeScript + Jest

## Quickstart (Local Database)

1. Copy `.env.example` to `.env` (or use default local values):
   ```bash
   cp .env.example .env
   ```
2. Start PostgreSQL 15:
   ```bash
   docker compose up -d
   ```
3. Verify connectivity:
   ```bash
   docker compose exec postgres psql -U decisiontrace -d decisiontrace -c "SELECT 1;"
   ```

