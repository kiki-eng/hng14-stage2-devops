#!/usr/bin/env bash
set -euo pipefail

TIMEOUT=${TIMEOUT:-120}
INTERVAL=3
FRONTEND_URL="http://localhost:3000"

wait_for_service() {
  local url="$1"
  local elapsed=0
  echo "Waiting for $url ..."
  while [ "$elapsed" -lt "$TIMEOUT" ]; do
    if curl -fsS "$url" > /dev/null 2>&1; then
      echo "$url is ready (${elapsed}s)"
      return 0
    fi
    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
  done
  echo "Timed out after ${TIMEOUT}s waiting for $url"
  return 1
}

echo "==> Starting services"
docker compose up -d

echo "==> Waiting for frontend health"
wait_for_service "${FRONTEND_URL}/health"

echo "==> Submitting a job"
JOB_ID=$(curl -fsS -X POST "${FRONTEND_URL}/submit" | python3 -c "import sys, json; print(json.load(sys.stdin)['job_id'])")
echo "Job ID: $JOB_ID"

echo "==> Polling job status"
elapsed=0
while [ "$elapsed" -lt "$TIMEOUT" ]; do
  STATUS=$(curl -fsS "${FRONTEND_URL}/status/${JOB_ID}" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
  echo "  Status: $STATUS (${elapsed}s)"
  if [ "$STATUS" = "completed" ]; then
    echo "==> Integration test passed!"
    docker compose down
    exit 0
  fi
  sleep "$INTERVAL"
  elapsed=$((elapsed + INTERVAL))
done

echo "==> Job did not complete within ${TIMEOUT}s"
docker compose logs
docker compose down
exit 1
