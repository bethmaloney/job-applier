import os
from dotenv import load_dotenv

load_dotenv()

# Flask
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
DATABASE = os.path.join(os.path.dirname(__file__), "jobs.db")

# Scraper settings
REQUEST_TIMEOUT = 15
SCRAPE_DELAY_MIN = 2
SCRAPE_DELAY_MAX = 5
LINKEDIN_MAX_PAGES = 3
LINKEDIN_PAGE_SIZE = 25

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Default search URLs / params
SEEK_SEARCH_URLS = [
    "https://www.seek.com.au/software-engineer-jobs/in-Melbourne-VIC?daterange=7",
    "https://www.seek.com.au/software-developer-jobs/in-Melbourne-VIC?daterange=7",
    "https://www.seek.com.au/python-developer-jobs/in-Melbourne-VIC?daterange=7",
]

LINKEDIN_SEARCHES = [
    {"keywords": "software engineer", "location": "Melbourne, Victoria, Australia"},
    {"keywords": "software developer", "location": "Melbourne, Victoria, Australia"},
]

# Claude CLI ranking
CLAUDE_CLI_PATH = "claude"  # assumes claude is on PATH
CLAUDE_MODEL = "sonnet"
CLAUDE_COVER_LETTER_MODEL = "opus"
