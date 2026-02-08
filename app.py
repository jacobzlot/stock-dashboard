"""
Stock Analysis Dashboard — Flask Backend
"""
import sqlite3
import json
import os
from flask import Flask, render_template, jsonify, request, g
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DB_PATH = os.environ.get("DB_PATH", "stocks.db")
SHORTLIST_PATH = os.environ.get("SHORTLIST_PATH", "shortlist.json")

# ── Database helpers ───────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()

# ── Shortlist persistence (simple JSON file) ──────────────

def _load_shortlist():
    if os.path.exists(SHORTLIST_PATH):
        with open(SHORTLIST_PATH, "r") as f:
            return json.load(f)
    return []

def _save_shortlist(tickers):
    with open(SHORTLIST_PATH, "w") as f:
        json.dump(sorted(set(tickers)), f)

# ── Column metadata ───────────────────────────────────────

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

# Human-readable labels + formatting hints for every column
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
    """Return column groups, column metadata, and filter options."""
    db = get_db()
    industries = [r[0] for r in db.execute(
        "SELECT DISTINCT industry FROM stocks WHERE industry IS NOT NULL ORDER BY industry"
    ).fetchall()]
    sectors = [r[0] for r in db.execute(
        "SELECT DISTINCT sector FROM stocks WHERE sector IS NOT NULL AND sector != '' ORDER BY sector"
    ).fetchall()]

    return jsonify({
        "column_groups": COLUMN_GROUPS,
        "column_meta": COLUMN_META,
        "industries": industries,
        "sectors": sectors,
        "total_stocks": db.execute("SELECT COUNT(*) FROM stocks").fetchone()[0],
    })


@app.route("/api/stocks")
def api_stocks():
    """Return all stock data. Supports query params for filtering."""
    db = get_db()

    where_clauses = []
    params = []

    industry = request.args.get("industry")
    if industry:
        where_clauses.append("industry = ?")
        params.append(industry)

    sector = request.args.get("sector")
    if sector:
        where_clauses.append("sector = ?")
        params.append(sector)

    # Market cap range filter
    min_cap = request.args.get("min_cap")
    if min_cap:
        where_clauses.append("market_cap >= ?")
        params.append(int(min_cap))
    max_cap = request.args.get("max_cap")
    if max_cap:
        where_clauses.append("market_cap <= ?")
        params.append(int(max_cap))

    # RSI range
    min_rsi = request.args.get("min_rsi")
    if min_rsi:
        where_clauses.append("rsi >= ?")
        params.append(float(min_rsi))
    max_rsi = request.args.get("max_rsi")
    if max_rsi:
        where_clauses.append("rsi <= ?")
        params.append(float(max_rsi))

    # PE range
    min_pe = request.args.get("min_pe")
    if min_pe:
        where_clauses.append("pe_ratio >= ?")
        params.append(float(min_pe))
    max_pe = request.args.get("max_pe")
    if max_pe:
        where_clauses.append("pe_ratio <= ?")
        params.append(float(max_pe))

    # Shortlist only
    shortlist_only = request.args.get("shortlist_only")
    if shortlist_only == "true":
        sl = _load_shortlist()
        if sl:
            placeholders = ",".join("?" * len(sl))
            where_clauses.append(f"ticker IN ({placeholders})")
            params.extend(sl)
        else:
            return jsonify([])

    where = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    query = f"SELECT * FROM stocks{where} ORDER BY market_cap DESC NULLS LAST"

    rows = db.execute(query, params).fetchall()
    columns = [desc[0] for desc in db.execute(f"SELECT * FROM stocks LIMIT 0").description]

    shortlist = set(_load_shortlist())
    result = []
    for row in rows:
        d = {columns[i]: row[i] for i in range(len(columns))}
        d["_shortlisted"] = d["ticker"] in shortlist
        result.append(d)

    return jsonify(result)


