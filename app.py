"""
Stock Analysis Dashboard -- Flask Backend
Supports Postgres (Railway) and SQLite (local dev).
"""
import os
import json
import sqlite3
import logging
from datetime import datetime
from flask import Flask, render_template, jsonify, request, g
from flask_cors import CORS

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False
    logging.warning("yfinance not installed. Live price data unavailable. pip install yfinance")

app = Flask(__name__)
CORS(app)

# ── Database config ────────────────────────────────────────
# If DATABASE_URL is set, use Postgres. Otherwise fall back to SQLite.
DATABASE_URL = os.environ.get("DATABASE_URL")
DB_PATH = os.environ.get("DB_PATH", "stocks.db")

USE_POSTGRES = DATABASE_URL is not None

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    print(f"[DB] Using Postgres")
else:
    print(f"[DB] Using SQLite: {DB_PATH}")

# ── Postgres ↔ API column name mapping ────────────────────
# Postgres uses _pct suffixes, but the frontend expects the original names.
# PG_TO_API: Postgres column name → API column name sent to frontend
# API_TO_PG: reverse

PG_TO_API = {
    'price_change_pct': 'price_change',
    'profit_margin_pct': 'profit_margin',
    'operating_margin_pct': 'operating_margin',
    'gross_margin_pct': 'gross_margin',
    'roe_pct': 'roe',
    'roa_pct': 'roa',
    'roi_pct': 'roi',
    'roic_pct': 'roic',
    'eps_growth_ttm_pct': 'eps_growth_ttm',
    'revenue_growth_ttm_pct': 'revenue_growth_ttm',
    'eps_growth_next_y_pct': 'eps_growth_next_y',
    'eps_growth_next_5y_pct': 'eps_growth_next_5y',
    'eps_past_3y_pct': 'eps_past_3y',
    'eps_past_5y_pct': 'eps_past_5y',
    'sales_past_3y_pct': 'sales_past_3y',
    'sales_past_5y_pct': 'sales_past_5y',
    'sales_qyq_pct': 'sales_qyq',
    'eps_qoq_pct': 'eps_qoq',
    'volatility_week_pct': 'volatility_week',
    'volatility_month_pct': 'volatility_month',
    'insider_own_pct': 'insider_own',
    'insider_trans_pct': 'insider_trans',
    'inst_own_pct': 'inst_own',
    'inst_trans_pct': 'inst_trans',
    'short_float_pct': 'short_float',
    'dividend_yield_pct': 'dividend_yield',
    'dividend_yield_est_pct': 'dividend_yield_est',
    'payout_ratio_pct': 'payout_ratio',
    'dividend_gr_3y_pct': 'dividend_gr_3y',
    'dividend_gr_5y_pct': 'dividend_gr_5y',
    'eps_surprise_pct': 'eps_surprise',
    'sales_surprise_pct': 'sales_surprise',
    'perf_week_pct': 'perf_week',
    'perf_month_pct': 'perf_month',
    'perf_quarter_pct': 'perf_quarter',
    'perf_half_y_pct': 'perf_half_y',
    'perf_year_pct': 'perf_year',
    'perf_ytd_pct': 'perf_ytd',
    'perf_3y_pct': 'perf_3y',
    'perf_5y_pct': 'perf_5y',
    'perf_10y_pct': 'perf_10y',
}

API_TO_PG = {v: k for k, v in PG_TO_API.items()}


def pg_row_to_api(pg_columns, row):
    """Convert a Postgres row (with _pct columns) to API dict (without _pct)."""
    d = {}
    for i, col in enumerate(pg_columns):
        api_name = PG_TO_API.get(col, col)
        val = row[i]
        # Convert Decimal to float for JSON
        if hasattr(val, 'as_tuple'):
            val = float(val)
        d[api_name] = val
    return d


def api_col_to_pg(api_col):
    """Convert an API column name to Postgres column name."""
    return API_TO_PG.get(api_col, api_col)


# ── Database helpers ───────────────────────────────────────

def get_db():
    if 'db' not in g:
        if USE_POSTGRES:
            g.db = psycopg2.connect(DATABASE_URL)
        else:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db:
        db.close()


def db_execute(query, params=None):
    """Execute a query, returning (columns, rows). Handles both PG and SQLite."""
    db = get_db()
    if USE_POSTGRES:
        cur = db.cursor()
        cur.execute(query, params or ())
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchall()
        return columns, rows
    else:
        cur = db.execute(query, params or ())
        columns = [desc[0] for desc in cur.description] if cur.description else []
        rows = cur.fetchall()
        return columns, rows


def db_execute_write(query, params=None):
    """Execute a write query (INSERT/UPDATE/DELETE)."""
    db = get_db()
    if USE_POSTGRES:
        cur = db.cursor()
        cur.execute(query, params or ())
        db.commit()
    else:
        db.execute(query, params or ())
        db.commit()


def db_fetchone(query, params=None):
    """Fetch a single row."""
    db = get_db()
    if USE_POSTGRES:
        cur = db.cursor()
        cur.execute(query, params or ())
        columns = [desc[0] for desc in cur.description] if cur.description else []
        row = cur.fetchone()
        return columns, row
    else:
        cur = db.execute(query, params or ())
        columns = [desc[0] for desc in cur.description] if cur.description else []
        row = cur.fetchone()
        return columns, row


def db_param(index=None):
    """Return the parameter placeholder for the current DB."""
    return '%s' if USE_POSTGRES else '?'


P = '%s' if USE_POSTGRES else '?'


# ── Shortlist ─────────────────────────────────────────────
# Postgres: uses a shortlist table
# SQLite: uses a JSON file

SHORTLIST_PATH = os.environ.get("SHORTLIST_PATH", "shortlist.json")


