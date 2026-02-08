import sqlite3
import psycopg2
from psycopg2.extras import execute_batch
import sys
import re

def clean_numeric(value, return_string=False):
    if value is None or value == '' or value == '-' or value == '- -':
        return None
    if isinstance(value, (int, float)):
        rounded = round(float(value), 2)
        return str(rounded) if return_string else rounded
    value = str(value).strip().replace('%', '')
    match = re.match(r'^(-?\d+\.?\d*)', value)
    if match:
        try:
            cleaned = round(float(match.group(1)), 2)
            return str(cleaned) if return_string else cleaned
        except ValueError:
            return None
    return None

def clean_text(value):
    if value is None or value == '' or value == '-' or value == '- -':
        return None
    return str(value).strip()

def migrate(sqlite_db='stocks.db', postgres_url=None):
    if not postgres_url:
        print("Error: PostgreSQL URL required")
        print("Usage: python migrate.py <postgres_url>")
        return

    print("Connecting to SQLite...")
    sqlite_conn = sqlite3.connect(sqlite_db)
    sqlite_cursor = sqlite_conn.cursor()

    print("Connecting to PostgreSQL...")
    if 'sslmode' not in postgres_url:
        postgres_url += '?sslmode=require'
    pg_conn = psycopg2.connect(postgres_url)
    pg_cursor = pg_conn.cursor()

    print("Creating schema...")
    with open('postgres_schema.sql', 'r') as f:
        pg_cursor.execute(f.read())
        pg_conn.commit()

    # SQLite column name -> Postgres column name
    column_mapping = {
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

    # All numeric columns in SQLite
    numeric_cols = {
        'price', 'price_change', 'prev_close', 'volume', 'market_cap', 'enterprise_value',
        'pe_ratio', 'forward_pe', 'peg_ratio', 'ps_ratio', 'pb_ratio', 'pc_ratio', 'pfcf_ratio',
        'ev_sales', 'ev_ebitda', 'profit_margin', 'operating_margin', 'gross_margin',
        'roe', 'roa', 'roi', 'roic', 'eps_ttm', 'eps_growth_ttm', 'revenue_growth_ttm',
        'eps_growth_next_y', 'eps_growth_next_5y', 'eps_next_q', 'eps_this_y',
        'eps_past_3y', 'eps_past_5y',
        'sales_past_3y', 'sales_past_5y',
        'sales_qyq', 'eps_qoq', 'rsi', 'beta', 'atr',
        'volatility_week', 'volatility_month',
        'rel_volume', 'avg_volume',
        'week_52_high', 'week_52_high_pct', 'week_52_low', 'week_52_low_pct',
        'sma20', 'sma50', 'sma200',
        'insider_own', 'insider_trans', 'inst_own', 'inst_trans',
        'shares_outstanding', 'shares_float', 'short_float', 'short_ratio', 'short_interest',
        'debt_to_equity', 'lt_debt_to_equity', 'current_ratio', 'quick_ratio',
        'cash_per_share', 'book_per_share',
        'dividend_yield', 'dividend_ttm', 'dividend_est', 'dividend_yield_est',
        'payout_ratio',
        'dividend_gr_3y', 'dividend_gr_5y',
        'eps_surprise', 'sales_surprise',
        'target_price', 'recommendation',
        'perf_week', 'perf_month', 'perf_quarter', 'perf_half_y',
        'perf_year', 'perf_ytd', 'perf_3y', 'perf_5y', 'perf_10y',
        'employees', 'income', 'sales',
    }

    # Migrate stocks
    print("\nMigrating stocks...")
    sqlite_cursor.execute("SELECT * FROM stocks")
    stocks = sqlite_cursor.fetchall()
    old_cols = [desc[0] for desc in sqlite_cursor.description]
    new_cols = [column_mapping.get(col, col) for col in old_cols]

    cleaned_stocks = []
    for row in stocks:
        cleaned_row = []
        for i, value in enumerate(row):
            col = old_cols[i]
            if col in numeric_cols:
                cleaned_row.append(clean_numeric(value))
            else:
                cleaned_row.append(clean_text(value))
        cleaned_stocks.append(tuple(cleaned_row))

    insert_sql = f"INSERT INTO stocks ({','.join(new_cols)}) VALUES ({','.join(['%s']*len(new_cols))}) ON CONFLICT (ticker) DO NOTHING"

    batch_size = 50
    for i in range(0, len(cleaned_stocks), batch_size):
        batch = cleaned_stocks[i:i+batch_size]
        execute_batch(pg_cursor, insert_sql, batch, page_size=50)
        pg_conn.commit()
        print(f"  Progress: {min(i+batch_size, len(cleaned_stocks))}/{len(cleaned_stocks)}")

    print(f"Migrated {len(cleaned_stocks)} stocks")

    # Migrate history
    print("\nMigrating history...")
    sqlite_cursor.execute("SELECT * FROM stock_history")
    history = sqlite_cursor.fetchall()

    if history:
        old_hist_cols = [desc[0] for desc in sqlite_cursor.description]
        hist_mapping = {
            'profit_margin': 'profit_margin_pct',
            'revenue_growth_ttm': 'revenue_growth_ttm_pct',
            'insider_own': 'insider_own_pct',
            'inst_own': 'inst_own_pct',
            'perf_week': 'perf_week_pct',
            'perf_month': 'perf_month_pct',
            'perf_quarter': 'perf_quarter_pct',
            'perf_year': 'perf_year_pct',
        }
        new_hist_cols = [hist_mapping.get(col, col) for col in old_hist_cols]

        cleaned_history = []
        for row in history:
            cleaned_row = []
            for i, value in enumerate(row):
                col = old_hist_cols[i]
                if col in ['id', 'ticker', 'date', 'scraped_at']:
                    cleaned_row.append(value)
                else:
                    cleaned_row.append(clean_numeric(value))
            cleaned_history.append(tuple(cleaned_row))

        insert_sql = f"INSERT INTO stock_history ({','.join(new_hist_cols)}) VALUES ({','.join(['%s']*len(new_hist_cols))}) ON CONFLICT (ticker, date) DO NOTHING"

        for i in range(0, len(cleaned_history), batch_size):
            batch = cleaned_history[i:i+batch_size]
            execute_batch(pg_cursor, insert_sql, batch, page_size=50)
            pg_conn.commit()
            print(f"  Progress: {min(i+batch_size, len(cleaned_history))}/{len(cleaned_history)}")

        print(f"Migrated {len(cleaned_history)} history records")

    sqlite_conn.close()
    pg_conn.close()
    print("\nMigration complete!")

if __name__ == "__main__":
    postgres_url = sys.argv[1] if len(sys.argv) > 1 else None
    migrate('stocks.db', postgres_url)