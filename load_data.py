import sqlite3
import json
import csv
from datetime import datetime, date

def clean_value(value):
    if value is None or value == '' or value == 'N/A':
        return None
    if isinstance(value, str):
        value = value.replace(',', '').strip()
        try:
            return float(value) if '.' in value else int(value)
        except ValueError:
            return value
    return value

def clean_company_name(name):
    if not name:
        return None
    name = name.replace('\r', '').replace('\n', '')
    name = ' '.join(name.split())
    return name.strip()

def load_csv_mapping(csv_file):
    industry_map = {}
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = (row.get('Exchange:Ticker') or row.get('Ticker') or row.get('ticker', '')).strip()
                industry = row.get('Industry Group', '').strip()
                sector = row.get('Sector', '').strip()
                if ticker:
                    industry_map[ticker] = {'industry': industry, 'sector': sector}
    except Exception as e:
        print(f"Warning: Could not load CSV: {e}")
    return industry_map

def parse_finviz_key(key):
    """Map JSON keys (from scraper output) to DB column names.

    The updated scraper already splits multi-value fields into separate
    keys (e.g. '52w_high' + '52w_high_pct'), so we just need to map
    those new keys to their DB columns.
    """
    mapping = {
        'index': 'market_index', 'change': 'price_change', 'p_e': 'pe_ratio', 'forward_p_e': 'forward_pe',
        'peg': 'peg_ratio', 'p_s': 'ps_ratio', 'p_b': 'pb_ratio', 'p_c': 'pc_ratio', 'p_fcf': 'pfcf_ratio',
        'ev_sales': 'ev_sales', 'ev_ebitda': 'ev_ebitda', 'price': 'price', 'volume': 'volume',
        'market_cap': 'market_cap', 'enterprise_value': 'enterprise_value', 'avg_volume': 'avg_volume',
        'rel_volume': 'rel_volume', 'prev_close': 'prev_close', 'eps_(ttm)': 'eps_ttm', 'eps_ttm': 'eps_ttm',
        'eps_next_y': 'eps_growth_next_y', 'eps_next_q': 'eps_next_q', 'eps_this_y': 'eps_this_y',
        'eps_next_5y': 'eps_growth_next_5y', 'eps_past_5y': 'eps_past_5y',
        'eps_y_y_ttm': 'eps_growth_ttm', 'eps_qoq': 'eps_qoq',
        'sales_y_y_ttm': 'revenue_growth_ttm', 'revenue_growth_ttm': 'revenue_growth_ttm',
        'sales_qyq': 'sales_qyq',
        'income': 'income', 'sales': 'sales',
        'profit_margin': 'profit_margin',
        'oper._margin': 'operating_margin', 'operating_margin': 'operating_margin', 'gross_margin': 'gross_margin',
        'roa': 'roa', 'roe': 'roe', 'roi': 'roi', 'roic': 'roic', 'rsi_(14)': 'rsi', 'rsi': 'rsi',
        'beta': 'beta', 'atr_(14)': 'atr', 'atr': 'atr',
        'sma20': 'sma20', 'sma50': 'sma50', 'sma200': 'sma200',
        'insider_own': 'insider_own', 'insider_trans': 'insider_trans',
        'inst_own': 'inst_own', 'inst_trans': 'inst_trans',
        'shs_outstand': 'shares_outstanding', 'shs_float': 'shares_float',
        'short_float': 'short_float', 'short_ratio': 'short_ratio', 'short_interest': 'short_interest',
        'debt_eq': 'debt_to_equity', 'debt_to_eq': 'debt_to_equity',
        'lt_debt_eq': 'lt_debt_to_equity', 'current_ratio': 'current_ratio', 'quick_ratio': 'quick_ratio',
        'cash_sh': 'cash_per_share', 'book_sh': 'book_per_share', 'book_value': 'book_per_share',
        'dividend_ex-date': 'ex_dividend_date', 'ex-dividend_date': 'ex_dividend_date',
        'payout': 'payout_ratio',
        'target_price': 'target_price', 'recom': 'recommendation', 'recommendation': 'recommendation',
        'perf_week': 'perf_week', 'perf_month': 'perf_month',
        'perf_quarter': 'perf_quarter', 'perf_half_y': 'perf_half_y', 'perf_year': 'perf_year',
        'perf_ytd': 'perf_ytd', 'perf_3y': 'perf_3y', 'perf_5y': 'perf_5y', 'perf_10y': 'perf_10y',
        'employees': 'employees', 'ipo': 'ipo_date', 'earnings': 'earnings_date', 'option_short': 'option_short',

        # ── NEW: split multi-value fields ──
        # 52-week high/low (was single TEXT column)
        '52w_high':     'week_52_high',
        '52w_high_pct': 'week_52_high_pct',
        '52w_low':      'week_52_low',
        '52w_low_pct':  'week_52_low_pct',
        # Volatility (was single TEXT column)
        'volatility_week':  'volatility_week',
        'volatility_month': 'volatility_month',
        # EPS past 3Y/5Y (was single TEXT column eps_past_3_5y)
        'eps_past_3y':  'eps_past_3y',
        # eps_past_5y already mapped above from 'eps_past_5y'
        # Sales past 3Y/5Y (was single REAL sales_growth_past_5y)
        'sales_past_3y': 'sales_past_3y',
        'sales_past_5y': 'sales_past_5y',
        # Dividend (was single TEXT / merged)
        'dividend_ttm':       'dividend_ttm',
        'dividend_yield':     'dividend_yield',
        'dividend_est':       'dividend_est',
        'dividend_yield_est': 'dividend_yield_est',
        # Dividend growth 3Y/5Y (was single TEXT)
        'dividend_gr_3y': 'dividend_gr_3y',
        'dividend_gr_5y': 'dividend_gr_5y',
        # EPS / Sales surprise (was not stored at all)
        'eps_surprise':   'eps_surprise',
        'sales_surprise': 'sales_surprise',
    }
    return mapping.get(key, None)

