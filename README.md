# FinResearchAgent

**Buy-side equity and macro research, fully automated.**

FinResearchAgent is an open-source, multi-agent AI pipeline that produces institutional-quality investment research from a single ticker or topic. Submit a request and receive a structured, cited, fact-checked research memo — equity reports run to 21 sections, macro reports to 8 sections plus a literature review.

Built on [Google Vertex AI](https://cloud.google.com/vertex-ai) and the [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/), deployed to [Cloud Run](https://cloud.google.com/run).

---

## How it works

The pipeline runs a coordinated team of specialized AI agents, each with a defined role and strict tool budget. Python gathers all structured financial data directly from APIs before any LLM call starts, so agents spend their time on analysis rather than data retrieval.

```
User request (ticker or topic)
        │
        ▼
 ┌─────────────────────────────────────────────────┐
 │  Step 1a  Python API gather (parallel)          │
 │           Finnhub · FMP · Alpha Vantage         │
 │           Polygon · SEC EDGAR · NewsAPI         │
 │           OpenFIGI · FRED · World Bank · OECD   │
 └──────────────────┬──────────────────────────────┘
                    │
        ┌───────────▼───────────┐
        │  Step 1b              │
        │  Context Processor    │  ← reads user focus notes,
        │  (if context given)   │    fetches targeted gaps,
        └───────────┬───────────┘    guides data harvester
                    │
        ┌───────────▼───────────┐
        │  Step 1c              │
        │  Data Harvester       │  ← web search, coverage log,
        └───────────┬───────────┘    25+ source validation
                    │
        ┌───────────▼───────────────────────────────────┐
        │  Step 2  Six analyst agents (parallel)        │
        │  · Fundamental (market)                       │
        │  · Fundamental (financials)                   │
        │  · Competitive                                │
        │  · Risk                                       │
        │  · Valuation                                  │
        │  · Earnings Quality                           │
        └───────────┬───────────────────────────────────┘
                    │
        ┌───────────▼───────────┐
        │  Step 3               │
        │  Quant Modeler        │  ← DCF · scenario matrix
        └───────────┬───────────┘    · technical overlay
                    │
        ┌───────────▼───────────┐
        │  Step 4               │
        │  Report Compiler      │  ← assembles 21-section memo
        └───────────┬───────────┘
                    │
        ┌───────────▼───────────┐
        │  Step 5  Review loop  │
        │  Fact Checker         │  ← up to 3 passes
        │  Review Agent         │    PASS / FAIL + revise
        └───────────┬───────────┘
                    │
        ┌───────────▼───────────┐
        │  Step 6               │
        │  Orchestrator         │  ← executive summary
        └───────────┬───────────┘
                    │
                    ▼
          Final report saved to
          Google Cloud Storage
```

---

## Features

- **21-section equity memos** covering thesis, financials, competitive position, valuation, risk, and quantitative models
- **8-section macro reports** with econometric modelling and academic literature review
- **Python-first data gathering** — 22 pre-fetched data sources per equity run; LLMs receive clean structured data, not raw API calls
- **Context processor** — paste in your own research notes and the pipeline adapts every agent's focus accordingly
- **Automated QA loop** — Fact Checker and Review Agent validate citations, math, and internal consistency before delivery
- **Parallel execution** — six analyst agents run simultaneously; rate-limit retry logic with full-jitter backoff handles free-tier quotas gracefully
- **Web UI** — submit requests and read reports from any browser; no CLI required
- **Scheduled analysis** — configure tickers and macro topics to run automatically via Cloud Scheduler
- **LaTeX export** — reports can be converted to publication-quality PDFs

---

## Data sources

| Source | Type | Used in |
|---|---|---|
| [Finnhub](https://finnhub.io) | Price, financials, earnings, analyst ratings | Equity |
| [Financial Modeling Prep](https://financialmodelingprep.com) | Income statement, balance sheet, cash flow | Equity |
| [Alpha Vantage](https://www.alphavantage.co) | Price, overview, EPS | Equity |
| [Polygon.io](https://polygon.io) | Ticker details, OHLCV, news | Equity |
| [SEC EDGAR](https://www.sec.gov/edgar) | Filings, insider transactions, reported financials | Equity |
| [NewsAPI](https://newsapi.org) | Financial news from WSJ, FT, Bloomberg, Reuters | Both |
| [OpenFIGI](https://www.openfigi.com) | Instrument identification and metadata | Equity |
| [FRED](https://fred.stlouisfed.org) | US yield curve, recession indicators | Macro |
| [World Bank](https://data.worldbank.org) | Cross-country GDP, inflation, unemployment | Macro |
| [OECD](https://data-explorer.oecd.org) | Composite leading indicators, economic outlook | Macro |
| [CORE](https://core.ac.uk) | Academic papers (no quota cost) | Both |
| [Semantic Scholar](https://www.semanticscholar.org) | Academic research | Both |

> **World Bank and OECD require no API key.** All other keys are stored in Google Cloud Secret Manager — never in code or config files.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| [Google Cloud project](https://console.cloud.google.com) | Free trial works; pay-as-you-go recommended for parallel execution |
| [Google Cloud CLI (`gcloud`)](https://cloud.google.com/sdk/docs/install) | Used by `deploy.sh` |
| Python 3.11+ | Local development only; container handles production |
| [Docker](https://docs.docker.com/get-docker/) | Built automatically by Cloud Build during deployment |

**Required API keys** (all have free tiers):

| Key | Where to get it |
|---|---|
| Finnhub | [finnhub.io](https://finnhub.io) |
| Financial Modeling Prep | [financialmodelingprep.com](https://financialmodelingprep.com) |
| Alpha Vantage | [alphavantage.co](https://www.alphavantage.co) |
| Polygon | [polygon.io](https://polygon.io) |
| FRED | [fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html) |
| CORE | [core.ac.uk/api-keys](https://core.ac.uk/api-keys) |
| NewsAPI | [newsapi.org](https://newsapi.org) |
| OpenFIGI | [openfigi.com/api](https://www.openfigi.com/api) |
| Semantic Scholar | Optional — API works without a key |

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/fin-research-agent.git
cd fin-research-agent
```

### 2. Create your config file

```bash
cp config.yaml.example config.yaml
```

Open `config.yaml` and fill in:
- `google_cloud.project_id` — your GCP project ID
- `google_cloud.reports_bucket` — a Cloud Storage bucket name (will be created if it doesn't exist)
- `google_cloud.sec_user_agent` — your email address (required by SEC EDGAR)

> `config.yaml` is gitignored and never committed. It stays on your machine only.

### 3. Store API keys in Secret Manager

For each key, run:

```bash
echo -n "YOUR_ACTUAL_KEY" | gcloud secrets create finnhub-api-key \
  --data-file=- \
  --project=YOUR_GCP_PROJECT_ID
```

Repeat for all keys listed in the `secrets:` block of your `config.yaml`. The secret names in `config.yaml` must exactly match the names you use in Secret Manager.

### 4. Deploy to Cloud Run

```bash
chmod +x deploy.sh
./deploy.sh
```

The script will:
1. Enable required GCP APIs (Cloud Run, Secret Manager, Cloud Build, Cloud Storage)
2. Grant the Cloud Run service account access to your secrets
3. Build the Docker image via Cloud Build
4. Deploy the container to Cloud Run with all secrets mounted as environment variables

Deployment takes 3–5 minutes on first run.

### 5. Open the web UI

The deploy script prints the Cloud Run service URL when it finishes:

```
Service URL: https://fin-research-agent-xxxx-uc.a.run.app
```

Open that URL in your browser to access the research interface.

---

## Usage

### Web interface

1. Open the service URL
2. Choose **Equity** or **Macro**
3. Enter a ticker (e.g. `NVDA`) or a macro topic (e.g. `European inflation outlook`)
4. Optionally paste research notes in the context box — the pipeline will adapt all agents to your focus
5. Click **Run Research** and wait ~15–25 minutes for the full report

### REST API

```bash
# Equity research
curl -X POST https://YOUR_SERVICE_URL/research \
  -H "Content-Type: application/json" \
  -d '{"ticker": "AAPL", "research_type": "equity", "user_context": ""}'

# Macro research
curl -X POST https://YOUR_RESEARCH_URL/research \
  -H "Content-Type: application/json" \
  -d '{"ticker": "US interest rate outlook", "research_type": "macro"}'
```

### Scheduled analysis

Add tickers or macro topics to the `scheduled_runs` block in `config.yaml`, then redeploy. Set the trigger frequency in [Cloud Scheduler](https://console.cloud.google.com/cloudscheduler).

---

## Configuration reference

All runtime behaviour is controlled by `config.yaml`. Key settings:

| Setting | Default | Description |
|---|---|---|
| `models.tier1` | `gemini-2.5-flash` | Model for orchestrator, fact checker, review agent |
| `models.tier_compiler` | `gemini-2.5-pro` | Model for report compiler (long-document assembly) |
| `review.max_passes` | `3` | Maximum QA loop iterations before delivering with review notes |
| `report.expected_return_hurdle` | `0.30` | Required expected return for a Buy rating (30%) |
| `report.margin_of_safety` | `0.25` | Required discount to fair value mid-point (25%) |
| `concurrency.max_parallel_agents` | `3` | Simultaneous Vertex AI calls (set to `1` on free-tier quota) |
| `search.min_interval_seconds` | `2.0` | Throttle between web search calls |

---

## Project structure

```
├── main.py                  # FastAPI application and pipeline orchestration
├── agents/
│   └── team.py              # All agent definitions (models, tools, instructions)
├── prompts/                 # System prompts for each agent (19 markdown files)
├── tools/                   # Data source integrations (12 Python modules)
│   ├── finnhub_data.py
│   ├── fmp_data.py
│   ├── sec_filings.py
│   ├── macro_data.py        # FRED
│   ├── worldbank_data.py
│   ├── oecd_data.py
│   ├── news_api.py
│   ├── openfigi_data.py
│   └── ...
├── web/
│   └── index.html           # Web UI
├── Dockerfile
├── deploy.sh                # One-command Cloud Run deployment
├── config.yaml.example      # Configuration template (copy → config.yaml)
└── requirements.txt
```

---

## Contributing

Contributions are welcome. To get started:

```bash
# Fork the repo, then clone your fork
git clone https://github.com/YOUR_USERNAME/fin-research-agent.git
cd fin-research-agent

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up your config
cp config.yaml.example config.yaml
# Fill in config.yaml with your project details
```

**Good places to start:**
- Add a new data source in `tools/` following the pattern in `worldbank_data.py`
- Improve an agent prompt in `prompts/` — each file is self-contained
- Add a new analyst agent by defining it in `agents/team.py` and wiring it into the pipeline in `main.py`

Please open an issue before starting significant work so we can discuss the approach.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Disclaimer

FinResearchAgent produces AI-generated research for informational purposes only. It is not financial advice. Always conduct your own due diligence before making investment decisions.
