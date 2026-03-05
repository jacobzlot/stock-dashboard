#!/usr/bin/env python3
"""
cron_scrape.py -- Triggered by Railway cron to refresh stock data.

Uses Postgres if DATABASE_URL is set, otherwise SQLite.

Railway cron setup:
  - Schedule: 0 6 * * 1   (weekly Monday 6 AM UTC)
  - Command:  python cron_scrape.py

Test modes (run locally):
  python cron_scrape.py --test 5          # scrape only 5 tickers
  python cron_scrape.py --db-only         # skip scrape, use last JSON backup
"""
import os
import sys
import json
import re
import time
from datetime import datetime, date

from scraper import FinvizScraper, split_multi_value_fields
from load_data import clean_company_name, parse_finviz_key

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get("DB_PATH", "stocks.db")
USE_POSTGRES = DATABASE_URL is not None
BATCH_DELAY = float(os.environ.get("SCRAPE_DELAY", "2.5"))
OUTPUT_JSON = "stock_data_latest.json"


# ── Robust value cleaner ──────────────────────────────────────
# The old clean_value() from load_data.py doesn't strip % signs,
# causing "invalid input syntax for type numeric" in Postgres.
# This function handles all FinViz formatting.

def safe_numeric(value):
    """Convert a FinViz value to a Python number, stripping %, $, B, M, K, commas.
    Returns None for non-numeric or empty values. Returns float or int."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value or value in ('', '-', 'N/A', '--', '- -'):
        return None

    # Strip currency and percent signs
    value = value.replace('$', '').replace('%', '').replace(',', '').strip()

    # Handle multiplier suffixes: 1.5B, 300M, 12.5K
    multiplier = 1
    if value and value[-1] in ('B', 'b'):
        value = value[:-1]
        multiplier = 1_000_000_000
    elif value and value[-1] in ('M', 'm'):
        value = value[:-1]
        multiplier = 1_000_000
    elif value and value[-1] in ('K', 'k'):
        value = value[:-1]
        multiplier = 1_000
    elif value and value[-1] in ('T', 't'):
        value = value[:-1]
        multiplier = 1_000_000_000_000

    # Try to parse as number
    try:
        num = float(value.strip()) * multiplier
        if num == int(num) and multiplier == 1 and '.' not in value:
            return int(num)
        return round(num, 6)
    except (ValueError, TypeError):
        return None


def safe_text(value):
    """Clean a text value."""
    if value is None or value == '' or value == '-':
        return None
    if not isinstance(value, str):
        return str(value)
    return value.strip() or None

if USE_POSTGRES:
    import psycopg2

# Mapping from SQLite column names (what parse_finviz_key returns) to Postgres column names
SQLITE_TO_PG = {
    'price_change': 'price_change_pct',
    'profit_margin': 'profit_margin_pct',
    'operating_margin': 'operating_margin_pct',
    'gross_margin': 'gross_margin_pct',
    'roe': 'roe_pct',
    'roa': 'roa_pct',
    'roi': 'roi_pct',
    'roic': 'roic_pct',
    'eps_growth_ttm': 'eps_growth_ttm_pct',
    'revenue_growth_ttm': 'revenue_growth_ttm_pct',
    'eps_growth_next_y': 'eps_growth_next_y_pct',
    'eps_growth_next_5y': 'eps_growth_next_5y_pct',
    'eps_past_3y': 'eps_past_3y_pct',
    'eps_past_5y': 'eps_past_5y_pct',
    'sales_past_3y': 'sales_past_3y_pct',
    'sales_past_5y': 'sales_past_5y_pct',
    'sales_qyq': 'sales_qyq_pct',
    'eps_qoq': 'eps_qoq_pct',
    'volatility_week': 'volatility_week_pct',
    'volatility_month': 'volatility_month_pct',
    'insider_own': 'insider_own_pct',
    'insider_trans': 'insider_trans_pct',
    'inst_own': 'inst_own_pct',
    'inst_trans': 'inst_trans_pct',
    'short_float': 'short_float_pct',
    'dividend_yield': 'dividend_yield_pct',
    'dividend_yield_est': 'dividend_yield_est_pct',
    'payout_ratio': 'payout_ratio_pct',
    'dividend_gr_3y': 'dividend_gr_3y_pct',
    'dividend_gr_5y': 'dividend_gr_5y_pct',
    'eps_surprise': 'eps_surprise_pct',
    'sales_surprise': 'sales_surprise_pct',
    'perf_week': 'perf_week_pct',
    'perf_month': 'perf_month_pct',
    'perf_quarter': 'perf_quarter_pct',
    'perf_half_y': 'perf_half_y_pct',
    'perf_year': 'perf_year_pct',
    'perf_ytd': 'perf_ytd_pct',
    'perf_3y': 'perf_3y_pct',
    'perf_5y': 'perf_5y_pct',
    'perf_10y': 'perf_10y_pct',
}

PG_HISTORY_MAP = {
    'profit_margin': 'profit_margin_pct',
    'revenue_growth_ttm': 'revenue_growth_ttm_pct',
    'insider_own': 'insider_own_pct',
    'inst_own': 'inst_own_pct',
    'perf_week': 'perf_week_pct',
    'perf_month': 'perf_month_pct',
    'perf_quarter': 'perf_quarter_pct',
    'perf_year': 'perf_year_pct',
}


def get_tickers():
    """Get all tickers from the current database."""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks ORDER BY ticker")
        tickers = [r[0] for r in cur.fetchall()]
        conn.close()
    else:
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks ORDER BY ticker")
        tickers = [r[0] for r in cur.fetchall()]
        conn.close()
    return tickers


def update_postgres(stocks):
    """Insert/update scraped stocks into Postgres."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    today = date.today().isoformat()
    updated = 0
    errors = 0

    # Text columns that should NOT be run through safe_numeric
    TEXT_COLS = {'ex_dividend_date', 'ipo_date', 'earnings_date', 'option_short',
                 'company_name', 'sector', 'industry', 'market_index'}

    # Fix: reset stock_history serial sequence to avoid id collision
    try:
        cur.execute("SELECT setval('stock_history_id_seq', COALESCE((SELECT MAX(id) FROM stock_history), 0))")
        conn.commit()
    except Exception as e:
        print(f"  Note: could not reset sequence: {e}")
        conn.rollback()

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
            # Map to Postgres column name
            db_column = SQLITE_TO_PG.get(db_column, db_column)
            # Use the right cleaner for the column type
            if db_column in TEXT_COLS:
                cleaned = safe_text(value)
            else:
                cleaned = safe_numeric(value)
            if cleaned is not None:
                columns.append(db_column)
                values.append(cleaned)

        placeholders = ','.join(['%s'] * len(values))
        columns_str = ','.join(columns)
        update_parts = [f"{col} = EXCLUDED.{col}" for col in columns if col != 'ticker']
        update_str = ','.join(update_parts)

        sql = f"""INSERT INTO stocks ({columns_str}) VALUES ({placeholders})
                  ON CONFLICT (ticker) DO UPDATE SET {update_str}"""

        try:
            cur.execute(sql, values)
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  ERROR on {ticker}: {e}")
            conn.rollback()
            continue

        # History
        h_cols = ['ticker', 'date', 'price', 'market_cap', 'volume',
                  'pe_ratio', 'ps_ratio', 'pb_ratio', 'profit_margin_pct',
                  'revenue_growth_ttm_pct', 'rsi', 'beta',
                  'insider_own_pct', 'inst_own_pct', 'debt_to_equity',
                  'target_price', 'recommendation',
                  'perf_week_pct', 'perf_month_pct', 'perf_quarter_pct', 'perf_year_pct']
        h_vals = [
            ticker, today,
            safe_numeric(stock.get('price')), safe_numeric(stock.get('market_cap')),
            safe_numeric(stock.get('volume')),
            safe_numeric(stock.get('p_e')), safe_numeric(stock.get('p_s')),
            safe_numeric(stock.get('p_b')), safe_numeric(stock.get('profit_margin')),
            safe_numeric(stock.get('revenue_growth_ttm')), safe_numeric(stock.get('rsi')),
            safe_numeric(stock.get('beta')),
            safe_numeric(stock.get('insider_own')), safe_numeric(stock.get('inst_own')),
            safe_numeric(stock.get('debt_to_equity')),
            safe_numeric(stock.get('target_price')), safe_numeric(stock.get('recommendation')),
            safe_numeric(stock.get('perf_week')), safe_numeric(stock.get('perf_month')),
            safe_numeric(stock.get('perf_quarter')), safe_numeric(stock.get('perf_year'))
        ]
        h_placeholders = ','.join(['%s'] * len(h_vals))
        try:
            cur.execute(
                f"""INSERT INTO stock_history ({','.join(h_cols)}) VALUES ({h_placeholders})
                    ON CONFLICT (ticker, date) DO NOTHING""",
                h_vals
            )
        except Exception as e:
            if errors <= 3:
                print(f"  HISTORY ERROR on {ticker}: {e}")
            conn.rollback()

        updated += 1
        if updated % 100 == 0:
            conn.commit()
            print(f"  Updated {updated} stocks...")

    conn.commit()
    conn.close()
    if errors:
        print(f"  {errors} stocks had errors (skipped)")
    return updated


