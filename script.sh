#!/bin/bash

set -e

NETWORK_NAME="supertokens-network"
VOLUME_NAME="supertokens-postgres-data"

API_KEY="2b6cbbeb70b1ae499c1ceb1cf43d165db98a637de9dd44f83804f3523a2cbad7"
POSTGRES_PASSWORD="36d5fc5444cd96f4"

echo "Creating Docker network..."
docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || \
docker network create "$NETWORK_NAME"

echo "Creating Docker volume..."
docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1 || \
docker volume create "$VOLUME_NAME"

echo "Starting PostgreSQL..."
docker rm -f supertokens-postgres >/dev/null 2>&1 || true

docker run -d \
  --name supertokens-postgres \
  --network "$NETWORK_NAME" \
  -e POSTGRES_DB="supertokens" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e POSTGRES_USER="supertokens" \
  -v "$VOLUME_NAME":/var/lib/postgresql/data \
  --restart unless-stopped \
  postgres:15-alpine

echo "Waiting 10 seconds for PostgreSQL to start..."
sleep 10

echo "Starting SuperTokens..."
docker rm -f supertokens >/dev/null 2>&1 || true

docker run -d \
  --name supertokens \
  --network "$NETWORK_NAME" \
  -e API_KEYS="$API_KEY" \
  -e DISABLE_TELEMETRY="true" \
  -e POSTGRESQL_CONNECTION_URI="postgresql://supertokens:${POSTGRES_PASSWORD}@supertokens-postgres:5432/supertokens" \
  -p 3567:3567 \
  --restart unless-stopped \
  supertokens/supertokens-postgresql:7.0

echo ""
echo "==================================="
echo "SuperTokens deployment completed"
echo "Network : $NETWORK_NAME"
echo "Volume  : $VOLUME_NAME"
echo "API Key : $API_KEY"
echo "Port    : 3567"
echo "==================================="