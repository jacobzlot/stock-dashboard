#!/bin/bash
set -e

DB_PATH="${DB_PATH:-/data/stocks.db}"

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
echo "Starting application..."

# Execute the CMD
exec "$@"
