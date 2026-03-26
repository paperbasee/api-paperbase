#!/usr/bin/env bash

set -e
set -u

echo "Loading environment..."

# Capture pre-existing exported variables so system/CI values can override .env.
declare -A pre_existing_map
while IFS='=' read -r name value; do
  pre_existing_map["$name"]="$value"
done < <(env)

ENV_FILE="${ENV_FILE:-.env.railway}"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a

  # Restore any values that were already exported before loading env file.
  for name in "${!pre_existing_map[@]}"; do
    export "$name=${pre_existing_map[$name]}"
  done
fi

export DEBUG="${DEBUG:-False}"
export ADMIN_PATH="${ADMIN_PATH:-admin/}"
export ADMIN_URL_PATH="${ADMIN_URL_PATH:-$ADMIN_PATH}"

required_vars=(
  "SECRET_KEY"
  "DATABASE_URL"
  "ALLOWED_HOSTS"
  "CORS_ALLOWED_ORIGINS"
  "CSRF_TRUSTED_ORIGINS"
  "R2_ACCESS_KEY_ID"
  "R2_SECRET_ACCESS_KEY"
  "R2_BUCKET_NAME"
  "R2_ENDPOINT_URL"
  "R2_PUBLIC_URL"
  "REDIS_URL"
)

missing_vars=()
for var_name in "${required_vars[@]}"; do
  if [ -z "${!var_name:-}" ]; then
    missing_vars+=("$var_name")
  fi
done

if [ "${#missing_vars[@]}" -gt 0 ]; then
  echo "Error: missing required environment variable(s): ${missing_vars[*]}" >&2
  echo "Provide them via ${ENV_FILE} or exported system environment variables." >&2
  exit 1
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "Error: DATABASE_URL is required but not set." >&2
  exit 1
fi

echo "Environment loaded successfully"

if [ "$#" -eq 0 ]; then
  echo "No command provided. Opening Django shell by default."
  echo "Running Django command: shell"
  exec python3 manage.py shell
fi

if [ "$1" = "seed-email-templates" ]; then
  shift
  if [ "${1:-}" = "--update-existing" ]; then
    echo "Running Django command: seed_email_templates --update-existing"
    exec python3 manage.py seed_email_templates --update-existing
  fi
  echo "Running Django command: seed_email_templates"
  exec python3 manage.py seed_email_templates
fi

echo "Running Django command: $*"
exec python3 manage.py "$@"
