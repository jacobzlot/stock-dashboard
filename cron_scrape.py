#!/usr/bin/env python3
"""
cron_scrape.py — Triggered by Railway cron to refresh stock data.

This script:
1. Reads tickers from the existing database
2. Scrapes fresh data from FinViz
3. Updates the SQLite database

Set up in Railway as a cron job service:
  - Schedule: 0 6 * * 1   (weekly, Monday 6 AM UTC)
  - Or:       0 6 1 * *   (monthly, 1st of month 6 AM UTC)
  - Command:  python cron_scrape.py
"""
import sqlite3
import json
import os
import sys
import time
from datetime import datetime, date

# Import from local modules
from scraper import FinvizScraper, split_multi_value_fields
from load_data import clean_value, clean_company_name, parse_finviz_key

DB_PATH = os.environ.get("DB_PATH", "stocks.db")
BATCH_DELAY = float(os.environ.get("SCRAPE_DELAY", "2.5"))  # seconds between requests
OUTPUT_JSON = "stock_data_latest.json"


def get_tickers_from_db():
    """Get all tickers from the current database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT ticker FROM stocks ORDER BY ticker")
    tickers = [row[0] for row in cursor.fetchall()]
    conn.close()
    return tickers


def get_tickers_from_csv(csv_path):
    """Fallback: read tickers from CSV if available."""
    import csv
    tickers = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = (row.get('Exchange:Ticker') or row.get('Ticker') or '').strip()
                if ticker:
                    tickers.append(ticker)
    except FileNotFoundError:
        pass
    return tickers


def update_database(stocks):
    """Insert/update scraped stocks into the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = date.today().isoformat()
    updated = 0

    for stock in stocks:
        ticker = stock.get('ticker')
        if not ticker:
            continue

        company_name = clean_company_name(stock.get('company_name'))

        columns = ['ticker', 'company_name', 'last_updated']
        values = [ticker, company_name, datetime.now()]

        for key, value in stock.items():
            if key in ['ticker', 'company_name', 'industry', 'sector', 'scraped_at']:
                continue
            db_column = parse_finviz_key(key)
            if db_column is None:
                continue
            cleaned = clean_value(value)
            if cleaned is not None:
                columns.append(db_column)
                values.append(cleaned)

        # Build UPDATE on conflict
        placeholders = ','.join(['?' for _ in values])
        columns_str = ','.join(columns)

        update_parts = []
        for col in columns:
            if col != 'ticker':
                update_parts.append(f"{col} = excluded.{col}")
        update_str = ','.join(update_parts)

        sql = f"""INSERT INTO stocks ({columns_str}) VALUES ({placeholders})
                  ON CONFLICT(ticker) DO UPDATE SET {update_str}"""
        cursor.execute(sql, values)

        # Also insert history row
        history_data = [
            ticker, today, stock.get('price'), stock.get('market_cap'), stock.get('volume'),
            stock.get('p_e'), stock.get('p_s'), stock.get('p_b'), stock.get('profit_margin'),
            stock.get('revenue_growth_ttm'), stock.get('rsi'), stock.get('beta'),
            stock.get('insider_own'), stock.get('inst_own'), stock.get('debt_to_equity'),
            stock.get('target_price'), stock.get('recommendation'),
            stock.get('perf_week'), stock.get('perf_month'),
            stock.get('perf_quarter'), stock.get('perf_year')
        ]
        cursor.execute("""INSERT OR IGNORE INTO stock_history
            (ticker, date, price, market_cap, volume, pe_ratio, ps_ratio, pb_ratio,
             profit_margin, revenue_growth_ttm, rsi, beta, insider_own, inst_own,
             debt_to_equity, target_price, recommendation,
             perf_week, perf_month, perf_quarter, perf_year)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [clean_value(v) for v in history_data])

        updated += 1
        if updated % 100 == 0:
            conn.commit()
            print(f"  Updated {updated} stocks...")

    conn.commit()
    conn.close()
    return updated


def main():
    print(f"{'='*60}")
    print(f"  Stock Data Refresh — {datetime.now().isoformat()}")
    print(f"{'='*60}")

    # Get tickers
    tickers = get_tickers_from_db()
    if not tickers:
        csv_path = os.environ.get("CSV_PATH", "StockSource.csv")
        tickers = get_tickers_from_csv(csv_path)

    if not tickers:
        print("ERROR: No tickers found in database or CSV!")
        sys.exit(1)

    print(f"Found {len(tickers)} tickers to scrape")
    est_time = len(tickers) * BATCH_DELAY / 60
    print(f"Estimated time: {est_time:.0f} minutes (delay={BATCH_DELAY}s)")

    # Scrape
    scraper = FinvizScraper()
    start = datetime.now()
    results, affiliates = scraper.scrape_multiple(tickers, delay=BATCH_DELAY)
    duration = datetime.now() - start

    print(f"\nScraped {len(results)}/{len(tickers)} stocks in {duration}")
    if affiliates:
        print(f"  Skipped {len(affiliates)} affiliates")

    # Save JSON backup
    with open(OUTPUT_JSON, 'w') as f:
        json.dump({
            'scraped_at': start.isoformat(),
            'duration_seconds': duration.total_seconds(),
            'total_stocks': len(tickers),
            'successful': len(results),
            'stocks': results
        }, f, indent=2, default=str)
    print(f"Saved JSON backup: {OUTPUT_JSON}")

    # Update database
    print("Updating database...")
    updated = update_database(results)
    print(f"Updated {updated} stocks in database")

    print(f"\n{'='*60}")
    print(f"  COMPLETE — {datetime.now().isoformat()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
