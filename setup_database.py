import sqlite3

def create_database(db_path='stocks.db'):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stocks (
        ticker TEXT PRIMARY KEY,
        company_name TEXT,
        sector TEXT,
        industry TEXT,
        market_index TEXT,
        price REAL,
        price_change REAL,
        prev_close REAL,
        volume INTEGER,
        market_cap INTEGER,
        enterprise_value INTEGER,
        pe_ratio REAL,
        forward_pe REAL,
        peg_ratio REAL,
        ps_ratio REAL,
        pb_ratio REAL,
        pc_ratio REAL,
        pfcf_ratio REAL,
        ev_sales REAL,
        ev_ebitda REAL,
        profit_margin REAL,
        operating_margin REAL,
        gross_margin REAL,
        roe REAL,
        roa REAL,
        roic REAL,
        roi REAL,
        eps_ttm REAL,
        eps_growth_ttm REAL,
        revenue_growth_ttm REAL,
        eps_growth_next_y REAL,
        eps_growth_next_5y REAL,
        eps_next_q REAL,
        eps_this_y REAL,
        -- Split: was eps_past_3_5y (single TEXT)
        eps_past_3y REAL,
        eps_past_5y REAL,
        -- Split: was sales_growth_past_5y (single REAL, poorly named)
        sales_past_3y REAL,
        sales_past_5y REAL,
        sales_qyq REAL,
        eps_qoq REAL,
        rsi REAL,
        beta REAL,
        atr REAL,
        -- Split: was volatility (single TEXT)
        volatility_week REAL,
        volatility_month REAL,
        rel_volume REAL,
        avg_volume INTEGER,
        -- Split: was week_52_high (single TEXT)
        week_52_high REAL,
        week_52_high_pct REAL,
        -- Split: was week_52_low (single TEXT)
        week_52_low REAL,
        week_52_low_pct REAL,
        sma20 REAL,
        sma50 REAL,
        sma200 REAL,
        insider_own REAL,
        insider_trans REAL,
        inst_own REAL,
        inst_trans REAL,
        shares_outstanding INTEGER,
        shares_float INTEGER,
        short_float REAL,
        short_ratio REAL,
        short_interest INTEGER,
        debt_to_equity REAL,
        lt_debt_to_equity REAL,
        current_ratio REAL,
        quick_ratio REAL,
        cash_per_share REAL,
        book_per_share REAL,
        -- Split: was dividend_yield (single REAL, came from dividend_ttm parsing)
        dividend_yield REAL,
        dividend_ttm REAL,
        -- Split: was dividend_est (single TEXT)
        dividend_est REAL,
        dividend_yield_est REAL,
        payout_ratio REAL,
        ex_dividend_date TEXT,
        -- Split: was dividend_growth_3_5y (single TEXT)
        dividend_gr_3y REAL,
        dividend_gr_5y REAL,
        -- Split: was eps_sales_surpr (not stored before)
        eps_surprise REAL,
        sales_surprise REAL,
        target_price REAL,
        recommendation REAL,
        perf_week REAL,
        perf_month REAL,
        perf_quarter REAL,
        perf_half_y REAL,
        perf_year REAL,
        perf_ytd REAL,
        perf_3y REAL,
        perf_5y REAL,
        perf_10y REAL,
        employees INTEGER,
        ipo_date TEXT,
        earnings_date TEXT,
        income REAL,
        sales REAL,
        option_short TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stock_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT NOT NULL,
        date DATE NOT NULL,
        price REAL,
        market_cap INTEGER,
        volume INTEGER,
        pe_ratio REAL,
        ps_ratio REAL,
        pb_ratio REAL,
        profit_margin REAL,
        revenue_growth_ttm REAL,
        rsi REAL,
        beta REAL,
        insider_own REAL,
        inst_own REAL,
        debt_to_equity REAL,
        target_price REAL,
        recommendation REAL,
        perf_week REAL,
        perf_month REAL,
        perf_quarter REAL,
        perf_year REAL,
        scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(ticker, date)
    )
    """)

    for idx in [
        "CREATE INDEX IF NOT EXISTS idx_sector ON stocks(sector)",
        "CREATE INDEX IF NOT EXISTS idx_industry ON stocks(industry)",
        "CREATE INDEX IF NOT EXISTS idx_market_cap ON stocks(market_cap)",
        "CREATE INDEX IF NOT EXISTS idx_pe_ratio ON stocks(pe_ratio)",
        "CREATE INDEX IF NOT EXISTS idx_rsi ON stocks(rsi)",
        "CREATE INDEX IF NOT EXISTS idx_price ON stocks(price)",
        "CREATE INDEX IF NOT EXISTS idx_history_ticker ON stock_history(ticker)",
        "CREATE INDEX IF NOT EXISTS idx_history_date ON stock_history(date)"
    ]:
        cursor.execute(idx)

    conn.commit()
    conn.close()
    print(f"Database ready: {db_path}")

if __name__ == "__main__":
    import sys
    create_database(sys.argv[1] if len(sys.argv) > 1 else 'stocks.db')