#!/bin/bash
# Script to connect to database and run Django management commands
# Usage: ./db.sh <command> [args...]
# Examples:
#   ./db.sh createsuperuser
#   ./db.sh migrate
#   ./db.sh shell
#   ./db.sh dbshell

# Edit the variables below with your Railway values
export SECRET_KEY="4&s^)dm549a@^t=z+5jm1@#uxkfh0#97s!mdr4)o)v8uwlh-)%"
export DATABASE_URL="postgresql://postgres:wYimohSrAMBxAmJmCqwDmFfKyocyMyKx@crossover.proxy.rlwy.net:52294/railway"
export R2_ACCOUNT_ID="${R2_ACCOUNT_ID}"
export R2_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}"
export R2_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}"
export R2_BUCKET_NAME="${R2_BUCKET_NAME}"

# Optional variables (can be empty)
export ADMIN_PATH="${ADMIN_PATH:-${ADMIN_URL_PATH:-admin/}}"
# Keep legacy variable in sync for any older scripts/tools.
export ADMIN_URL_PATH="${ADMIN_PATH}"
export CSRF_TRUSTED_ORIGINS="${CSRF_TRUSTED_ORIGINS:-}"
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-}"
export CSRF_COOKIE_DOMAIN="${CSRF_COOKIE_DOMAIN:-}"
export R2_CUSTOM_DOMAIN="${R2_CUSTOM_DOMAIN:-}"
export DEBUG="${DEBUG:-False}"

# Run the Django management command with any provided arguments
# If no command is provided, default to shell for interactive use
if [ $# -eq 0 ]; then
    echo "No command provided. Opening Django shell..."
    echo "Usage: ./db.sh <command> [args...]"
    echo "Examples: ./db.sh createsuperuser, ./db.sh migrate, ./db.sh shell"
    python3 manage.py shell
else
    python3 manage.py "$@"
fi