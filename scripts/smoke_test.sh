#!/usr/bin/env bash
# End-to-end smoke test against a running instance (used by CI on the built container).
# Usage: scripts/smoke_test.sh [base_url]
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
json() { python3 -c "import sys, json; print(json.load(sys.stdin)$1)"; }
post() { curl -fsS -X POST "$BASE_URL$1" -H 'Content-Type: application/json' -d "$2"; }

echo "Waiting for $BASE_URL/health ..."
ready=0
for _ in $(seq 1 30); do
  if curl -fsS "$BASE_URL/health" > /dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[ "$ready" = 1 ] || { echo "service did not become healthy" >&2; exit 1; }

suffix="$(date +%s)$RANDOM"
cash=$(post /accounts "{\"code\":\"CASH$suffix\",\"name\":\"Cash\",\"type\":\"asset\"}" | json '["id"]')
sales=$(post /accounts "{\"code\":\"SALES$suffix\",\"name\":\"Sales\",\"type\":\"income\"}" | json '["id"]')

tx=$(post /transactions "{\"date\":\"2020-01-01\",\"description\":\"smoke\",\"postings\":[
  {\"account_id\":\"$cash\",\"side\":\"debit\",\"amount\":\"42.50\"},
  {\"account_id\":\"$sales\",\"side\":\"credit\",\"amount\":\"42.50\"}]}" | json '["id"]')

balance=$(curl -fsS "$BASE_URL/accounts/$cash/balance" | json '["balance"]')
[ "$balance" = "42.50" ] || { echo "unexpected balance: $balance" >&2; exit 1; }

post "/transactions/$tx/reverse" '{}' > /dev/null
balance=$(curl -fsS "$BASE_URL/accounts/$cash/balance" | json '["balance"]')
[ "$balance" = "0.00" ] || { echo "balance not zero after reversal: $balance" >&2; exit 1; }

balanced=$(curl -fsS "$BASE_URL/reports/trial-balance" | json '["balanced"]')
[ "$balanced" = "True" ] || { echo "trial balance is not balanced" >&2; exit 1; }

echo "Smoke test passed."