def update_sqlite(stocks):
    """Insert/update scraped stocks into SQLite."""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today = date.today().isoformat()
    updated = 0

    TEXT_COLS = {'ex_dividend_date', 'ipo_date', 'earnings_date', 'option_short',
                 'company_name', 'sector', 'industry', 'market_index'}

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
            if db_column in TEXT_COLS:
                cleaned = safe_text(value)
            else:
                cleaned = safe_numeric(value)
            if cleaned is not None:
                columns.append(db_column)
                values.append(cleaned)

        placeholders = ','.join(['?'] * len(values))
        columns_str = ','.join(columns)
        cursor.execute(f"INSERT OR REPLACE INTO stocks ({columns_str}) VALUES ({placeholders})", values)

        h_vals = [
            ticker, today,
            safe_numeric(stock.get('price')), safe_numeric(stock.get('market_cap')),
            safe_numeric(stock.get('volume')),
            safe_numeric(stock.get('p_e')), safe_numeric(stock.get('p_s')),
            safe_numeric(stock.get('p_b')), safe_numeric(stock.get('profit_margin')),
            safe_numeric(stock.get('revenue_growth_ttm')), safe_numeric(stock.get('rsi')),
            safe_numeric(stock.get('beta')),
            safe_numeric(stock.get('insider_own')), safe_numeric(stock.get('inst_own')),
            safe_numeric(stock.get('debt_to_equity')),
            safe_numeric(stock.get('target_price')), safe_numeric(stock.get('recommendation')),
            safe_numeric(stock.get('perf_week')), safe_numeric(stock.get('perf_month')),
            safe_numeric(stock.get('perf_quarter')), safe_numeric(stock.get('perf_year'))
        ]
        cursor.execute(
            """INSERT OR IGNORE INTO stock_history
               (ticker, date, price, market_cap, volume, pe_ratio, ps_ratio, pb_ratio,
                profit_margin, revenue_growth_ttm, rsi, beta, insider_own, inst_own,
                debt_to_equity, target_price, recommendation,
                perf_week, perf_month, perf_quarter, perf_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            h_vals
        )

        updated += 1
        if updated % 100 == 0:
            conn.commit()
            print(f"  Updated {updated} stocks...")

    conn.commit()
    conn.close()
    return updated


def main():
    # Parse simple CLI args for test modes
    test_count = None
    db_only = False
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--test' and i < len(sys.argv) - 1:
            test_count = int(sys.argv[i + 1])
        if arg == '--db-only':
            db_only = True
        if arg.isdigit() and i > 1 and sys.argv[i - 1] == '--test':
            pass  # already handled

    db_type = "Postgres" if USE_POSTGRES else f"SQLite ({DB_PATH})"
    print(f"{'='*60}")
    print(f"  Stock Data Refresh -- {datetime.now().isoformat()}")
    print(f"  Database: {db_type}")
    if test_count:
        print(f"  TEST MODE: {test_count} tickers only")
    if db_only:
        print(f"  DB-ONLY MODE: using {OUTPUT_JSON}")
    print(f"{'='*60}")

    if db_only:
        # Skip scraping, just load from last JSON backup and write to DB
        if not os.path.exists(OUTPUT_JSON):
            print(f"ERROR: {OUTPUT_JSON} not found! Run a scrape first.")
            sys.exit(1)
        with open(OUTPUT_JSON, 'r') as f:
            backup = json.load(f)
        results = backup.get('stocks', [])
        print(f"Loaded {len(results)} stocks from {OUTPUT_JSON}")
    else:
        tickers = get_tickers()
        if not tickers:
            print("ERROR: No tickers found!")
            sys.exit(1)

        if test_count:
            tickers = tickers[:test_count]

        print(f"Found {len(tickers)} tickers to scrape")
        est_time = len(tickers) * BATCH_DELAY / 60
        print(f"Estimated time: {est_time:.0f} minutes (delay={BATCH_DELAY}s)")

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
    if USE_POSTGRES:
        updated = update_postgres(results)
    else:
        updated = update_sqlite(results)
    print(f"Updated {updated} stocks")

    print(f"\n{'='*60}")
    print(f"  COMPLETE -- {datetime.now().isoformat()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()