def _ensure_shortlist_table():
    """Create shortlist table in Postgres if it doesn't exist."""
    if USE_POSTGRES:
        db = get_db()
        cur = db.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS shortlist (
                ticker VARCHAR(10) PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                added_price NUMERIC
            )
        """)
        # Migration: add added_price if missing
        try:
            cur.execute("""
                ALTER TABLE shortlist ADD COLUMN IF NOT EXISTS added_price NUMERIC
            """)
        except Exception:
            pass
        db.commit()


def _load_shortlist():
    """Return list of ticker strings (backward-compatible)."""
    if USE_POSTGRES:
        try:
            _ensure_shortlist_table()
            cols, rows = db_execute("SELECT ticker FROM shortlist ORDER BY ticker")
            return [r[0] for r in rows]
        except Exception:
            get_db().rollback()
            return []
    else:
        if os.path.exists(SHORTLIST_PATH):
            with open(SHORTLIST_PATH, 'r') as f:
                data = json.load(f)
            # Handle both old format (list of strings) and new format (list of dicts)
            if data and isinstance(data[0], dict):
                return [d['ticker'] for d in data]
            return data
        return []


def _load_shortlist_detailed():
    """Return list of dicts with ticker, added_price, added_at."""
    if USE_POSTGRES:
        try:
            _ensure_shortlist_table()
            cols, rows = db_execute(
                "SELECT ticker, added_price, added_at FROM shortlist ORDER BY ticker"
            )
            result = []
            for r in rows:
                val = r[1]
                if hasattr(val, 'as_tuple'):
                    val = float(val)
                result.append({
                    'ticker': r[0],
                    'added_price': val,
                    'added_at': r[2].isoformat() if r[2] else None,
                })
            return result
        except Exception:
            get_db().rollback()
            return []
    else:
        if os.path.exists(SHORTLIST_PATH):
            with open(SHORTLIST_PATH, 'r') as f:
                data = json.load(f)
            # Handle old format (list of strings)
            if data and isinstance(data[0], str):
                return [{'ticker': t, 'added_price': None, 'added_at': None} for t in data]
            return data
        return []


def _save_shortlist_add(ticker, price=None):
    if USE_POSTGRES:
        _ensure_shortlist_table()
        db_execute_write(
            f"INSERT INTO shortlist (ticker, added_price) VALUES ({P}, {P}) ON CONFLICT (ticker) DO NOTHING",
            (ticker, price)
        )
    else:
        sl = _load_shortlist_detailed()
        tickers = [d['ticker'] for d in sl]
        if ticker not in tickers:
            sl.append({
                'ticker': ticker,
                'added_price': price,
                'added_at': datetime.utcnow().isoformat(),
            })
        _save_shortlist_file_detailed(sl)


def _save_shortlist_remove(ticker):
    if USE_POSTGRES:
        _ensure_shortlist_table()
        db_execute_write(f"DELETE FROM shortlist WHERE ticker = {P}", (ticker,))
    else:
        sl = _load_shortlist_detailed()
        sl = [d for d in sl if d['ticker'] != ticker]
        _save_shortlist_file_detailed(sl)


def _save_shortlist_file(tickers):
    """SQLite fallback: save list of ticker strings (legacy compat)."""
    detailed = []
    for t in sorted(set(tickers)):
        detailed.append({'ticker': t, 'added_price': None, 'added_at': None})
    _save_shortlist_file_detailed(detailed)


def _save_shortlist_file_detailed(entries):
    """SQLite fallback: save list of dicts to JSON file."""
    with open(SHORTLIST_PATH, 'w') as f:
        json.dump(entries, f)


# ── Column metadata ───────────────────────────────────────
# These use API names (no _pct suffix) — the frontend never sees Postgres names.

COLUMN_GROUPS = {
    "identity": {
        "label": "Identity",
        "columns": ["ticker", "company_name", "sector", "industry", "market_index"],
    },
    "price": {
        "label": "Price & Volume",
        "columns": [
            "price", "price_change", "prev_close", "volume", "avg_volume",
            "rel_volume", "market_cap", "enterprise_value",
        ],
    },
    "valuation": {
        "label": "Valuation",
        "columns": [
            "pe_ratio", "forward_pe", "peg_ratio", "ps_ratio", "pb_ratio",
            "pc_ratio", "pfcf_ratio", "ev_sales", "ev_ebitda",
        ],
    },
    "profitability": {
        "label": "Profitability",
        "columns": [
            "eps_ttm", "income", "sales", "profit_margin", "operating_margin",
            "gross_margin", "roa", "roe", "roi", "roic",
        ],
    },
    "growth": {
        "label": "Growth",
        "columns": [
            "revenue_growth_ttm", "eps_growth_ttm", "eps_growth_next_y",
            "eps_growth_next_5y", "eps_this_y", "eps_next_q",
            "eps_past_3y", "eps_past_5y", "sales_past_3y", "sales_past_5y",
            "sales_qyq", "eps_qoq", "eps_surprise", "sales_surprise",
        ],
    },
    "financial_health": {
        "label": "Financial Health",
        "columns": [
            "debt_to_equity", "lt_debt_to_equity", "current_ratio",
            "quick_ratio", "cash_per_share", "book_per_share",
        ],
    },
    "ownership": {
        "label": "Ownership",
        "columns": [
            "insider_own", "insider_trans", "inst_own", "inst_trans",
            "shares_outstanding", "shares_float",
        ],
    },
    "short_interest": {
        "label": "Short Interest",
        "columns": ["short_float", "short_ratio", "short_interest"],
    },
    "technical": {
        "label": "Technical",
        "columns": [
            "rsi", "beta", "atr", "volatility_week", "volatility_month",
            "sma20", "sma50", "sma200",
            "week_52_high", "week_52_high_pct", "week_52_low", "week_52_low_pct",
        ],
    },
    "dividends": {
        "label": "Dividends",
        "columns": [
            "dividend_ttm", "dividend_yield", "dividend_est", "dividend_yield_est",
            "payout_ratio", "ex_dividend_date", "dividend_gr_3y", "dividend_gr_5y",
        ],
    },
    "performance": {
        "label": "Performance",
        "columns": [
            "perf_week", "perf_month", "perf_quarter", "perf_half_y",
            "perf_year", "perf_ytd", "perf_3y", "perf_5y", "perf_10y",
        ],
    },
    "analyst": {
        "label": "Analyst",
        "columns": ["target_price", "recommendation"],
    },
    "other": {
        "label": "Other",
        "columns": ["employees", "ipo_date", "earnings_date", "option_short"],
    },
}

COLUMN_META = {
    "ticker": {"label": "Ticker", "fmt": "text"},
    "company_name": {"label": "Company", "fmt": "text"},
    "sector": {"label": "Sector", "fmt": "text"},
    "industry": {"label": "Industry", "fmt": "text"},
    "market_index": {"label": "Index", "fmt": "text"},
    "price": {"label": "Price", "fmt": "dollar"},
    "price_change": {"label": "Change %", "fmt": "pct"},
    "prev_close": {"label": "Prev Close", "fmt": "dollar"},
    "volume": {"label": "Volume", "fmt": "bignum"},
    "avg_volume": {"label": "Avg Volume", "fmt": "bignum"},
    "rel_volume": {"label": "Rel Volume", "fmt": "num2"},
    "market_cap": {"label": "Market Cap", "fmt": "bignum"},
    "enterprise_value": {"label": "Enterprise Value", "fmt": "bignum"},
    "pe_ratio": {"label": "P/E", "fmt": "num2"},
    "forward_pe": {"label": "Fwd P/E", "fmt": "num2"},
    "peg_ratio": {"label": "PEG", "fmt": "num2"},
    "ps_ratio": {"label": "P/S", "fmt": "num2"},
    "pb_ratio": {"label": "P/B", "fmt": "num2"},
    "pc_ratio": {"label": "P/C", "fmt": "num2"},
    "pfcf_ratio": {"label": "P/FCF", "fmt": "num2"},
    "ev_sales": {"label": "EV/Sales", "fmt": "num2"},
    "ev_ebitda": {"label": "EV/EBITDA", "fmt": "num2"},
    "eps_ttm": {"label": "EPS (TTM)", "fmt": "dollar"},
    "income": {"label": "Income", "fmt": "bignum"},
    "sales": {"label": "Revenue", "fmt": "bignum"},
    "profit_margin": {"label": "Profit Margin", "fmt": "pct"},
    "operating_margin": {"label": "Oper. Margin", "fmt": "pct"},
    "gross_margin": {"label": "Gross Margin", "fmt": "pct"},
    "roa": {"label": "ROA", "fmt": "pct"},
    "roe": {"label": "ROE", "fmt": "pct"},
    "roi": {"label": "ROI", "fmt": "pct"},
    "roic": {"label": "ROIC", "fmt": "pct"},
    "revenue_growth_ttm": {"label": "Rev Growth TTM", "fmt": "pct"},
    "eps_growth_ttm": {"label": "EPS Growth TTM", "fmt": "pct"},
    "eps_growth_next_y": {"label": "EPS Growth Next Y", "fmt": "pct"},
    "eps_growth_next_5y": {"label": "EPS Growth Next 5Y", "fmt": "pct"},
    "eps_this_y": {"label": "EPS This Y", "fmt": "pct"},
    "eps_next_q": {"label": "EPS Next Q", "fmt": "dollar"},
    "eps_past_3y": {"label": "EPS Past 3Y", "fmt": "pct"},
    "eps_past_5y": {"label": "EPS Past 5Y", "fmt": "pct"},
    "sales_past_3y": {"label": "Sales Past 3Y", "fmt": "pct"},
    "sales_past_5y": {"label": "Sales Past 5Y", "fmt": "pct"},
    "sales_qyq": {"label": "Sales Q/Q", "fmt": "pct"},
    "eps_qoq": {"label": "EPS Q/Q", "fmt": "pct"},
    "eps_surprise": {"label": "EPS Surprise", "fmt": "pct"},
    "sales_surprise": {"label": "Sales Surprise", "fmt": "pct"},
    "debt_to_equity": {"label": "Debt/Eq", "fmt": "num2"},
    "lt_debt_to_equity": {"label": "LT Debt/Eq", "fmt": "num2"},
    "current_ratio": {"label": "Current Ratio", "fmt": "num2"},
    "quick_ratio": {"label": "Quick Ratio", "fmt": "num2"},
    "cash_per_share": {"label": "Cash/sh", "fmt": "dollar"},
    "book_per_share": {"label": "Book/sh", "fmt": "dollar"},
    "insider_own": {"label": "Insider Own", "fmt": "pct"},
    "insider_trans": {"label": "Insider Trans", "fmt": "pct"},
    "inst_own": {"label": "Inst Own", "fmt": "pct"},
    "inst_trans": {"label": "Inst Trans", "fmt": "pct"},
    "shares_outstanding": {"label": "Shs Outstd", "fmt": "bignum"},
    "shares_float": {"label": "Shs Float", "fmt": "bignum"},
    "short_float": {"label": "Short Float", "fmt": "pct"},
    "short_ratio": {"label": "Short Ratio", "fmt": "num2"},
    "short_interest": {"label": "Short Interest", "fmt": "bignum"},
    "rsi": {"label": "RSI (14)", "fmt": "num1"},
    "beta": {"label": "Beta", "fmt": "num2"},
    "atr": {"label": "ATR (14)", "fmt": "num2"},
    "volatility_week": {"label": "Vol Week", "fmt": "pct"},
    "volatility_month": {"label": "Vol Month", "fmt": "pct"},
    "sma20": {"label": "SMA20 %", "fmt": "pct"},
    "sma50": {"label": "SMA50 %", "fmt": "pct"},
    "sma200": {"label": "SMA200 %", "fmt": "pct"},
    "week_52_high": {"label": "52W High", "fmt": "dollar"},
    "week_52_high_pct": {"label": "52W High %", "fmt": "pct"},
    "week_52_low": {"label": "52W Low", "fmt": "dollar"},
    "week_52_low_pct": {"label": "52W Low %", "fmt": "pct"},
    "dividend_ttm": {"label": "Div TTM", "fmt": "dollar"},
    "dividend_yield": {"label": "Div Yield", "fmt": "pct"},
    "dividend_est": {"label": "Div Est", "fmt": "dollar"},
    "dividend_yield_est": {"label": "Div Yield Est", "fmt": "pct"},
    "payout_ratio": {"label": "Payout", "fmt": "pct"},
    "ex_dividend_date": {"label": "Ex-Div Date", "fmt": "text"},
    "dividend_gr_3y": {"label": "Div Gr 3Y", "fmt": "pct"},
    "dividend_gr_5y": {"label": "Div Gr 5Y", "fmt": "pct"},
    "target_price": {"label": "Target Price", "fmt": "dollar"},
    "recommendation": {"label": "Analyst Rec", "fmt": "num1"},
    "perf_week": {"label": "Perf Week", "fmt": "pct"},
    "perf_month": {"label": "Perf Month", "fmt": "pct"},
    "perf_quarter": {"label": "Perf Quarter", "fmt": "pct"},
    "perf_half_y": {"label": "Perf Half Y", "fmt": "pct"},
    "perf_year": {"label": "Perf Year", "fmt": "pct"},
    "perf_ytd": {"label": "Perf YTD", "fmt": "pct"},
    "perf_3y": {"label": "Perf 3Y", "fmt": "pct"},
    "perf_5y": {"label": "Perf 5Y", "fmt": "pct"},
    "perf_10y": {"label": "Perf 10Y", "fmt": "pct"},
    "employees": {"label": "Employees", "fmt": "bignum"},
    "ipo_date": {"label": "IPO Date", "fmt": "text"},
    "earnings_date": {"label": "Earnings Date", "fmt": "text"},
    "option_short": {"label": "Option/Short", "fmt": "text"},
}


# ── Routes ─────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/meta")
def api_meta():
    cols, rows = db_execute(
        "SELECT DISTINCT industry FROM stocks WHERE industry IS NOT NULL ORDER BY industry"
    )
    industries = [r[0] for r in rows]

    cols, rows = db_execute(
        "SELECT DISTINCT sector FROM stocks WHERE sector IS NOT NULL AND sector != '' ORDER BY sector"
    )
    sectors = [r[0] for r in rows]

    cols, rows = db_execute("SELECT COUNT(*) FROM stocks")
    total = rows[0][0]

    return jsonify({
        "column_groups": COLUMN_GROUPS,
        "column_meta": COLUMN_META,
        "industries": industries,
        "sectors": sectors,
        "total_stocks": total,
    })


@app.route("/api/stocks")
def api_stocks():
    where_clauses = []
    params = []

    industry = request.args.get("industry")
    if industry:
        where_clauses.append(f"industry = {P}")
        params.append(industry)

    sector = request.args.get("sector")
    if sector:
        where_clauses.append(f"sector = {P}")
        params.append(sector)

    min_cap = request.args.get("min_cap")
    if min_cap:
        where_clauses.append(f"market_cap >= {P}")
        params.append(int(min_cap))
    max_cap = request.args.get("max_cap")
    if max_cap:
        where_clauses.append(f"market_cap <= {P}")
        params.append(int(max_cap))

    min_rsi = request.args.get("min_rsi")
    if min_rsi:
        where_clauses.append(f"rsi >= {P}")
        params.append(float(min_rsi))
    max_rsi = request.args.get("max_rsi")
    if max_rsi:
        where_clauses.append(f"rsi <= {P}")
        params.append(float(max_rsi))

    min_pe = request.args.get("min_pe")
    if min_pe:
        where_clauses.append(f"pe_ratio >= {P}")
        params.append(float(min_pe))
    max_pe = request.args.get("max_pe")
    if max_pe:
        where_clauses.append(f"pe_ratio <= {P}")
        params.append(float(max_pe))

    shortlist_only = request.args.get("shortlist_only")
    if shortlist_only == "true":
        sl = _load_shortlist()
        if sl:
            placeholders = ",".join([P] * len(sl))
            where_clauses.append(f"ticker IN ({placeholders})")
            params.extend(sl)
        else:
            return jsonify([])

    where = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    query = f"SELECT * FROM stocks{where} ORDER BY market_cap DESC NULLS LAST"

    columns, rows = db_execute(query, params)
    shortlist_set = set(_load_shortlist())

    result = []
    for row in rows:
        if USE_POSTGRES:
            d = pg_row_to_api(columns, row)
        else:
            d = {columns[i]: row[i] for i in range(len(columns))}
        d["_shortlisted"] = d.get("ticker") in shortlist_set
        result.append(d)

    return jsonify(result)


@app.route("/api/stock/<ticker>")
def api_stock_detail(ticker):
    columns, row = db_fetchone(f"SELECT * FROM stocks WHERE ticker = {P}", (ticker,))
    if not row:
        return jsonify({"error": "Not found"}), 404

    if USE_POSTGRES:
        data = pg_row_to_api(columns, row)
    else:
        data = {columns[i]: row[i] for i in range(len(columns))}

    # History
    h_cols, h_rows = db_execute(
        f"SELECT * FROM stock_history WHERE ticker = {P} ORDER BY date", (ticker,)
    )
    if USE_POSTGRES:
        data["history"] = [pg_row_to_api(h_cols, h) for h in h_rows]
    else:
        data["history"] = [{h_cols[i]: h[i] for i in range(len(h_cols))} for h in h_rows]

    # Industry peers
    ind = data.get("industry")
    if ind:
        # Use Postgres column names for the query
        if USE_POSTGRES:
            peer_q = f"""SELECT ticker, company_name, price, market_cap, pe_ratio, ps_ratio,
                         profit_margin_pct, roe_pct, rsi, debt_to_equity, revenue_growth_ttm_pct
                         FROM stocks WHERE industry = {P} AND ticker != {P}
                         ORDER BY market_cap DESC NULLS LAST LIMIT 10"""
        else:
            peer_q = f"""SELECT ticker, company_name, price, market_cap, pe_ratio, ps_ratio,
                         profit_margin, roe, rsi, debt_to_equity, revenue_growth_ttm
                         FROM stocks WHERE industry = {P} AND ticker != {P}
                         ORDER BY market_cap DESC NULLS LAST LIMIT 10"""

        p_cols, p_rows = db_execute(peer_q, (ind, ticker))
        if USE_POSTGRES:
            data["peers"] = [pg_row_to_api(p_cols, p) for p in p_rows]
        else:
            data["peers"] = [{p_cols[i]: p[i] for i in range(len(p_cols))} for p in p_rows]
    else:
        data["peers"] = []

    # Industry averages
    if ind:
        if USE_POSTGRES:
            avg_q = f"""SELECT
                AVG(pe_ratio) as avg_pe, AVG(ps_ratio) as avg_ps, AVG(pb_ratio) as avg_pb,
                AVG(profit_margin_pct) as avg_profit_margin,
                AVG(operating_margin_pct) as avg_oper_margin,
                AVG(gross_margin_pct) as avg_gross_margin,
                AVG(roe_pct) as avg_roe, AVG(roa_pct) as avg_roa, AVG(roic_pct) as avg_roic,
                AVG(debt_to_equity) as avg_de, AVG(current_ratio) as avg_cr,
                AVG(revenue_growth_ttm_pct) as avg_rev_growth, AVG(rsi) as avg_rsi,
                AVG(beta) as avg_beta, AVG(peg_ratio) as avg_peg, AVG(pfcf_ratio) as avg_pfcf,
                COUNT(*) as peer_count
               FROM stocks WHERE industry = {P} AND pe_ratio IS NOT NULL"""
        else:
            avg_q = f"""SELECT
                AVG(pe_ratio) as avg_pe, AVG(ps_ratio) as avg_ps, AVG(pb_ratio) as avg_pb,
                AVG(profit_margin) as avg_profit_margin,
                AVG(operating_margin) as avg_oper_margin,
                AVG(gross_margin) as avg_gross_margin,
                AVG(roe) as avg_roe, AVG(roa) as avg_roa, AVG(roic) as avg_roic,
                AVG(debt_to_equity) as avg_de, AVG(current_ratio) as avg_cr,
                AVG(revenue_growth_ttm) as avg_rev_growth, AVG(rsi) as avg_rsi,
                AVG(beta) as avg_beta, AVG(peg_ratio) as avg_peg, AVG(pfcf_ratio) as avg_pfcf,
                COUNT(*) as peer_count
               FROM stocks WHERE industry = {P} AND pe_ratio IS NOT NULL"""

        a_cols, a_rows = db_execute(avg_q, (ind,))
        if a_rows and a_rows[0]:
            avg_dict = {}
            for i, col in enumerate(a_cols):
                val = a_rows[0][i]
                if hasattr(val, 'as_tuple'):
                    val = float(val)
                avg_dict[col] = val
            data["industry_averages"] = avg_dict
        else:
            data["industry_averages"] = {}
    else:
        data["industry_averages"] = {}

    shortlist_set = set(_load_shortlist())
    data["_shortlisted"] = ticker in shortlist_set

    # Inject live price from yfinance (fall back to finviz-scraped price)
    live = _get_live_quote(ticker)
    if live:
        data["live_price"] = live["price"]
        data["live_change"] = live["change"]
        data["live_change_pct"] = live["change_pct"]
        data["live_prev_close"] = live["prev_close"]
        data["price_source"] = "live"
    else:
        data["live_price"] = data.get("price")
        data["live_change"] = None
        data["live_change_pct"] = None
        data["live_prev_close"] = data.get("prev_close")
        data["price_source"] = "finviz"

    return jsonify(data)


@app.route("/api/shortlist", methods=["GET"])
def api_shortlist_get():
    detailed = request.args.get("detailed", "false").lower() == "true"
    if detailed:
        return jsonify(_load_shortlist_detailed())
    return jsonify(_load_shortlist())


@app.route("/api/shortlist", methods=["POST"])
def api_shortlist_update():
    body = request.get_json()
    ticker = body.get("ticker")
    action = body.get("action", "toggle")
    price = body.get("price")  # price at time of adding

    sl = _load_shortlist()
    currently_in = ticker in sl

    if action == "add" or (action == "toggle" and not currently_in):
        _save_shortlist_add(ticker, price)
        shortlisted = True
    elif action == "remove" or (action == "toggle" and currently_in):
        _save_shortlist_remove(ticker)
        shortlisted = False
    else:
        shortlisted = currently_in

    return jsonify({
        "shortlist": _load_shortlist(),
        "shortlist_detailed": _load_shortlist_detailed(),
        "ticker": ticker,
        "shortlisted": shortlisted,
    })


@app.route("/api/shortlist/bulk", methods=["POST"])
def api_shortlist_bulk():
    body = request.get_json()
    tickers = body.get("tickers", [])
    action = body.get("action", "add")

    if action == "add":
        for t in tickers:
            _save_shortlist_add(t)
    elif action == "remove":
        for t in tickers:
            _save_shortlist_remove(t)
    elif action == "set":
        # Clear all, then add
        if USE_POSTGRES:
            _ensure_shortlist_table()
            db_execute_write("DELETE FROM shortlist")
            for t in tickers:
                _save_shortlist_add(t)
        else:
            _save_shortlist_file(tickers)

    return jsonify({"shortlist": _load_shortlist(), "shortlist_detailed": _load_shortlist_detailed()})


@app.route("/api/industry_stats")
def api_industry_stats():
    if USE_POSTGRES:
        q = """SELECT industry, COUNT(*) as count,
            AVG(pe_ratio) as avg_pe, AVG(ps_ratio) as avg_ps, AVG(pb_ratio) as avg_pb,
            AVG(profit_margin_pct) as avg_profit_margin, AVG(roe_pct) as avg_roe,
            AVG(roa_pct) as avg_roa, AVG(debt_to_equity) as avg_de,
            AVG(revenue_growth_ttm_pct) as avg_rev_growth, AVG(rsi) as avg_rsi,
            AVG(market_cap) as avg_market_cap, SUM(market_cap) as total_market_cap
            FROM stocks WHERE industry IS NOT NULL
            GROUP BY industry ORDER BY total_market_cap DESC"""
    else:
        q = """SELECT industry, COUNT(*) as count,
            AVG(pe_ratio) as avg_pe, AVG(ps_ratio) as avg_ps, AVG(pb_ratio) as avg_pb,
            AVG(profit_margin) as avg_profit_margin, AVG(roe) as avg_roe,
            AVG(roa) as avg_roa, AVG(debt_to_equity) as avg_de,
            AVG(revenue_growth_ttm) as avg_rev_growth, AVG(rsi) as avg_rsi,
            AVG(market_cap) as avg_market_cap, SUM(market_cap) as total_market_cap
            FROM stocks WHERE industry IS NOT NULL
            GROUP BY industry ORDER BY total_market_cap DESC"""

    cols, rows = db_execute(q)
    result = []
    for row in rows:
        d = {}
        for i, col in enumerate(cols):
            val = row[i]
            if hasattr(val, 'as_tuple'):
                val = float(val)
            d[col] = val
        result.append(d)
    return jsonify(result)


# ── Live Price & History (yfinance) ────────────────────────

# Period config: maps frontend period keys to yfinance params
# Each entry: (yf_period, yf_interval, label)
PRICE_PERIODS = {
    "1D":  {"period": "1d",  "interval": "5m"},
    "1W":  {"period": "5d",  "interval": "15m"},
    "1M":  {"period": "1mo", "interval": "1h"},
    "3M":  {"period": "3mo", "interval": "1d"},
    "1Y":  {"period": "1y",  "interval": "1d"},
    "5Y":  {"period": "5y",  "interval": "1wk"},
}


def _get_live_quote(ticker_symbol):
    """Fetch live price data from yfinance. Returns dict or None."""
    if not YF_AVAILABLE:
        return None
    try:
        tk = yf.Ticker(ticker_symbol)
        info = tk.fast_info
        current_price = getattr(info, 'last_price', None)
        prev_close = getattr(info, 'previous_close', None)
        if current_price is None:
            return None
        change = round(current_price - prev_close, 2) if prev_close else None
        change_pct = round((change / prev_close) * 100, 2) if prev_close and change is not None else None
        return {
            "price": round(current_price, 2),
            "prev_close": round(prev_close, 2) if prev_close else None,
            "change": change,
            "change_pct": change_pct,
            "source": "live",
        }
    except Exception as e:
        logging.warning(f"yfinance quote failed for {ticker_symbol}: {e}")
        return None


@app.route("/api/stock/<ticker>/price_history")
def api_price_history(ticker):
    """
    Return historical price data for a ticker.
    Query params:
      - period: 1D, 1W, 1M, 3M, 1Y, 5Y (default 1M)
    Response mimics Robinhood-style data: array of price points with
    timestamp, open, high, low, close, volume, plus summary stats.
    """
    if not YF_AVAILABLE:
        return jsonify({"error": "yfinance not installed on server"}), 503

    period_key = request.args.get("period", "1M").upper()
    if period_key not in PRICE_PERIODS:
        return jsonify({"error": f"Invalid period. Use: {', '.join(PRICE_PERIODS.keys())}"}), 400

    cfg = PRICE_PERIODS[period_key]

    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period=cfg["period"], interval=cfg["interval"])

        if hist.empty:
            return jsonify({"error": f"No price data found for {ticker}"}), 404

        prices = []
        for idx, row in hist.iterrows():
            ts = idx
            # Convert timezone-aware timestamps to ISO strings
            if hasattr(ts, 'isoformat'):
                ts_str = ts.isoformat()
            else:
                ts_str = str(ts)

            prices.append({
                "timestamp": ts_str,
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]) if row["Volume"] else 0,
            })

        if not prices:
            return jsonify({"error": "No data points"}), 404

        # Summary stats
        first_close = prices[0]["close"]
        last_close = prices[-1]["close"]
        change = round(last_close - first_close, 2)
        change_pct = round((change / first_close) * 100, 2) if first_close else 0
        high = max(p["high"] for p in prices)
        low = min(p["low"] for p in prices)

        return jsonify({
            "ticker": ticker,
            "period": period_key,
            "current_price": last_close,
            "change": change,
            "change_pct": change_pct,
            "high": high,
            "low": low,
            "data_points": len(prices),
            "prices": prices,
        })

    except Exception as e:
        logging.error(f"Price history error for {ticker}/{period_key}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/stock/<ticker>/quote")
def api_live_quote(ticker):
    """Return a live quote for a single ticker, falling back to DB price."""
    live = _get_live_quote(ticker)
    if live:
        return jsonify(live)

    # Fallback: pull from database (finviz scrape)
    columns, row = db_fetchone(f"SELECT price, prev_close FROM stocks WHERE ticker = {P}", (ticker,))
    if row:
        db_price = row[0]
        db_prev = row[1]
        change = round(db_price - db_prev, 2) if db_price and db_prev else None
        change_pct = round((change / db_prev) * 100, 2) if db_prev and change else None
        return jsonify({
            "price": db_price,
            "prev_close": db_prev,
            "change": change,
            "change_pct": change_pct,
            "source": "finviz",
        })
    return jsonify({"error": "Not found"}), 404


@app.route("/api/metrics_guide")
def api_metrics_guide():
    return jsonify(METRICS_GUIDE)


# ── Metrics reference data ─────────────────────────────────

METRICS_GUIDE = {
    "price": {
        "name": "Price", "direction": "neutral", "good_range": "Context-dependent",
        "desc": "Current trading price per share.",
        "calc": "Determined by supply and demand on the exchange.",
        "detail": "Price alone is meaningless without context. A $5 stock is not inherently cheaper than a $500 stock. Valuation ratios (P/E, P/S, etc.) are what matter for determining if a stock is cheap or expensive.",
    },
    "market_cap": {
        "name": "Market Cap", "direction": "neutral", "good_range": "Mega >200B, Large 10-200B, Mid 2-10B, Small 300M-2B",
        "desc": "Total market value of a company's outstanding shares.",
        "calc": "Market Cap = Current Share Price x Total Shares Outstanding.",
        "detail": "Used for classification. Compare metrics to companies in the same market cap tier. Small caps often have higher growth but more risk than large caps. Mega Cap: >$200B, Large Cap: $10B-$200B, Mid Cap: $2B-$10B, Small Cap: $300M-$2B, Micro Cap: <$300M.",
    },
    "enterprise_value": {
        "name": "Enterprise Value", "direction": "neutral", "good_range": "Compare to Market Cap",
        "desc": "Comprehensive measure of total company value, accounting for debt and cash. The theoretical takeover price.",
        "calc": "EV = Market Cap + Total Debt - Cash & Cash Equivalents.",
        "detail": "Used as a denominator in EV/EBITDA, EV/Sales. A company with much lower EV than market cap has a strong net cash position. Much higher EV than market cap indicates heavy debt.",
    },
    "pe_ratio": {
        "name": "P/E Ratio", "direction": "lower", "good_range": "10-25",
        "desc": "Price relative to earnings. The most widely used valuation metric.",
        "calc": "P/E = Current Share Price / Earnings Per Share (TTM).",
        "detail": "Lower is generally cheaper, but must compare to industry peers. S&P 500 historical average: ~15-20. Growth stocks: 25-60+ is common. Value stocks: 5-15 is common. Negative P/E means the company is losing money. Very high P/E (>50) means the market expects strong future growth OR the stock is overvalued. Tech companies routinely trade at higher P/Es than utilities or banks.",
    },
    "forward_pe": {
        "name": "Forward P/E", "direction": "lower", "good_range": "Below trailing P/E",
        "desc": "P/E using estimated future earnings instead of trailing.",
        "calc": "Forward P/E = Current Share Price / Estimated EPS (next 12 months).",
        "detail": "Lower than trailing P/E means earnings are expected to grow (positive signal). Higher than trailing P/E means earnings expected to decline (negative signal). Compare to industry forward P/E averages.",
    },
    "peg_ratio": {
        "name": "PEG Ratio", "direction": "lower", "good_range": "<1 undervalued, 1 fair",
        "desc": "P/E adjusted for expected earnings growth rate.",
        "calc": "PEG = P/E Ratio / Expected Annual EPS Growth Rate (usually next 5 years).",
        "detail": "<1 = potentially undervalued relative to growth (Peter Lynch's rule of thumb). 1 = fairly valued. >1 = potentially overvalued. >2 = expensive even accounting for growth. Caution: relies on growth estimates which can be wrong. Does not work for companies with negative earnings or negative growth.",
    },
    "ps_ratio": {
        "name": "P/S Ratio", "direction": "lower", "good_range": "<3 for most industries",
        "desc": "Price relative to revenue. Useful for unprofitable companies.",
        "calc": "P/S = Market Cap / Total Revenue (TTM), or Price / Revenue Per Share.",
        "detail": "Lower is generally better. <2 is cheap for most industries. High-growth SaaS companies can trade at 10-30x sales. Established companies: <1 is very cheap, 1-3 is typical. Varies enormously by sector.",
    },
    "pb_ratio": {
        "name": "P/B Ratio", "direction": "lower", "good_range": "1-3",
        "desc": "Price relative to book value (net assets).",
        "calc": "P/B = Current Share Price / Book Value Per Share.",
        "detail": "<1 = stock is trading below liquidation value (could be a bargain or value trap). 1-3 = typical range. >3 = premium to book, common for asset-light companies (tech, pharma). Very useful for banks and financial companies. Less useful for tech companies whose value is in intangible assets. Negative book value is a red flag.",
    },
    "pc_ratio": {
        "name": "P/C Ratio", "direction": "lower", "good_range": "Lower = more cash cushion",
        "desc": "Price relative to cash per share on the balance sheet.",
        "calc": "P/C = Share Price / Cash Per Share.",
        "detail": "Lower means the company has more cash relative to its price. P/C < 1 would mean more cash per share than the stock price (rare but very attractive). Useful for assessing downside protection and financial flexibility.",
    },
    "pfcf_ratio": {
        "name": "P/FCF", "direction": "lower", "good_range": "<20",
        "desc": "Price relative to free cash flow. Harder to manipulate than P/E.",
        "calc": "P/FCF = Market Cap / Free Cash Flow. FCF = Operating Cash Flow - Capital Expenditures.",
        "detail": "<15 = generally attractive. 15-25 = fairly valued. >25 = expensive unless high growth justifies it. Negative FCF means the company is burning cash (not necessarily bad for early-stage growth, but a red flag for mature companies). Many investors consider FCF more reliable than earnings.",
    },
    "ev_sales": {
        "name": "EV/Sales", "direction": "lower", "good_range": "<3",
        "desc": "Enterprise value relative to revenue. Accounts for debt/cash unlike P/S.",
        "calc": "EV/Sales = Enterprise Value / Total Revenue (TTM).",
        "detail": "Similar to P/S but uses enterprise value which accounts for debt and cash. <1 = very cheap (debt-adjusted value is less than annual revenue). Compare to industry averages and P/S ratio to see the impact of debt.",
    },
    "ev_ebitda": {
        "name": "EV/EBITDA", "direction": "lower", "good_range": "<12",
        "desc": "Enterprise value to EBITDA. Capital-structure neutral valuation widely used in M&A.",
        "calc": "EV/EBITDA = Enterprise Value / EBITDA (TTM).",
        "detail": "<10 = generally considered cheap. 10-15 = fair value for many industries. >15 = expensive. Industry benchmarks matter enormously. Tech: 15-25 is common. Utilities: 8-12. Oil & Gas: 4-8. Widely used in mergers and acquisitions.",
    },
    "eps_ttm": {
        "name": "EPS (TTM)", "direction": "higher", "good_range": "Positive and growing",
        "desc": "Earnings per share over the trailing twelve months.",
        "calc": "EPS = (Net Income - Preferred Dividends) / Weighted Average Shares Outstanding.",
        "detail": "Positive and growing is good. Negative means the company is losing money. Compare to prior periods and analyst estimates. Beating estimates is a positive catalyst. The absolute number matters less than the trend and comparison to peers.",
    },
    "income": {
        "name": "Net Income", "direction": "higher", "good_range": "Positive and growing",
        "desc": "Total profit after all expenses, taxes, interest, and other costs.",
        "calc": "Revenue - COGS - Operating Expenses - Interest - Taxes - Other Expenses.",
        "detail": "Positive and growing is good. Compare growth rate to revenue growth. If income grows faster than revenue, margins are expanding. Negative income is acceptable for high-growth companies reinvesting heavily, but should have a path to profitability.",
    },
    "sales": {
        "name": "Revenue", "direction": "higher", "good_range": "Positive and growing",
        "desc": "Total revenue (sales) generated by the company.",
        "calc": "Sum of all revenue from goods sold and services rendered.",
        "detail": "The top line. Revenue growth is fundamental. Compare to prior periods and peers. Revenue growing faster than industry average is a positive sign.",
    },
    "profit_margin": {
        "name": "Profit Margin", "direction": "higher", "good_range": ">10%",
        "desc": "Net income as a percentage of revenue. The bottom-line profitability measure.",
        "calc": "Profit Margin = Net Income / Revenue x 100%.",
        "detail": ">20% = excellent. 10-20% = solid. 5-10% = average. <5% = thin but may be normal for some industries (retail/grocery). Negative means the company is losing money. Higher is better, and expanding margins over time is a very positive sign.",
    },
    "operating_margin": {
        "name": "Operating Margin", "direction": "higher", "good_range": ">15%",
        "desc": "Operating income as a percentage of revenue, before interest and taxes.",
        "calc": "Operating Margin = Operating Income / Revenue x 100%.",
        "detail": ">20% = strong for most industries. 10-20% = healthy. <10% = thin margins (could be industry norm for retail, restaurants). Negative means the company is not operationally profitable. Expanding operating margins over time is a very positive sign.",
    },
    "gross_margin": {
        "name": "Gross Margin", "direction": "higher", "good_range": ">40%",
        "desc": "Revenue minus cost of goods sold, as a percentage of revenue.",
        "calc": "Gross Margin = (Revenue - Cost of Goods Sold) / Revenue x 100%.",
        "detail": "Software/SaaS: 70-90%+ is excellent. Manufacturing: 25-45% is typical. Retail: 20-40% is typical. Grocery: 25-30%. Higher means more of each dollar is available for operating expenses and profit. Declining gross margin is a red flag.",
    },
    "roe": {
        "name": "ROE", "direction": "higher", "good_range": ">15%",
        "desc": "Return on equity. Measures how efficiently the company uses shareholder equity to generate profit.",
        "calc": "ROE = Net Income / Shareholders' Equity x 100%.",
        "detail": ">15% = generally strong. 10-15% = decent. <10% = below average. Very high ROE (>30%) can indicate either excellent management OR excessive leverage (check Debt/Equity). Negative ROE means net losses or negative equity. Use DuPont analysis to decompose: ROE = Profit Margin x Asset Turnover x Equity Multiplier.",
    },
    "roa": {
        "name": "ROA", "direction": "higher", "good_range": ">5%",
        "desc": "Return on assets. How efficiently assets generate profit.",
        "calc": "ROA = Net Income / Total Assets x 100%.",
        "detail": ">10% = excellent. 5-10% = good. <5% = may be an asset-heavy industry (real estate, utilities). Negative means losing money. Compare to industry -- asset-light companies naturally have higher ROA.",
    },
    "roi": {
        "name": "ROI", "direction": "higher", "good_range": ">10%",
        "desc": "Return on investment. General measure of returns generated.",
        "calc": "ROI = Net Profit / Total Investment x 100%.",
        "detail": "Measures the overall return on invested resources. Higher is better. Compare to cost of capital and industry peers.",
    },
    "roic": {
        "name": "ROIC", "direction": "higher", "good_range": ">15%",
        "desc": "Return on invested capital. Considered by many to be the most important profitability metric.",
        "calc": "ROIC = NOPAT / Invested Capital. Invested Capital = Total Equity + Total Debt - Cash.",
        "detail": ">15% = excellent value creator. 10-15% = solid. ROIC > WACC (Weighted Average Cost of Capital) means the company is creating value. ROIC < WACC means destroying value. Consistently high ROIC over many years is a hallmark of great businesses (Buffett/Munger philosophy).",
    },
    "debt_to_equity": {
        "name": "Debt/Equity", "direction": "lower", "good_range": "<1.0",
        "desc": "Total debt relative to equity. Measures financial leverage.",
        "calc": "Debt/Equity = Total Debt / Total Shareholders' Equity.",
        "detail": "<0.5 = conservative, low leverage. 0.5-1.0 = moderate. 1.0-2.0 = high leverage (may be appropriate for capital-intensive industries). >2.0 = very high leverage, higher risk. Negative means negative equity (red flag). Industry context is crucial: utilities and REITs normally have higher D/E; tech companies tend to have lower.",
    },
    "lt_debt_to_equity": {
        "name": "LT Debt/Equity", "direction": "lower", "good_range": "<1.0",
        "desc": "Long-term debt relative to equity. Excludes short-term obligations.",
        "calc": "LT Debt/Equity = Long-Term Debt / Total Shareholders' Equity.",
        "detail": "Same guidelines as total Debt/Equity. If there's a big gap between total D/E and LT D/E, the company has significant short-term debt, which could be a liquidity risk.",
    },
    "current_ratio": {
        "name": "Current Ratio", "direction": "higher", "good_range": ">1.5",
        "desc": "Ability to pay short-term obligations with short-term assets.",
        "calc": "Current Ratio = Current Assets / Current Liabilities.",
        "detail": ">2.0 = very comfortable. 1.5-2.0 = healthy. 1.0-1.5 = adequate but watch closely. <1.0 = may struggle to meet short-term obligations (red flag unless normal for the industry). Very high (>4) could mean the company is not efficiently deploying assets.",
    },
    "quick_ratio": {
        "name": "Quick Ratio", "direction": "higher", "good_range": ">1.0",
        "desc": "Conservative liquidity measure. Excludes inventory.",
        "calc": "Quick Ratio = (Cash + Short-Term Investments + Accounts Receivable) / Current Liabilities.",
        "detail": ">1.0 = can cover short-term liabilities without selling inventory (the standard benchmark). 0.5-1.0 = somewhat reliant on inventory. <0.5 = potential liquidity issues. Particularly important for companies with slow-moving inventory.",
    },
    "cash_per_share": {
        "name": "Cash/Share", "direction": "higher", "good_range": "Higher = more flexibility",
        "desc": "Cash and equivalents per share on the balance sheet.",
        "calc": "Cash/sh = (Cash + Equivalents + Short-Term Investments) / Shares Outstanding.",
        "detail": "Higher means more financial flexibility. Compare to stock price -- if cash/sh is a significant percentage of the stock price, there's a cash cushion. Some investors subtract cash/sh from price to get an ex-cash price for valuation.",
    },
    "book_per_share": {
        "name": "Book/Share", "direction": "higher", "good_range": "Positive and growing",
        "desc": "Net asset value per share. What shareholders would theoretically receive in liquidation.",
        "calc": "Book/sh = Total Shareholders' Equity / Shares Outstanding.",
        "detail": "Positive and growing means building shareholder value. Negative book value means liabilities exceed assets (very concerning). Compare to stock price via P/B ratio. Useful for banks and asset-heavy companies, less useful for tech/service companies.",
    },
    "rsi": {
        "name": "RSI (14)", "direction": "neutral", "good_range": "30-70",
        "desc": "Momentum oscillator measuring overbought/oversold conditions.",
        "calc": "RSI = 100 - [100 / (1 + (Avg Gain over 14 periods / Avg Loss over 14 periods))].",
        "detail": "<30 = oversold (potential buying opportunity). 30-70 = neutral range. >70 = overbought (potential selling opportunity or caution). RSI divergence (price makes new high but RSI doesn't) can signal a reversal. Works best in ranging markets, less reliable in strong trends.",
    },
    "beta": {
        "name": "Beta", "direction": "neutral", "good_range": "0.5-1.5",
        "desc": "Volatility relative to the overall market (S&P 500).",
        "calc": "Beta = Covariance(Stock Returns, Market Returns) / Variance(Market Returns). Typically calculated over 5 years of monthly returns.",
        "detail": "Beta 1.0 = stock moves in line with market. >1.0 = more volatile (1.5 means 50% more movement than market). <1.0 = less volatile. <0 = moves inversely to market (rare). High-beta stocks amplify both gains and losses. Conservative investors prefer lower beta.",
    },
    "atr": {
        "name": "ATR (14)", "direction": "neutral", "good_range": "Compare to stock price",
        "desc": "Average True Range. Measures average daily price volatility.",
        "calc": "True Range = Max of (High-Low, |High-Prev Close|, |Low-Prev Close|). ATR = 14-period moving average of True Range.",
        "detail": "Not good or bad -- it's a volatility measure. Higher ATR = more volatile (bigger daily swings). Useful for setting stop-losses (e.g., 2x ATR below entry) and position sizing. Compare ATR to stock price for context. ATR of $2 means different things for a $10 stock vs a $200 stock.",
    },
    "volatility_week": {
        "name": "Volatility Week", "direction": "neutral", "good_range": "Lower = more stable",
        "desc": "Annualized standard deviation of weekly returns.",
        "calc": "Standard deviation of weekly returns, annualized (x sqrt(52)).",
        "detail": "Lower volatility means more predictable price movements. Higher means larger swings (higher risk and potential reward). Compare to sector and market volatility.",
    },
    "volatility_month": {
        "name": "Volatility Month", "direction": "neutral", "good_range": "Lower = more stable",
        "desc": "Annualized standard deviation of monthly returns.",
        "calc": "Standard deviation of monthly returns, annualized (x sqrt(12)).",
        "detail": "Lower volatility means more predictable price movements. Higher means larger swings. Compare to sector and market volatility.",
    },
    "sma20": {
        "name": "SMA20 %", "direction": "neutral", "good_range": "Above = short-term uptrend",
        "desc": "Percentage distance from the 20-day simple moving average. Short-term trend indicator.",
        "calc": "SMA20 = Sum of Last 20 Closing Prices / 20. Shown as % distance from SMA.",
        "detail": "Price above SMA20 = short-term uptrend. Price below = short-term downtrend. Large negative values suggest the stock is potentially oversold in the short term.",
    },
    "sma50": {
        "name": "SMA50 %", "direction": "neutral", "good_range": "Above = intermediate uptrend",
        "desc": "Percentage distance from the 50-day simple moving average. Intermediate trend indicator.",
        "calc": "SMA50 = Sum of Last 50 Closing Prices / 50. Shown as % distance from SMA.",
        "detail": "Price above SMA50 = intermediate uptrend. SMA50 crossing above SMA200 = 'Golden Cross' (bullish). SMA50 crossing below SMA200 = 'Death Cross' (bearish).",
    },
    "sma200": {
        "name": "SMA200 %", "direction": "neutral", "good_range": "Above = long-term uptrend",
        "desc": "Percentage distance from the 200-day simple moving average. The most widely followed long-term trend indicator.",
        "calc": "SMA200 = Sum of Last 200 Closing Prices / 200. Shown as % distance from SMA.",
        "detail": "Price above SMA200 = long-term uptrend (most institutions only buy above the 200-day). Below = long-term downtrend. The further below SMA200, the more beaten down the stock is.",
    },
    "short_float": {
        "name": "Short Float", "direction": "lower", "good_range": "<5%",
        "desc": "Percentage of float sold short by bearish investors.",
        "calc": "Short Float = Shares Sold Short / Float x 100%.",
        "detail": "<5% = low short interest (minimal bearish sentiment). 5-10% = moderate. 10-20% = elevated (significant bearish bet). >20% = very high, potential for short squeeze if stock rises. High short interest can be bearish (many expect decline) or can lead to explosive upside if sentiment shifts.",
    },
    "short_ratio": {
        "name": "Short Ratio", "direction": "lower", "good_range": "<3 days",
        "desc": "Days it would take short sellers to cover their positions based on average volume.",
        "calc": "Short Ratio = Shares Sold Short / Average Daily Volume.",
        "detail": "<2 days = short sellers can cover quickly (lower squeeze potential). 2-5 days = moderate. >5 days = harder to cover (higher squeeze potential). >10 days = very crowded short trade.",
    },
    "short_interest": {
        "name": "Short Interest", "direction": "lower", "good_range": "Compare to historical",
        "desc": "Total number of shares currently sold short.",
        "calc": "Reported by exchanges twice per month.",
        "detail": "Compare to historical levels and float. Rising short interest = growing bearish sentiment. Falling = shorts are covering. Useful in conjunction with short float % and short ratio.",
    },
    "revenue_growth_ttm": {
        "name": "Revenue Growth TTM", "direction": "higher", "good_range": ">10%",
        "desc": "Year-over-year revenue growth on a trailing twelve month basis.",
        "calc": "(Revenue TTM current - Revenue TTM prior year) / Revenue TTM prior year x 100%.",
        "detail": "Positive and consistent = good. >20% = strong growth. >50% = hyper-growth. Negative = revenue declining (concerning unless temporary/cyclical). Compare to industry growth rates and the company's own historical trend.",
    },
    "eps_growth_ttm": {
        "name": "EPS Growth TTM", "direction": "higher", "good_range": ">15%",
        "desc": "Year-over-year earnings per share growth.",
        "calc": "(EPS TTM current - EPS TTM prior year) / |EPS TTM prior year| x 100%.",
        "detail": "Positive and ideally higher than revenue growth (implies margin expansion). >25% = strong. Negative = earnings declining. Can be volatile due to one-time charges and tax effects.",
    },
    "eps_growth_next_y": {
        "name": "EPS Growth Next Y", "direction": "higher", "good_range": ">15%",
        "desc": "Analyst consensus estimate for EPS growth over the next 12 months.",
        "calc": "(Estimated Next Year EPS - Current Year EPS) / |Current Year EPS| x 100%.",
        "detail": "Positive = analysts expect earnings to grow. >15% = solid expected growth. >25% = strong. Negative = analysts expect decline. Caution: analyst estimates are often wrong, especially for smaller companies.",
    },
    "eps_growth_next_5y": {
        "name": "EPS Growth Next 5Y", "direction": "higher", "good_range": ">15%",
        "desc": "Analyst consensus estimate for annualized EPS growth over the next 5 years.",
        "calc": "Based on long-range analyst projections (compound annual growth rate).",
        "detail": ">15% = strong long-term growth expected. 10-15% = solid. <10% = moderate. Used in PEG ratio calculation. Long-term estimates are inherently less reliable than short-term.",
    },
    "eps_this_y": {
        "name": "EPS This Y", "direction": "higher", "good_range": "Higher than last year",
        "desc": "Analyst estimate for full-year EPS for the current fiscal year.",
        "calc": "Compiled from individual analyst models and estimates.",
        "detail": "Compare to last year's actual EPS. An increase = expected growth. Upward revisions are bullish; downward revisions are bearish.",
    },
    "eps_next_q": {
        "name": "EPS Next Q", "direction": "higher", "good_range": "Beat estimates",
        "desc": "Analyst consensus estimate for EPS in the next fiscal quarter.",
        "calc": "Compiled from individual analyst models and estimates.",
        "detail": "Compare to current quarter to see trajectory. Upward revisions to estimates are bullish.",
    },
    "eps_past_3y": {
        "name": "EPS Past 3Y", "direction": "higher", "good_range": ">15% CAGR",
        "desc": "Compound annual growth rate of EPS over the past 3 years.",
        "calc": "CAGR = (EPS_end / EPS_start)^(1/3) - 1.",
        "detail": "Positive = earnings have been growing historically. >15% CAGR = strong track record. Compare past growth to forward estimates. Decelerating growth may indicate a maturing business.",
    },
    "eps_past_5y": {
        "name": "EPS Past 5Y", "direction": "higher", "good_range": ">15% CAGR",
        "desc": "Compound annual growth rate of EPS over the past 5 years.",
        "calc": "CAGR = (EPS_end / EPS_start)^(1/5) - 1.",
        "detail": "Longer track record than 3Y. Positive = earnings growing. >15% = strong. Compare to forward estimates to assess trajectory.",
    },
    "sales_past_3y": {
        "name": "Sales Past 3Y", "direction": "higher", "good_range": ">10% CAGR",
        "desc": "Compound annual growth rate of revenue over the past 3 years.",
        "calc": "CAGR = (Revenue_end / Revenue_start)^(1/3) - 1.",
        "detail": "Positive = revenue growing. >10% CAGR = solid. Compare to EPS growth -- if revenue grows faster than earnings, margins are contracting.",
    },
    "sales_past_5y": {
        "name": "Sales Past 5Y", "direction": "higher", "good_range": ">10% CAGR",
        "desc": "Compound annual growth rate of revenue over the past 5 years.",
        "calc": "CAGR = (Revenue_end / Revenue_start)^(1/5) - 1.",
        "detail": "Longer track record. Positive = revenue growing. Compare to EPS growth to check margin trends.",
    },
    "sales_qyq": {
        "name": "Sales Q/Q", "direction": "higher", "good_range": "Positive",
        "desc": "Quarter-over-quarter sequential revenue growth.",
        "calc": "(Current Q Revenue - Previous Q Revenue) / Previous Q Revenue x 100%.",
        "detail": "Shows momentum but is affected by seasonality. Year-over-year comparisons are usually more meaningful for most businesses.",
    },
    "eps_qoq": {
        "name": "EPS Q/Q", "direction": "higher", "good_range": "Positive",
        "desc": "Quarter-over-quarter sequential EPS growth.",
        "calc": "(Current Q EPS - Previous Q EPS) / |Previous Q EPS| x 100%.",
        "detail": "Can show momentum but affected by seasonality. Year-over-year comparisons are usually more meaningful.",
    },
    "eps_surprise": {
        "name": "EPS Surprise", "direction": "higher", "good_range": "Positive = beat",
        "desc": "How much actual EPS exceeded or missed analyst estimates.",
        "calc": "Surprise = (Actual EPS - Estimate) / |Estimate| x 100%.",
        "detail": "Positive = beat estimates (bullish). Negative = missed (bearish). Consistent beats suggest conservative guidance or strong execution. The market's reaction to the surprise often matters more than the number itself.",
    },
    "sales_surprise": {
        "name": "Sales Surprise", "direction": "higher", "good_range": "Positive = beat",
        "desc": "How much actual revenue exceeded or missed analyst estimates.",
        "calc": "Surprise = (Actual Revenue - Estimate) / |Estimate| x 100%.",
        "detail": "Positive = revenue beat (bullish). Revenue beats combined with EPS beats are the strongest signal. Revenue misses are often more concerning than EPS misses.",
    },
    "insider_own": {
        "name": "Insider Ownership", "direction": "higher", "good_range": "5-20%",
        "desc": "Percentage of shares held by company insiders (officers, directors).",
        "calc": "Total Insider Shares / Shares Outstanding x 100%.",
        "detail": "5-20% = healthy alignment of management with shareholders. >30% = strong alignment but watch for control issues. <1% = insiders have little skin in the game. Very high insider ownership can also mean low public float.",
    },
    "insider_trans": {
        "name": "Insider Trans", "direction": "neutral", "good_range": "Buying = bullish",
        "desc": "Percentage of insider transactional activity relative to their holdings.",
        "calc": "Reported percentage of insider transactional activity.",
        "detail": "Insider BUYING is almost always a positive signal (they know the company best). Insider SELLING is a weaker negative signal -- insiders sell for many reasons (diversification, taxes, expenses). Low transaction volume is normal. Cluster buying (multiple insiders buying) is especially strong.",
    },
    "inst_own": {
        "name": "Institutional Own", "direction": "higher", "good_range": "50-80%",
        "desc": "Percentage of shares held by institutional investors.",
        "calc": "Total Institutional Shares / Shares Outstanding x 100%.",
        "detail": "50-80% = healthy institutional interest (implies validation through due diligence). >80% = very heavily institutional, potential for large block sales. <30% = low interest, could be underfollowed (potential hidden gem or value trap). Increasing institutional ownership is a positive signal.",
    },
    "inst_trans": {
        "name": "Institutional Trans", "direction": "higher", "good_range": "Positive = accumulating",
        "desc": "Net change in institutional ownership over the last 3 months.",
        "calc": "Current Institutional Holdings % - Holdings % 3 Months Ago.",
        "detail": "Positive = institutions are accumulating (bullish). Negative = institutions are selling (bearish or cautious). Large positive delta with price declines could indicate smart money buying the dip.",
    },
    "shares_outstanding": {
        "name": "Shares Outstanding", "direction": "neutral", "good_range": "Watch for trends",
        "desc": "Total shares issued by the company, including restricted shares.",
        "calc": "Reported by the company in SEC filings.",
        "detail": "Increasing shares = dilution (bad for existing shareholders). Decreasing shares = buybacks (generally positive). Watch the trend over time rather than the absolute number.",
    },
    "shares_float": {
        "name": "Shares Float", "direction": "neutral", "good_range": "Higher = more liquid",
        "desc": "Shares available for public trading (excludes insider/restricted).",
        "calc": "Float = Shares Outstanding - Restricted Shares - Insider Holdings.",
        "detail": "Lower float = more volatile price movements. Higher float = more liquidity and stability. Very low float stocks (<10M) can be extremely volatile.",
    },
    "target_price": {
        "name": "Target Price", "direction": "higher", "good_range": "Above current price",
        "desc": "Average analyst 12-month price target.",
        "calc": "Average of all individual analyst price targets.",
        "detail": "Target above current price = analysts see upside. Below = analysts see downside. The further above, the more bullish consensus is. Caution: analyst targets are often lagging and biased upward (most analysts maintain buy ratings).",
    },
    "recommendation": {
        "name": "Analyst Rec", "direction": "lower", "good_range": "1-2 (Buy)",
        "desc": "Average analyst recommendation. 1=Strong Buy through 5=Strong Sell.",
        "calc": "Average of all analyst ratings on a 1-5 scale.",
        "detail": "<2.0 = consensus buy. 2.0-3.0 = moderate buy to hold. >3.0 = hold to sell. Upgrades/downgrades (changes in rating) are often more impactful than the absolute level. Be aware of analyst bias -- very few stocks receive sell ratings.",
    },
    "rel_volume": {
        "name": "Relative Volume", "direction": "neutral", "good_range": "~1.0 normal, >1.5 elevated",
        "desc": "Current volume compared to average. Shows unusual trading activity.",
        "calc": "Relative Volume = Current Volume / Average Volume (typically 50-day average).",
        "detail": "~1.0 = normal. >1.5 = above-average interest. >2.0 = significantly elevated (watch for news, earnings, breakout). <0.5 = unusually low (low conviction in current move). High relative volume on breakouts confirms the move. Low volume breakouts more likely to fail.",
    },
    "volume": {
        "name": "Volume", "direction": "neutral", "good_range": "Compare to average",
        "desc": "Total shares traded in the most recent session.",
        "calc": "Sum of all shares exchanged during the trading day.",
        "detail": "Compare to average volume. Volume confirms price moves -- rising price on high volume is more meaningful than on low volume.",
    },
    "avg_volume": {
        "name": "Avg Volume", "direction": "neutral", "good_range": ">1M for liquidity",
        "desc": "Average daily trading volume (typically 50-day or 3-month).",
        "calc": "Sum of daily volumes over period / Number of trading days.",
        "detail": "Higher = more liquid (easier to trade without moving price). >1M shares/day = liquid for most investors. <100K/day = potentially illiquid, wider spreads, harder to exit.",
    },
    "week_52_high": {
        "name": "52W High", "direction": "neutral", "good_range": "Near = strong momentum",
        "desc": "Highest price traded in the past 52 weeks.",
        "calc": "Tracked from daily price data over the past year.",
        "detail": "Trading near 52W High = strong momentum (or potentially extended). Used with the % distance metric to gauge where the stock sits in its range.",
    },
    "week_52_high_pct": {
        "name": "52W High %", "direction": "neutral", "good_range": "Near 0% = at highs",
        "desc": "Percentage below the 52-week high.",
        "calc": "(Current Price - 52W High) / 52W High x 100%.",
        "detail": "Near 0% means trading near the high. Large negatives mean far below the high (beaten down). A stock down >50% from highs is either a value opportunity or a falling knife -- check fundamentals.",
    },
    "week_52_low": {
        "name": "52W Low", "direction": "neutral", "good_range": "Far above = strong performance",
        "desc": "Lowest price traded in the past 52 weeks.",
        "calc": "Tracked from daily price data over the past year.",
        "detail": "Trading near 52W Low = beaten down (potential value or falling knife). Used with the % distance metric.",
    },
    "week_52_low_pct": {
        "name": "52W Low %", "direction": "neutral", "good_range": "Higher = recovered well",
        "desc": "Percentage above the 52-week low.",
        "calc": "(Current Price - 52W Low) / 52W Low x 100%.",
        "detail": "Shows how far the stock has recovered from its low. Very high values mean strong recovery. Near 0% means still near the lows.",
    },
    "prev_close": {
        "name": "Prev Close", "direction": "neutral", "good_range": "Reference point",
        "desc": "Closing price from the previous trading session.",
        "calc": "Last traded price at market close of the prior day.",
        "detail": "Reference point for today's trading. The day's change is calculated relative to this value.",
    },
    "price_change": {
        "name": "Change %", "direction": "neutral", "good_range": "Context-dependent",
        "desc": "Percentage change from the prior day's close.",
        "calc": "(Current Price - Previous Close) / Previous Close x 100%.",
        "detail": "Large single-day moves often correlate with news events or earnings. Context-dependent -- not inherently good or bad.",
    },
    "dividend_ttm": {
        "name": "Dividend TTM", "direction": "higher", "good_range": "Positive and growing",
        "desc": "Total dividends paid per share over the trailing twelve months.",
        "calc": "Sum of all dividends paid per share in the last 12 months.",
        "detail": "Higher is better for income investors. Compare to historical dividend. Growing dividends is positive. Must be evaluated in context of payout ratio and company financials.",
    },
    "dividend_yield": {
        "name": "Dividend Yield", "direction": "higher", "good_range": "2-5% for most",
        "desc": "Annual dividend as a percentage of the stock price.",
        "calc": "Dividend Yield = Annual Dividend Per Share / Current Stock Price x 100%.",
        "detail": "Higher yield means more income per dollar invested, but very high yields (>8%) may signal the market expects a dividend cut. Compare to sector averages. Must check payout ratio sustainability.",
    },
    "dividend_est": {
        "name": "Dividend Est", "direction": "higher", "good_range": "Higher than TTM",
        "desc": "Expected annual dividend based on most recent declared rate.",
        "calc": "Based on the most recently declared dividend rate, annualized.",
        "detail": "Compare to TTM dividend. Higher estimate = expected dividend increase. Lower = potential cut.",
    },
    "dividend_yield_est": {
        "name": "Div Yield Est", "direction": "higher", "good_range": "2-5%",
        "desc": "Estimated dividend yield based on forward dividend estimate.",
        "calc": "Estimated Annual Dividend / Current Price x 100%.",
        "detail": "Forward-looking version of dividend yield. Compare to current yield to see expected changes.",
    },
    "payout_ratio": {
        "name": "Payout Ratio", "direction": "neutral", "good_range": "<60% sustainable",
        "desc": "Percentage of earnings paid out as dividends.",
        "calc": "Payout Ratio = Dividends Per Share / Earnings Per Share x 100%.",
        "detail": "<50% = sustainable, room to grow dividend. 50-75% = moderate. >75% = high, dividend at risk if earnings decline. >100% = paying more than earned (unsustainable long-term). REITs and MLPs are exceptions -- required to pay high payout ratios.",
    },
    "ex_dividend_date": {
        "name": "Ex-Div Date", "direction": "neutral", "good_range": "Informational",
        "desc": "Date after which new buyers will NOT receive the next dividend.",
        "calc": "Set by the company's board of directors.",
        "detail": "You must own the stock BEFORE this date to receive the dividend. Important for timing dividend capture strategies.",
    },
    "dividend_gr_3y": {
        "name": "Div Growth 3Y", "direction": "higher", "good_range": ">7%",
        "desc": "Compound annual growth rate of dividends over the past 3 years.",
        "calc": "CAGR of dividend per share over 3 years.",
        "detail": "Positive and consistent growth is very attractive for income investors. >7% = strong. >10% = excellent. Declining or inconsistent dividends are red flags.",
    },
    "dividend_gr_5y": {
        "name": "Div Growth 5Y", "direction": "higher", "good_range": ">7%",
        "desc": "Compound annual growth rate of dividends over the past 5 years.",
        "calc": "CAGR of dividend per share over 5 years.",
        "detail": "Longer track record than 3Y. Consistent growth over 5 years is a strong quality signal for income stocks.",
    },
    "perf_week": {
        "name": "Perf Week", "direction": "higher", "good_range": "Compare to benchmark",
        "desc": "Total price return over the past week.",
        "calc": "(Current Price - Price 1 Week Ago) / Price 1 Week Ago x 100%.",
        "detail": "Compare to S&P 500 over the same period. Short-term metric subject to noise.",
    },
    "perf_month": {
        "name": "Perf Month", "direction": "higher", "good_range": "Compare to benchmark",
        "desc": "Total price return over the past month.",
        "calc": "(Current Price - Price 1 Month Ago) / Price 1 Month Ago x 100%.",
        "detail": "Compare to benchmark. Strong short-term performance with weak long-term could indicate a bounce in a declining stock.",
    },
    "perf_quarter": {
        "name": "Perf Quarter", "direction": "higher", "good_range": "Compare to benchmark",
        "desc": "Total price return over the past quarter.",
        "calc": "(Current Price - Price 3 Months Ago) / Price 3 Months Ago x 100%.",
        "detail": "Compare to S&P 500 and sector performance. Outperforming = stock doing relatively well.",
    },
    "perf_half_y": {
        "name": "Perf Half Y", "direction": "higher", "good_range": "Compare to benchmark",
        "desc": "Total price return over the past 6 months.",
        "calc": "(Current Price - Price 6 Months Ago) / Price 6 Months Ago x 100%.",
        "detail": "Medium-term performance. Compare to benchmark and peers.",
    },
    "perf_year": {
        "name": "Perf Year", "direction": "higher", "good_range": "Compare to benchmark",
        "desc": "Total price return over the past year.",
        "calc": "(Current Price - Price 1 Year Ago) / Price 1 Year Ago x 100%.",
        "detail": "Important benchmark. Strong long-term performance with weak short-term may signal a buying opportunity if fundamentals are intact.",
    },
    "perf_ytd": {
        "name": "Perf YTD", "direction": "higher", "good_range": "Compare to benchmark",
        "desc": "Total price return year-to-date.",
        "calc": "(Current Price - Price at Year Start) / Price at Year Start x 100%.",
        "detail": "How the stock has performed this calendar year. Compare to S&P 500 YTD return.",
    },
    "perf_3y": {
        "name": "Perf 3Y", "direction": "higher", "good_range": "Compare to benchmark",
        "desc": "Total price return over the past 3 years.",
        "calc": "(Current Price - Price 3 Years Ago) / Price 3 Years Ago x 100%.",
        "detail": "Long-term track record. Compare to index total return over same period.",
    },
    "perf_5y": {
        "name": "Perf 5Y", "direction": "higher", "good_range": "Compare to benchmark",
        "desc": "Total price return over the past 5 years.",
        "calc": "(Current Price - Price 5 Years Ago) / Price 5 Years Ago x 100%.",
        "detail": "Long-term track record. Useful for evaluating management quality and business durability.",
    },
    "perf_10y": {
        "name": "Perf 10Y", "direction": "higher", "good_range": "Compare to benchmark",
        "desc": "Total price return over the past 10 years.",
        "calc": "(Current Price - Price 10 Years Ago) / Price 10 Years Ago x 100%.",
        "detail": "The longest track record available. Compounders that have done well over 10 years often have strong fundamentals.",
    },
    "employees": {
        "name": "Employees", "direction": "neutral", "good_range": "Context-dependent",
        "desc": "Total number of employees at the company.",
        "calc": "Reported by the company in 10-K filings.",
        "detail": "Revenue per employee is a better metric. High-margin tech companies may have few employees relative to revenue. Compare within the same industry.",
    },
    "ipo_date": {
        "name": "IPO Date", "direction": "neutral", "good_range": "Older = longer track record",
        "desc": "Date the company first went public on a stock exchange.",
        "calc": "Historical record.",
        "detail": "Newer IPOs (<2 years) tend to be more volatile and may lack earnings history. Mature public companies have longer track records for analysis.",
    },
    "earnings_date": {
        "name": "Earnings Date", "direction": "neutral", "good_range": "Informational",
        "desc": "The next scheduled earnings report date.",
        "calc": "Announced by the company.",
        "detail": "Important for anticipating volatility. Stocks often move significantly around earnings. BMO = Before Market Open. AMC = After Market Close.",
    },
    "option_short": {
        "name": "Option/Short", "direction": "neutral", "good_range": "Informational",
        "desc": "Whether options are available and the stock can be shorted.",
        "calc": "Determined by exchange and broker availability.",
        "detail": "Yes/Yes = full trading flexibility. Options availability enables hedging strategies.",
    },
}


# ── Entry point ────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)