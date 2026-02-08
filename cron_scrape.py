#!/usr/bin/env python3
"""
cron_scrape.py — Triggered by Railway cron to refresh stock data.

Uses Postgres if DATABASE_URL is set, otherwise SQLite.

Railway cron setup:
  - Schedule: 0 6 * * 1   (weekly Monday 6 AM UTC)
  - Command:  python cron_scrape.py
"""
import os
import sys
import json
import time
from datetime import datetime, date

from scraper import FinvizScraper, split_multi_value_fields
from load_data import clean_value, clean_company_name, parse_finviz_key

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get("DB_PATH", "stocks.db")
USE_POSTGRES = DATABASE_URL is not None
BATCH_DELAY = float(os.environ.get("SCRAPE_DELAY", "2.5"))
OUTPUT_JSON = "stock_data_latest.json"

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
            cleaned = clean_value(value)
            if cleaned is not None:
                columns.append(db_column)
                values.append(cleaned)

        placeholders = ','.join(['%s'] * len(values))
        columns_str = ','.join(columns)
        update_parts = [f"{col} = EXCLUDED.{col}" for col in columns if col != 'ticker']
        update_str = ','.join(update_parts)

        sql = f"""INSERT INTO stocks ({columns_str}) VALUES ({placeholders})
                  ON CONFLICT (ticker) DO UPDATE SET {update_str}"""
        cur.execute(sql, values)

        # History
        h_cols = ['ticker', 'date', 'price', 'market_cap', 'volume',
                  'pe_ratio', 'ps_ratio', 'pb_ratio', 'profit_margin_pct',
                  'revenue_growth_ttm_pct', 'rsi', 'beta',
                  'insider_own_pct', 'inst_own_pct', 'debt_to_equity',
                  'target_price', 'recommendation',
                  'perf_week_pct', 'perf_month_pct', 'perf_quarter_pct', 'perf_year_pct']
        h_vals = [
            ticker, today, stock.get('price'), stock.get('market_cap'), stock.get('volume'),
            stock.get('p_e'), stock.get('p_s'), stock.get('p_b'), stock.get('profit_margin'),
            stock.get('revenue_growth_ttm'), stock.get('rsi'), stock.get('beta'),
            stock.get('insider_own'), stock.get('inst_own'), stock.get('debt_to_equity'),
            stock.get('target_price'), stock.get('recommendation'),
            stock.get('perf_week'), stock.get('perf_month'),
            stock.get('perf_quarter'), stock.get('perf_year')
        ]
        h_placeholders = ','.join(['%s'] * len(h_vals))
        cur.execute(
            f"""INSERT INTO stock_history ({','.join(h_cols)}) VALUES ({h_placeholders})
                ON CONFLICT (ticker, date) DO NOTHING""",
            [clean_value(v) for v in h_vals]
        )

        updated += 1
        if updated % 100 == 0:
            conn.commit()
            print(f"  Updated {updated} stocks...")

    conn.commit()
    conn.close()
    return updated


def update_sqlite(stocks):
    """Insert/update scraped stocks into SQLite."""
    import sqlite3
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

        placeholders = ','.join(['?'] * len(values))
        columns_str = ','.join(columns)
        cursor.execute(f"INSERT OR REPLACE INTO stocks ({columns_str}) VALUES ({placeholders})", values)

        h_vals = [
            ticker, today, stock.get('price'), stock.get('market_cap'), stock.get('volume'),
            stock.get('p_e'), stock.get('p_s'), stock.get('p_b'), stock.get('profit_margin'),
            stock.get('revenue_growth_ttm'), stock.get('rsi'), stock.get('beta'),
            stock.get('insider_own'), stock.get('inst_own'), stock.get('debt_to_equity'),
            stock.get('target_price'), stock.get('recommendation'),
            stock.get('perf_week'), stock.get('perf_month'),
            stock.get('perf_quarter'), stock.get('perf_year')
        ]
        cursor.execute(
            """INSERT OR IGNORE INTO stock_history
               (ticker, date, price, market_cap, volume, pe_ratio, ps_ratio, pb_ratio,
                profit_margin, revenue_growth_ttm, rsi, beta, insider_own, inst_own,
                debt_to_equity, target_price, recommendation,
                perf_week, perf_month, perf_quarter, perf_year)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [clean_value(v) for v in h_vals]
        )

        updated += 1
        if updated % 100 == 0:
            conn.commit()
            print(f"  Updated {updated} stocks...")

    conn.commit()
    conn.close()
    return updated


def main():
    db_type = "Postgres" if USE_POSTGRES else f"SQLite ({DB_PATH})"
    print(f"{'='*60}")
    print(f"  Stock Data Refresh — {datetime.now().isoformat()}")
    print(f"  Database: {db_type}")
    print(f"{'='*60}")

    tickers = get_tickers()
    if not tickers:
        print("ERROR: No tickers found!")
        sys.exit(1)

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
    print(f"  COMPLETE — {datetime.now().isoformat()}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()