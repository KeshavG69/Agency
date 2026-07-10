#!/bin/sh
# Entry-point that dispatches on $SERVICE_ROLE so the same image can run the API,
# the Celery worker, the Celery beat scheduler, or all of them together.
#
#   SERVICE_ROLE=web          -> API only
#   SERVICE_ROLE=worker       -> Celery worker only
#   SERVICE_ROLE=beat         -> Celery beat (scheduler) only — run exactly ONE
#   SERVICE_ROLE=worker-beat  -> worker + beat in one container
#   SERVICE_ROLE=all (default)-> API + worker + beat in one container
set -e

ROLE="${SERVICE_ROLE:-all}"
PORT="${PORT:-8000}"
CONCURRENCY="${CELERY_CONCURRENCY:-15}"

WEB="uvicorn app.server:app --host 0.0.0.0 --port ${PORT}"
WORKER="celery -A app.worker.celery_app worker --pool=threads --concurrency=${CONCURRENCY} --loglevel=info"
BEAT="celery -A app.worker.celery_app beat --loglevel=info"



echo "Starting Collecct backend with SERVICE_ROLE=${ROLE}"

case "$ROLE" in
  web)         exec $WEB ;;
  worker)      exec $WORKER ;;
  beat)        exec $BEAT ;;
  worker-beat) $WORKER & exec $BEAT ;;
  all)         $WORKER & $BEAT & exec $WEB ;;
  *) echo "Unknown SERVICE_ROLE='${ROLE}' (use web|worker|beat|worker-beat|all)"; exit 1 ;;
esac
