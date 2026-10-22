#!/bin/sh
# Creates the bucket if it doesn't exist yet. Safe to run repeatedly.
set -e
mc alias set local http://minio:9000 "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
mc mb --ignore-existing "local/$MINIO_BUCKET"
echo "bucket '$MINIO_BUCKET' ready"
