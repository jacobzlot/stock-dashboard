"""
Stock Analysis Dashboard — Flask Backend
Supports Postgres (Railway) and SQLite (local dev).
"""
import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, render_template, jsonify, request, g
from flask_cors import CORS

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
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db.commit()


def _load_shortlist():
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
                return json.load(f)
        return []


def _save_shortlist_add(ticker):
    if USE_POSTGRES:
        _ensure_shortlist_table()
        db_execute_write(
            f"INSERT INTO shortlist (ticker) VALUES ({P}) ON CONFLICT (ticker) DO NOTHING",
            (ticker,)
        )
    else:
        sl = _load_shortlist()
        if ticker not in sl:
            sl.append(ticker)
        _save_shortlist_file(sl)


def _save_shortlist_remove(ticker):
    if USE_POSTGRES:
        _ensure_shortlist_table()
        db_execute_write(f"DELETE FROM shortlist WHERE ticker = {P}", (ticker,))
    else:
        sl = _load_shortlist()
        sl = [t for t in sl if t != ticker]
        _save_shortlist_file(sl)


def _save_shortlist_file(tickers):
    """SQLite fallback: save to JSON file."""
    with open(SHORTLIST_PATH, 'w') as f:
        json.dump(sorted(set(tickers)), f)


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

    return jsonify(data)


@app.route("/api/shortlist", methods=["GET"])
def api_shortlist_get():
    return jsonify(_load_shortlist())


@app.route("/api/shortlist", methods=["POST"])
def api_shortlist_update():
    body = request.get_json()
    ticker = body.get("ticker")
    action = body.get("action", "toggle")

    sl = _load_shortlist()
    currently_in = ticker in sl

    if action == "add" or (action == "toggle" and not currently_in):
        _save_shortlist_add(ticker)
        shortlisted = True
    elif action == "remove" or (action == "toggle" and currently_in):
        _save_shortlist_remove(ticker)
        shortlisted = False
    else:
        shortlisted = currently_in

    return jsonify({
        "shortlist": _load_shortlist(),
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

    return jsonify({"shortlist": _load_shortlist()})


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


@app.route("/api/metrics_guide")
def api_metrics_guide():
    return jsonify(METRICS_GUIDE)


# ── Metrics reference data ─────────────────────────────────

METRICS_GUIDE = {
    "pe_ratio": {
        "name": "P/E Ratio", "direction": "lower", "good_range": "10-25",
        "desc": "Price relative to earnings. Lower = cheaper. Compare within industry.",
    },
    "forward_pe": {
        "name": "Forward P/E", "direction": "lower", "good_range": "Below trailing P/E",
        "desc": "P/E using estimated future earnings. Lower than trailing = expected growth.",
    },
    "peg_ratio": {
        "name": "PEG Ratio", "direction": "lower", "good_range": "<1 undervalued, 1 fair",
        "desc": "P/E adjusted for growth. <1 = potentially undervalued relative to growth.",
    },
    "ps_ratio": {
        "name": "P/S Ratio", "direction": "lower", "good_range": "<3 for most industries",
        "desc": "Price relative to revenue. Useful for unprofitable companies.",
    },
    "pb_ratio": {
        "name": "P/B Ratio", "direction": "lower", "good_range": "1-3",
        "desc": "Price relative to book value. <1 could be bargain. Critical for banks.",
    },
    "pfcf_ratio": {
        "name": "P/FCF", "direction": "lower", "good_range": "<20",
        "desc": "Price relative to free cash flow. Harder to manipulate than P/E.",
    },
    "ev_ebitda": {
        "name": "EV/EBITDA", "direction": "lower", "good_range": "<12",
        "desc": "Enterprise value to EBITDA. Capital-structure neutral valuation.",
    },
    "profit_margin": {
        "name": "Profit Margin", "direction": "higher", "good_range": ">10%",
        "desc": "Net income as % of revenue. Higher = more profitable.",
    },
    "operating_margin": {
        "name": "Operating Margin", "direction": "higher", "good_range": ">15%",
        "desc": "Operating income as % of revenue. Shows operational efficiency.",
    },
    "gross_margin": {
        "name": "Gross Margin", "direction": "higher", "good_range": ">40%",
        "desc": "Revenue minus COGS as % of revenue. Industry-dependent.",
    },
    "roe": {
        "name": "ROE", "direction": "higher", "good_range": ">15%",
        "desc": "Return on equity. >15% = strong. Very high may indicate leverage.",
    },
    "roa": {
        "name": "ROA", "direction": "higher", "good_range": ">5%",
        "desc": "Return on assets. How efficiently assets generate profit.",
    },
    "roic": {
        "name": "ROIC", "direction": "higher", "good_range": ">15%",
        "desc": "Return on invested capital. Should exceed cost of capital.",
    },
    "debt_to_equity": {
        "name": "Debt/Equity", "direction": "lower", "good_range": "<1.0",
        "desc": "Total debt vs equity. <0.5 conservative, >2 high leverage.",
    },
    "current_ratio": {
        "name": "Current Ratio", "direction": "higher", "good_range": ">1.5",
        "desc": "Current assets vs liabilities. <1 = potential liquidity issues.",
    },
    "quick_ratio": {
        "name": "Quick Ratio", "direction": "higher", "good_range": ">1.0",
        "desc": "Liquid assets vs liabilities (excl. inventory).",
    },
    "rsi": {
        "name": "RSI (14)", "direction": "neutral", "good_range": "30-70",
        "desc": "Momentum oscillator. <30 = oversold. >70 = overbought.",
    },
    "beta": {
        "name": "Beta", "direction": "neutral", "good_range": "0.5-1.5",
        "desc": "Volatility vs market. 1 = market-like. >1 = more volatile.",
    },
    "short_float": {
        "name": "Short Float", "direction": "lower", "good_range": "<5%",
        "desc": "% of float sold short. >20% = high bearish sentiment / squeeze potential.",
    },
    "revenue_growth_ttm": {
        "name": "Revenue Growth TTM", "direction": "higher", "good_range": ">10%",
        "desc": "Year-over-year revenue growth. Positive = growing business.",
    },
    "eps_growth_ttm": {
        "name": "EPS Growth TTM", "direction": "higher", "good_range": ">15%",
        "desc": "Year-over-year earnings growth.",
    },
    "insider_own": {
        "name": "Insider Ownership", "direction": "higher", "good_range": "5-20%",
        "desc": "% held by insiders. Shows alignment with shareholders.",
    },
    "inst_own": {
        "name": "Institutional Own", "direction": "higher", "good_range": "50-80%",
        "desc": "% held by institutions. Higher = more validation.",
    },
    "recommendation": {
        "name": "Analyst Rec", "direction": "lower", "good_range": "1-2 (Buy)",
        "desc": "1=Strong Buy, 2=Buy, 3=Hold, 4=Sell, 5=Strong Sell.",
    },
}


# ── Entry point ────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)