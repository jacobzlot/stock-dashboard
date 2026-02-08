FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create data directory (Railway volume will be mounted here)
RUN mkdir -p /data

# Set env defaults
ENV PORT=5000
ENV DB_PATH=/data/stocks.db
ENV SHORTLIST_PATH=/data/shortlist.json
ENV FLASK_DEBUG=false

# On first run, copy seed database to volume if not present
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE ${PORT}

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120"]
