import os, subprocess

PG_URL = os.getenv(
    "PG_URL", "postgresql://postgres:postgres@host.docker.internal:5433/drug_safety"
)

subprocess.check_call([
    "pg_restore",
    "--clean", "--if-exists", "--no-owner",
    "--dbname", PG_URL,
    "data/dumps/pg_drug_safety.dump",
])
print("PostgreSQL restored.")