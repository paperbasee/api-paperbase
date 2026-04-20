web: bash entrypoint.sh
worker: celery -A config worker --loglevel=info
beat: celery -A config beat --loglevel=info