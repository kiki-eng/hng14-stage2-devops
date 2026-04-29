#!/usr/bin/env bash
# Integration smoke-test for the anomaly detection stack.
# Run from the repo root on the VPS after `docker compose up -d`.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

info "Checking Docker containers..."
for svc in nginx nextcloud db detector; do
  if docker compose ps --status running | grep -q "$svc"; then
    pass "$svc is running"
  else
    fail "$svc is NOT running"
  fi
done

info "Checking Nginx JSON access log volume..."
if docker volume inspect HNG-nginx-logs &>/dev/null; then
  pass "HNG-nginx-logs volume exists"
else
  fail "HNG-nginx-logs volume not found"
fi

info "Checking Nextcloud responds via Nginx..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost/ || true)
if [[ "$HTTP_CODE" =~ ^(200|302|301|401)$ ]]; then
  pass "Nextcloud reachable (HTTP $HTTP_CODE)"
else
  fail "Nextcloud not reachable (HTTP $HTTP_CODE)"
fi

info "Checking detector dashboard..."
DASH_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/ || true)
if [[ "$DASH_CODE" == "200" ]]; then
  pass "Dashboard reachable (HTTP $DASH_CODE)"
else
  fail "Dashboard not reachable (HTTP $DASH_CODE)"
fi

info "Checking dashboard metrics API..."
METRICS=$(curl -s http://localhost:8080/api/metrics || true)
if echo "$METRICS" | python3 -m json.tool &>/dev/null; then
  pass "Metrics API returns valid JSON"
else
  fail "Metrics API did not return valid JSON"
fi

info "Checking audit log file exists..."
if docker compose exec detector test -f /var/log/detector/audit.log; then
  pass "Audit log exists"
else
  info "Audit log not yet created (will appear on first event)"
fi

info "Generating test traffic (50 rapid requests)..."
for i in $(seq 1 50); do
  curl -s -o /dev/null http://localhost/ &
done
wait
sleep 2

info "Verifying log lines are being parsed..."
LINES=$(curl -s http://localhost:8080/api/metrics | python3 -c "import sys,json; print(json.load(sys.stdin)['lines_processed'])" 2>/dev/null || echo 0)
if [[ "$LINES" -gt 0 ]]; then
  pass "Detector has processed $LINES log lines"
else
  info "No log lines processed yet — may need more traffic"
fi

echo ""
echo -e "${GREEN}=== Integration tests complete ===${NC}"