def load_data(json_file='stock_data.json', csv_file='StockSource.csv', db_path='stocks.db'):
    industry_map = load_csv_mapping(csv_file)
    print(f"Loaded {len(industry_map)} industry mappings from CSV")

    with open(json_file, 'r') as f:
        data = json.load(f)

    stocks = data.get('stocks', [])
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print(f"Loading {len(stocks)} stocks...")
    inserted = 0
    today = date.today().isoformat()

    for stock in stocks:
        ticker = stock.get('ticker')
        if not ticker:
            continue

        company_name = clean_company_name(stock.get('company_name'))

        csv_data = industry_map.get(ticker, {})
        industry = csv_data.get('industry') or stock.get('industry')
        sector = csv_data.get('sector') or stock.get('sector')

        columns = ['ticker', 'company_name', 'industry', 'sector', 'last_updated']
        values = [ticker, company_name, industry, sector, datetime.now()]

        for key, value in stock.items():
            if key in ['ticker', 'company_name', 'industry', 'sector', 'scraped_at']:
                continue
            db_column = parse_finviz_key(key)
            if db_column is None:
                continue
            cleaned_value = clean_value(value)
            if cleaned_value is not None:
                columns.append(db_column)
                values.append(cleaned_value)

        placeholders = ','.join(['?' for _ in values])
        columns_str = ','.join(columns)
        cursor.execute(f"INSERT OR REPLACE INTO stocks ({columns_str}) VALUES ({placeholders})", values)

        history_data = [ticker, today, stock.get('price'), stock.get('market_cap'), stock.get('volume'),
                       stock.get('p_e'), stock.get('p_s'), stock.get('p_b'), stock.get('profit_margin'),
                       stock.get('sales_y_y_ttm') or stock.get('revenue_growth_ttm'),
                       stock.get('rsi_(14)') or stock.get('rsi'),
                       stock.get('beta'),
                       stock.get('insider_own'), stock.get('inst_own'), stock.get('debt_eq') or stock.get('debt_to_equity'),
                       stock.get('target_price'), stock.get('recom') or stock.get('recommendation'),
                       stock.get('perf_week'),
                       stock.get('perf_month'), stock.get('perf_quarter'), stock.get('perf_year')]

        cursor.execute("""INSERT OR IGNORE INTO stock_history (ticker, date, price, market_cap, volume,
                         pe_ratio, ps_ratio, pb_ratio, profit_margin, revenue_growth_ttm, rsi, beta,
                         insider_own, inst_own, debt_to_equity, target_price, recommendation,
                         perf_week, perf_month, perf_quarter, perf_year) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       [clean_value(v) for v in history_data])

        inserted += 1
        if inserted % 500 == 0:
            print(f"{inserted}...")
            conn.commit()

    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM stocks")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM stocks WHERE industry IS NOT NULL")
    with_industry = cursor.fetchone()[0]
    conn.close()

    print(f"Complete: {total} stocks in database")
    print(f"{with_industry} stocks have industry data")

if __name__ == "__main__":
    import sys
    load_data(sys.argv[1] if len(sys.argv) > 1 else 'stock_data.json',
             sys.argv[2] if len(sys.argv) > 2 else 'StockSource.csv',
             sys.argv[3] if len(sys.argv) > 3 else 'stocks.db')