@app.route("/api/stock/<ticker>")
def api_stock_detail(ticker):
    """Return full detail + history for a single stock."""
    db = get_db()
    row = db.execute("SELECT * FROM stocks WHERE ticker = ?", (ticker,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404

    columns = [desc[0] for desc in db.execute("SELECT * FROM stocks LIMIT 0").description]
    data = {columns[i]: row[i] for i in range(len(columns))}

    history = db.execute(
        "SELECT * FROM stock_history WHERE ticker = ? ORDER BY date", (ticker,)
    ).fetchall()
    hist_cols = [desc[0] for desc in db.execute("SELECT * FROM stock_history LIMIT 0").description]
    data["history"] = [{hist_cols[i]: h[i] for i in range(len(hist_cols))} for h in history]

    # Industry peers (same industry, top 10 by market cap)
    if data.get("industry"):
        peers = db.execute(
            """SELECT ticker, company_name, price, market_cap, pe_ratio, ps_ratio,
                      profit_margin, roe, rsi, debt_to_equity, revenue_growth_ttm
               FROM stocks WHERE industry = ? AND ticker != ?
               ORDER BY market_cap DESC NULLS LAST LIMIT 10""",
            (data["industry"], ticker),
        ).fetchall()
        data["peers"] = [dict(p) for p in peers]
    else:
        data["peers"] = []

    # Industry averages
    if data.get("industry"):
        avgs = db.execute(
            """SELECT
                AVG(pe_ratio) as avg_pe, AVG(ps_ratio) as avg_ps, AVG(pb_ratio) as avg_pb,
                AVG(profit_margin) as avg_profit_margin, AVG(operating_margin) as avg_oper_margin,
                AVG(gross_margin) as avg_gross_margin, AVG(roe) as avg_roe, AVG(roa) as avg_roa,
                AVG(roic) as avg_roic, AVG(debt_to_equity) as avg_de, AVG(current_ratio) as avg_cr,
                AVG(revenue_growth_ttm) as avg_rev_growth, AVG(rsi) as avg_rsi, AVG(beta) as avg_beta,
                AVG(peg_ratio) as avg_peg, AVG(pfcf_ratio) as avg_pfcf,
                COUNT(*) as peer_count
               FROM stocks WHERE industry = ? AND pe_ratio IS NOT NULL""",
            (data["industry"],),
        ).fetchone()
        data["industry_averages"] = dict(avgs) if avgs else {}

    shortlist = set(_load_shortlist())
    data["_shortlisted"] = ticker in shortlist

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
    if action == "add" or (action == "toggle" and ticker not in sl):
        if ticker not in sl:
            sl.append(ticker)
    elif action == "remove" or (action == "toggle" and ticker in sl):
        sl = [t for t in sl if t != ticker]

    _save_shortlist(sl)
    return jsonify({"shortlist": sl, "ticker": ticker, "shortlisted": ticker in sl})


@app.route("/api/shortlist/bulk", methods=["POST"])
def api_shortlist_bulk():
    body = request.get_json()
    tickers = body.get("tickers", [])
    action = body.get("action", "add")
    sl = _load_shortlist()
    if action == "add":
        sl = list(set(sl + tickers))
    elif action == "remove":
        sl = [t for t in sl if t not in tickers]
    elif action == "set":
        sl = tickers
    _save_shortlist(sl)
    return jsonify({"shortlist": sl})


@app.route("/api/industry_stats")
def api_industry_stats():
    """Return aggregate stats per industry."""
    db = get_db()
    rows = db.execute("""
        SELECT industry,
            COUNT(*) as count,
            AVG(pe_ratio) as avg_pe,
            AVG(ps_ratio) as avg_ps,
            AVG(pb_ratio) as avg_pb,
            AVG(profit_margin) as avg_profit_margin,
            AVG(roe) as avg_roe,
            AVG(roa) as avg_roa,
            AVG(debt_to_equity) as avg_de,
            AVG(revenue_growth_ttm) as avg_rev_growth,
            AVG(rsi) as avg_rsi,
            AVG(market_cap) as avg_market_cap,
            SUM(market_cap) as total_market_cap
        FROM stocks
        WHERE industry IS NOT NULL
        GROUP BY industry
        ORDER BY total_market_cap DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/metrics_guide")
def api_metrics_guide():
    """Return the metrics reference guide as JSON."""
    return jsonify(METRICS_GUIDE)


# ── Metrics reference data ─────────────────────────────────

METRICS_GUIDE = {
    "pe_ratio": {
        "name": "P/E Ratio", "direction": "lower", "good_range": "10–25",
        "desc": "Price relative to earnings. Lower = cheaper. Compare within industry.",
        "warning_high": 50, "warning_low": 0,
    },
    "forward_pe": {
        "name": "Forward P/E", "direction": "lower", "good_range": "Below trailing P/E",
        "desc": "P/E using estimated future earnings. Lower than trailing = expected growth.",
    },
    "peg_ratio": {
        "name": "PEG Ratio", "direction": "lower", "good_range": "<1 undervalued, 1 fair",
        "desc": "P/E adjusted for growth. <1 = potentially undervalued relative to growth.",
        "warning_high": 2,
    },
    "ps_ratio": {
        "name": "P/S Ratio", "direction": "lower", "good_range": "<3 for most industries",
        "desc": "Price relative to revenue. Useful for unprofitable companies.",
    },
    "pb_ratio": {
        "name": "P/B Ratio", "direction": "lower", "good_range": "1–3",
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
        "warning_high": 2,
    },
    "current_ratio": {
        "name": "Current Ratio", "direction": "higher", "good_range": ">1.5",
        "desc": "Current assets vs liabilities. <1 = potential liquidity issues.",
        "warning_low": 1,
    },
    "quick_ratio": {
        "name": "Quick Ratio", "direction": "higher", "good_range": ">1.0",
        "desc": "Liquid assets vs liabilities (excl. inventory).",
        "warning_low": 0.5,
    },
    "rsi": {
        "name": "RSI (14)", "direction": "neutral", "good_range": "30–70",
        "desc": "Momentum oscillator. <30 = oversold. >70 = overbought.",
        "warning_high": 70, "warning_low": 30,
    },
    "beta": {
        "name": "Beta", "direction": "neutral", "good_range": "0.5–1.5",
        "desc": "Volatility vs market. 1 = market-like. >1 = more volatile.",
    },
    "short_float": {
        "name": "Short Float", "direction": "lower", "good_range": "<5%",
        "desc": "% of float sold short. >20% = high bearish sentiment / squeeze potential.",
        "warning_high": 0.2,
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
        "name": "Insider Ownership", "direction": "higher", "good_range": "5–20%",
        "desc": "% held by insiders. Shows alignment with shareholders.",
    },
    "inst_own": {
        "name": "Institutional Own", "direction": "higher", "good_range": "50–80%",
        "desc": "% held by institutions. Higher = more validation.",
    },
    "recommendation": {
        "name": "Analyst Rec", "direction": "lower", "good_range": "1–2 (Buy)",
        "desc": "1=Strong Buy, 2=Buy, 3=Hold, 4=Sell, 5=Strong Sell.",
    },
}


# ── Entry point ────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
