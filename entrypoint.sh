#!/bin/bash
set -e

DB_PATH="${DB_PATH:-/data/stocks.db}"
PORT="${PORT:-5000}"

# If the database doesn't exist on the volume, copy the seed database
if [ ! -f "$DB_PATH" ]; then
    echo "First run: seeding database from /app/stocks.db..."
    if [ -f /app/stocks.db ]; then
        cp /app/stocks.db "$DB_PATH"
        echo "Database seeded successfully."
    else
        echo "No seed database found. Running setup..."
        python /app/setup_database.py "$DB_PATH"
        echo "Empty database created."
    fi
fi

echo "Database: $DB_PATH"
echo "Starting on port: $PORT"

exec gunicorn app:app --bind "0.0.0.0:${PORT}" --workers 2 --timeout 120