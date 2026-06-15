#!/usr/bin/env bash
# Apply the repo-tracked govcon customization to the running EspoCRM and rebuild.
# Re-run any time you change files under crm/customization/.
set -euo pipefail
cd "$(dirname "$0")"

CID="$(docker compose ps -q espocrm)"
if [ -z "$CID" ]; then
    echo "EspoCRM container is not running. Run: docker compose up -d"
    exit 1
fi

echo "Copying customization into container..."
docker cp customization/Espo/Custom/. "$CID:/var/www/html/custom/Espo/Custom/"
docker exec "$CID" chown -R www-data:www-data /var/www/html/custom/Espo/Custom

echo "Rebuilding EspoCRM..."
docker exec -u www-data "$CID" php command.php rebuild

echo "Done. Refresh http://localhost:8080 (hard-refresh to clear the client cache)."
