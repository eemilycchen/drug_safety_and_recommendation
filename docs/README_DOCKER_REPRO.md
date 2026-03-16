## Drug Safety Demo – Docker Reproducibility Guide

### 1. Prerequisites

- Docker and Docker Compose installed
- Python 3.11+ on the host (for restore scripts)
- `pg_restore` and `mongorestore` available (Postgres + MongoDB tools)

You should have this folder structure after unzipping:

- `dsc 202/` (project root)
- `drug-safety-demo.tar` (Docker image tar, one level above or alongside)

---

### 2. Load the Docker image

From inside the project folder:

```bash
cd "/path/to/dsc 202"
docker load -i ../drug-safety-demo.tar
```

This loads the app image (e.g. `sanjana111/drug-safety-demo:latest`) into Docker.

---

### 3. Start backing services

If a `docker-compose.yml` is provided, start Postgres, MongoDB, and Qdrant:

```bash
docker compose up -d
```

Wait until all services are healthy.

---

### 4. Restore Sanjana’s exact data

From the project root:

```bash
cd "/path/to/dsc 202"

export PG_URL=postgresql://postgres:postgres@host.docker.internal:5433/drug_safety
export MONGO_URI=mongodb://host.docker.internal:27017
export MONGO_DB=drug_safety
```

Restore PostgreSQL:

```bash
python scripts/restore_pg.py
```

Restore MongoDB (FAERS + audit):

```bash
chmod +x scripts/restore_mongo.sh
./scripts/restore_mongo.sh
```

Restore Qdrant vectors:

```bash
chmod +x scripts/restore_qdrant.sh
./scripts/restore_qdrant.sh
```

At this point, your Postgres, Mongo, and Qdrant instances contain the same data as Sanjana’s environment.

---

### 5. Configure environment variables

If an `.env.example` file is present, create your `.env` from it:

```bash
cp .env.example .env
```

Then edit `.env` and, at minimum, set:

- `OPENFDA_API_KEY` (optional if you rely only on the shipped FAERS snapshot)

Leave `PG_URL`, `MONGO_URI`, `MONGO_DB`, `QDRANT_HOST`, and `QDRANT_PORT` as provided unless your ports differ.

---

### 6. Run the Streamlit app in Docker

```bash
docker run --rm -p 8501:8501 --env-file .env sanjana111/drug-safety-demo:latest
```

Open the app in your browser:

```text
http://localhost:8501
```

You now have:

- The same Synthea patient data in PostgreSQL
- The same FAERS evidence and safety-check audit logs in MongoDB
- The same BioLORD/Qdrant vector index for FAERS similarity
- The same Streamlit UI and configuration as the original environment

