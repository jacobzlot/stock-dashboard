# Stock Analysis Dashboard

A full-featured stock screening and analysis dashboard powered by FinViz data, built with Flask + AG Grid, deployable on Railway with automated data refresh via cron.

## Features

- **2,400+ stocks** with 90+ financial metrics from FinViz
- **Preset views**: Overview, Valuation, Profitability, Growth, Health, Technical, All Columns
- **Custom column picker**: Choose exactly which metrics to display
- **Industry filtering**: Filter by any of 94 industries
- **Market cap filtering**: Mega, Large, Mid, Small, Micro cap tiers
- **Quick search**: Search by ticker or company name
- **Shortlist**: Star stocks for further review with persistent storage
- **Stock detail panel**: Click any ticker for deep-dive with industry peer comparison
- **Metrics guide**: Built-in reference explaining every financial metric
- **CSV export**: Export current view to CSV
- **Automated refresh**: Cron-scheduled scraper keeps data current

---

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run the dashboard
python app.py

# Visit http://localhost:5000
```

## Railway Deployment

### Step 1: Create a new Railway project

1. Go to [railway.app](https://railway.app) and create a new project
2. Select **"Deploy from GitHub repo"** (push this code to a GitHub repo first)
3. Or use **"Deploy from local"** with the Railway CLI

### Step 2: Add a persistent volume

**This is critical** — SQLite needs persistent storage across deploys.

1. In your Railway project, click on your service
2. Go to **Settings → Volumes**
3. Click **"Add Volume"**
4. Mount path: `/data`
5. This stores `stocks.db` and `shortlist.json` persistently

### Step 3: Set environment variables

In the Railway service settings, add:

| Variable | Value | Description |
|----------|-------|-------------|
| `DB_PATH` | `/data/stocks.db` | Database location on volume |
| `SHORTLIST_PATH` | `/data/shortlist.json` | Shortlist persistence |
| `PORT` | `5000` | (Railway usually sets this automatically) |

### Step 4: Deploy

Railway will automatically build from the Dockerfile and start the web service.

---

## Setting Up the Cron Scraper on Railway

Railway supports cron jobs via a separate **cron service** in the same project.

### Option A: Separate Cron Service (Recommended)

1. In your Railway project, click **"+ New Service"**
2. Select the same GitHub repo
3. In the new service's **Settings**:
   - **Start Command**: `python cron_scrape.py`
   - **Cron Schedule**: Choose one:
     - Weekly (Monday 6 AM UTC): `0 6 * * 1`
     - Monthly (1st of month 6 AM UTC): `0 6 1 * *`
     - Bi-weekly: `0 6 1,15 * *`
4. Add the same volume mount (`/data`) so both services share the database
5. Set the same environment variables (`DB_PATH=/data/stocks.db`)

### Option B: Manual Trigger via Railway CLI

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and link project
railway login
railway link

# Run the scraper manually
railway run python cron_scrape.py
```

### Scraper Configuration

The scraper has a configurable delay between requests to avoid rate limiting:

| Variable | Default | Description |
|----------|---------|-------------|
| `SCRAPE_DELAY` | `2.5` | Seconds between FinViz requests |
| `CSV_PATH` | `StockSource.csv` | Fallback ticker source |

**Estimated scrape times:**
- 2,400 stocks × 2.5s delay = ~100 minutes
- Adjust `SCRAPE_DELAY` based on your needs (minimum ~2s to avoid blocks)

---

## Project Structure

```
stock-dashboard/
├── app.py                 # Flask backend (API + routes)
├── templates/
│   └── index.html         # Dashboard frontend (AG Grid)
├── scraper.py             # FinViz scraper
├── cron_scrape.py         # Cron job script for automated refresh
├── setup_database.py      # SQLite schema creation
├── load_data.py           # JSON → SQLite data loader
├── reprocess_json.py      # Fix multi-value fields in JSON
├── stocks.db              # Seed database (2,400+ stocks)
├── stock_data.json        # Raw scraped data backup
├── requirements.txt       # Python dependencies
├── Dockerfile             # Container build
├── entrypoint.sh          # First-run database seeding
├── railway.toml           # Railway deployment config
├── Procfile               # Process definition
└── README.md              # This file
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard UI |
| `/api/meta` | GET | Column metadata, industries, stats |
| `/api/stocks` | GET | All stocks (with query filters) |
| `/api/stocks?industry=X` | GET | Filter by industry |
| `/api/stocks?min_cap=X&max_cap=Y` | GET | Filter by market cap |
| `/api/stocks?shortlist_only=true` | GET | Shortlisted stocks only |
| `/api/stock/<ticker>` | GET | Full detail + peers + history |
| `/api/shortlist` | GET | Current shortlist |
| `/api/shortlist` | POST | Add/remove/toggle ticker |
| `/api/shortlist/bulk` | POST | Bulk shortlist operations |
| `/api/industry_stats` | GET | Aggregate stats per industry |
| `/api/metrics_guide` | GET | Metrics reference data |

## Tech Stack

- **Backend**: Flask + SQLite + Gunicorn
- **Frontend**: Vanilla JS + AG Grid Community Edition
- **Scraper**: BeautifulSoup + Requests
- **Deployment**: Docker + Railway
