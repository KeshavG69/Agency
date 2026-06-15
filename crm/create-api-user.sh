#!/usr/bin/env bash
# Create a service Role + API user in EspoCRM and print the API key.
# One-time bootstrap. The API key is a per-deployment secret (not committed).
set -euo pipefail
cd "$(dirname "$0")"

BASE=http://localhost:8080/api/v1
AUTH="Espo-Authorization: $(printf 'admin:Esp0CRM!change' | base64)"

echo "Creating service role..."
ROLE_PAYLOAD='{
  "name": "AI Agency Service",
  "assignmentPermission": "all",
  "data": {
    "Opportunity": {"create":"yes","read":"all","edit":"all","delete":"yes","stream":"yes"},
    "Account":     {"create":"yes","read":"all","edit":"all","delete":"yes"},
    "Contact":     {"create":"yes","read":"all","edit":"all","delete":"yes"},
    "Call":        {"create":"yes","read":"all","edit":"all","delete":"yes"},
    "Meeting":     {"create":"yes","read":"all","edit":"all","delete":"yes"},
    "Task":        {"create":"yes","read":"all","edit":"all","delete":"yes"}
  }
}'
ROLE_ID=$(curl -s -H "$AUTH" -H 'Content-Type: application/json' \
    -X POST "$BASE/Role" -d "$ROLE_PAYLOAD" \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "  role id: $ROLE_ID"

echo "Creating API user 'ai-agency'..."
USER_PAYLOAD=$(printf '{"userName":"ai-agency","type":"api","authMethod":"ApiKey","isActive":true,"rolesIds":["%s"]}' "$ROLE_ID")
RESP=$(curl -s -H "$AUTH" -H 'Content-Type: application/json' -X POST "$BASE/User" -d "$USER_PAYLOAD")

API_KEY=$(echo "$RESP" | python3 -c "import sys,json;print(json.load(sys.stdin).get('apiKey',''))")
if [ -z "$API_KEY" ]; then
    echo "FAILED. Raw response:"; echo "$RESP"; exit 1
fi

echo "ESPOCRM_API_KEY=$API_KEY" > .api-credentials
echo "ESPOCRM_BASE_URL=http://localhost:8080" >> .api-credentials
echo "  API key saved to crm/.api-credentials (gitignored)"
echo "  API_KEY=$API_KEY"
