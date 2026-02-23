# Job Applier

A job scraping and ranking application that automatically collects listings from Seek and LinkedIn, then uses Claude AI to rank them by relevance to your profile.

## Features

- **Multi-source scraping** — Fetches jobs from Seek and LinkedIn with configurable search queries
- **AI-powered ranking** — Uses Claude CLI to score jobs 1–10 based on your profile
- **Web dashboard** — Filter, sort, and manage jobs with a clean Tailwind CSS interface
- **Status tracking** — Mark jobs as seen, dismissed, or applied
- **Background operations** — Scraping, ranking, and refreshing run in background threads
- **Smart deduplication** — Prevents duplicate listings using composite keys per source
- **Polite scraping** — Random 2–5 second delays between requests

## Prerequisites

- Python 3.8+
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed and available on your PATH

## Getting Started

### 1. Clone the repository

```bash
git clone <repo-url>
cd job-applier
```

### 2. Create a virtual environment

On Debian/Ubuntu, you may need to install the venv package first:

```bash
sudo apt install python3-venv
```

Then create and activate the environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and set your secret key:

```
FLASK_SECRET_KEY=change-me-to-something-random
```

### 5. Run the application

```bash
python app.py
```

The app will be available at **http://localhost:5000**.

## Usage

1. **Set up your profile** — Go to Settings and fill in your skills, target titles, preferences, minimum salary, and experience summary
2. **Fetch jobs** — Click "Fetch New Jobs" on the dashboard to scrape Seek and LinkedIn
3. **Rank jobs** — Click "Rank" to have Claude score unranked jobs against your profile
4. **Review** — Browse jobs on the dashboard sorted by relevance, with color-coded score badges
5. **Manage** — Mark jobs as Seen, Dismiss ones you're not interested in, or mark as Applied
6. **Refresh** — Re-fetch job detail pages and re-rank to get updated descriptions

## Configuration

Search queries and scraper settings are configured in `config.py`:

- `SEEK_SEARCH_URLS` — List of Seek search URL paths
- `LINKEDIN_SEARCHES` — List of `{keywords, location}` dicts for LinkedIn
- `LINKEDIN_MAX_PAGES` — Max pagination pages per LinkedIn search (default: 3)
- `CLAUDE_MODEL` — Claude model used for ranking (default: `sonnet`)

## Tech Stack

- **Backend:** Flask, SQLite
- **Scraping:** BeautifulSoup4, requests, curl_cffi
- **Frontend:** Jinja2 templates, Tailwind CSS
- **AI:** Claude CLI (called via subprocess)
