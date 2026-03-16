#!/usr/bin/env bash
set -euo pipefail

MONGO_URI="${MONGO_URI:-mongodb://host.docker.internal:27017}"

mongorestore --uri="$MONGO_URI" \
  --archive="data/dumps/mongo_drug_safety.archive" \
  --drop

echo "MongoDB restored."