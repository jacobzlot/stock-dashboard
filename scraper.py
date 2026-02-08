import requests
from bs4 import BeautifulSoup
import time
import json
import csv
import re
from datetime import datetime

# ──────────────────────────────────────────────────────────
# MULTI-VALUE FIELD DEFINITIONS
# These Finviz snapshot fields render two values in a single
# cell (using multiple <b>/<span> children). We split them
# into separate keys at scrape time.
# ──────────────────────────────────────────────────────────

# Fields where the cell contains a price/number followed by a
# percentage delta, e.g. "5.45-13.94%" → (5.45, -0.1394)
_52W_FIELDS = {'52w_high', '52w_low'}

# Fields where the cell contains two percentages (3Y and 5Y
# or EPS and Sales), e.g. "65.40%37.40%"
_TWO_PCT_FIELDS = {
    'eps_past_3_5y',       # EPS past 3Y / EPS past 5Y
    'sales_past_3_5y',     # Sales past 3Y / Sales past 5Y
    'dividend_gr._3_5y',   # Dividend growth 3Y / 5Y
    'eps_sales_surpr.',    # EPS surprise / Sales surprise
}

# Fields with "week month" volatility, e.g. "4.10% 6.28%"
_VOLATILITY_FIELDS = {'volatility'}

# Fields with "amount (yield%)", e.g. "0.06 (1.28%)"
_DIVIDEND_AMT_FIELDS = {'dividend_ttm', 'dividend_est.'}


# ──────────────────────────────────────────────────────────
# SPLIT HELPERS
# ──────────────────────────────────────────────────────────

_RE_52W = re.compile(r'^(\d+(?:\.\d{1,2})?)(-?\d+\.?\d*%)$')

def _split_52w(value):
    """Split '5.45-13.94%' → (5.45, -0.1394)"""
    if not isinstance(value, str):
        return value, None
    m = _RE_52W.match(value.strip())
    if m:
        price = float(m.group(1))
        pct = float(m.group(2).replace('%', '')) / 100
        return price, pct
    return value, None


_RE_PCT_TOKEN = re.compile(r'-?\d+\.?\d*%|-')

def _split_two_pcts(value):
    """Split '65.40%37.40%' → (0.654, 0.374) or '- -' → (None, None)"""
    if not isinstance(value, str):
        return value, None
    parts = _RE_PCT_TOKEN.findall(value.strip())
    if len(parts) == 2:
        def _to_float(p):
            if p == '-':
                return None
            return float(p.replace('%', '')) / 100
        return _to_float(parts[0]), _to_float(parts[1])
    return value, None


_RE_DIV_AMT = re.compile(r'^([\d.]+)\s*\(([\d.]+)%\)$')

def _split_dividend_amt(value):
    """Split '0.06 (1.28%)' → (0.06, 0.0128)"""
    if not isinstance(value, str):
        return value, None
    m = _RE_DIV_AMT.match(value.strip())
    if m:
        amount = float(m.group(1))
        pct = float(m.group(2)) / 100
        return amount, pct
    return value, None


# Mapping from original key → (primary_key, secondary_key, splitter)
MULTI_VALUE_SPLIT_MAP = {
    '52w_high':          ('52w_high',          '52w_high_pct',          _split_52w),
    '52w_low':           ('52w_low',           '52w_low_pct',           _split_52w),
    'eps_past_3_5y':     ('eps_past_3y',       'eps_past_5y',           _split_two_pcts),
    'sales_past_3_5y':   ('sales_past_3y',     'sales_past_5y',        _split_two_pcts),
    'dividend_gr._3_5y': ('dividend_gr_3y',    'dividend_gr_5y',        _split_two_pcts),
    'eps_sales_surpr.':  ('eps_surprise',      'sales_surprise',        _split_two_pcts),
    'volatility':        ('volatility_week',   'volatility_month',      _split_two_pcts),
    'dividend_ttm':      ('dividend_ttm',      'dividend_yield',        _split_dividend_amt),
    'dividend_est.':     ('dividend_est',      'dividend_yield_est',    _split_dividend_amt),
}


def split_multi_value_fields(data: dict) -> dict:
    """Post-process a scraped stock dict: split any multi-value fields
    into their component parts. Works on both freshly-scraped dicts
    and already-stored JSON records.
    """
    result = {}
    for key, value in data.items():
        if key in MULTI_VALUE_SPLIT_MAP:
            primary_key, secondary_key, splitter = MULTI_VALUE_SPLIT_MAP[key]
            primary_val, secondary_val = splitter(value)
            result[primary_key] = primary_val
            if secondary_val is not None:
                result[secondary_key] = secondary_val
        else:
            result[key] = value
    return result


# ──────────────────────────────────────────────────────────
# SCRAPER
# ──────────────────────────────────────────────────────────

class FinvizScraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })

    def _extract_cell_texts(self, cell):
        """Extract child texts from a snapshot-table value cell.

        Finviz value cells may contain multiple <b> or <span> children
        for multi-value fields (e.g. 52W High shows the price in one
        element and the % distance in another). This method returns
        a list of individual text fragments rather than one concatenated
        string, so the caller can detect and handle multi-value cells.
        """
        children = list(cell.children)
        # If the cell has <b> or <span> sub-elements, extract each
        block_tags = cell.find_all(['b', 'span', 'small'], recursive=False)
        if len(block_tags) >= 2:
            texts = [tag.get_text(strip=True) for tag in block_tags if tag.get_text(strip=True)]
            if texts:
                return texts
        # Fallback: single value
        text = cell.get_text(strip=True)
        return [text] if text else []

    def scrape_stock(self, ticker):
        try:
            url = f"https://finviz.com/quote.ashx?t={ticker}"
            response = self.session.get(url, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')

            company_elem = soup.find('a', {'class': 'tab-link'})
            company_name = company_elem.text.strip() if company_elem else "Unknown"

            if company_name == "Affiliate":
                return None

            data = {'ticker': ticker, 'company_name': company_name}

            table = soup.find('table', {'class': 'snapshot-table2'})
            if table:
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all('td')
                    for i in range(0, len(cells) - 1, 2):
                        label = cells[i].get_text(strip=True)
                        key = label.lower().replace(' ', '_').replace('/', '_')

                        # Extract child texts separately
                        texts = self._extract_cell_texts(cells[i + 1])
                        if not texts or all(t == '-' for t in texts):
                            continue

                        if key in MULTI_VALUE_SPLIT_MAP and len(texts) >= 2:
                            # We got clean separate values from HTML children
                            primary_key, secondary_key, _ = MULTI_VALUE_SPLIT_MAP[key]
                            data[primary_key] = self._parse_value(texts[0])
                            data[secondary_key] = self._parse_value(texts[1])
                        elif key in MULTI_VALUE_SPLIT_MAP and len(texts) == 1:
                            # Fallback: HTML didn't split, store raw for post-processing
                            data[key] = texts[0]
                        else:
                            value = texts[0] if len(texts) == 1 else ' '.join(texts)
                            if value and value != '-':
                                data[key] = self._parse_value(value)

            # Post-process: split any remaining concatenated multi-value fields
            data = split_multi_value_fields(data)
            return data

        except Exception as e:
            return None

    def _parse_value(self, value):
        if not value or value == '-':
            return None

        if '%' in value:
            try:
                return float(value.replace('%', '')) / 100
            except:
                return value

        if value[-1] in ['B', 'M', 'K']:
            multipliers = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}
            try:
                num = float(value[:-1])
                return int(num * multipliers[value[-1]])
            except:
                return value

        try:
            value_clean = value.replace(',', '')
            if '.' in value_clean:
                return float(value_clean)
            return int(value_clean)
        except:
            return value

    def scrape_multiple(self, tickers, delay=2.0):
        results = []
        affiliates = []
        for i, ticker in enumerate(tickers, 1):
            print(f"[{i}/{len(tickers)}] {ticker} ", end=" ")
            data = self.scrape_stock(ticker)
            if data:
                results.append(data)
                print("✓")
            else:
                affiliates.append(ticker)
                print("⊗ affiliate")

            if i < len(tickers):
                time.sleep(delay)

        return results, affiliates


def load_csv(csv_path='StockSource.csv'):
    tickers = []
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = (row.get('Exchange:Ticker') or row.get('Ticker') or row.get('ticker', '')).strip()
                if ticker:
                    tickers.append(ticker)
        return tickers
    except Exception as e:
        print(f"Error: {e}")
        return []


def scrape_all(csv_path='StockSource.csv', output='stock_data.json', delay=2.0):
    tickers = load_csv(csv_path)

    if not tickers:
        print("No tickers")
        return

    est_hours = len(tickers) * delay / 3600
    print(f"{len(tickers)} stocks, ~{est_hours:.1f}h")

    resp = input("Continue? (y/n): ").lower()
    if resp != 'y':
        print("Cancelled")
        return

    start = datetime.now()
    scraper = FinvizScraper()
    results, affiliates = scraper.scrape_multiple(tickers, delay=delay)
    duration = datetime.now() - start

    with open(output, 'w') as f:
        json.dump({
            'scraped_at': start.isoformat(),
            'duration_seconds': duration.total_seconds(),
            'total_stocks': len(tickers),
            'successful': len(results),
            'affiliates_skipped': len(affiliates),
            'failed': len(tickers) - len(results) - len(affiliates),
            'stocks': results
        }, f, indent=2, default=str)

    if affiliates:
        with open('affiliates_skipped.txt', 'w') as f:
            f.write('\n'.join(affiliates))

    print(f"\n{len(results)}/{len(tickers)} stocks ({len(affiliates)} affiliates skipped)")
    print(f"Duration: {duration}")
    print(f"Saved: {output}")


if __name__ == "__main__":
    import sys
    csv_path = sys.argv[1] if len(sys.argv) > 1 else 'StockSource.csv'
    scrape_all(csv_path)