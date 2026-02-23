import json
import re
import time
import random
import logging
from urllib.parse import urljoin, urlencode, quote

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

import config
import database

logger = logging.getLogger(__name__)

SESSION_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}


def _polite_delay():
    time.sleep(random.uniform(config.SCRAPE_DELAY_MIN, config.SCRAPE_DELAY_MAX))


def _get(url, session=None):
    s = session or requests
    resp = s.get(url, headers=SESSION_HEADERS, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


# --- Seek scraper ---

def _parse_seek_json(soup):
    """Try to extract jobs from Seek's embedded JSON data."""
    jobs = []
    for script in soup.find_all("script", type="application/json"):
        try:
            data = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue

        # Seek embeds job data in various JSON structures — search recursively
        found = _find_seek_jobs_in_data(data)
        if found:
            jobs.extend(found)
    return jobs


def _find_seek_jobs_in_data(data, depth=0):
    """Recursively search JSON for job listing arrays."""
    if depth > 10:
        return []
    jobs = []

    if isinstance(data, dict):
        # Look for common Seek job fields
        if "id" in data and "title" in data and ("advertiser" in data or "company" in data):
            job = _normalize_seek_json_job(data)
            if job:
                jobs.append(job)
        else:
            for value in data.values():
                jobs.extend(_find_seek_jobs_in_data(value, depth + 1))
    elif isinstance(data, list):
        for item in data:
            jobs.extend(_find_seek_jobs_in_data(item, depth + 1))

    return jobs


def _normalize_seek_json_job(data):
    """Convert a Seek JSON job object to our standard format."""
    try:
        external_id = str(data.get("id", ""))
        if not external_id:
            return None

        advertiser = data.get("advertiser") or {}
        company = advertiser.get("description", "") if isinstance(advertiser, dict) else str(advertiser)

        location = ""
        loc_data = data.get("location") or data.get("suburb") or ""
        if isinstance(loc_data, str):
            location = loc_data
        elif isinstance(loc_data, dict):
            location = loc_data.get("label", "")
        elif isinstance(loc_data, list) and loc_data:
            location = loc_data[0].get("label", "") if isinstance(loc_data[0], dict) else str(loc_data[0])

        salary = data.get("salary", "") or data.get("salaryLabel", "") or ""
        if isinstance(salary, dict):
            salary = salary.get("label", "")

        listing_date = data.get("listingDate") or data.get("listedAt") or ""
        if isinstance(listing_date, dict):
            listing_date = listing_date.get("label", "")

        return {
            "source": "seek",
            "external_id": external_id,
            "title": data.get("title", ""),
            "company": company,
            "location": location,
            "description": data.get("teaser", "") or data.get("abstract", "") or "",
            "url": f"https://www.seek.com.au/job/{external_id}",
            "salary": str(salary),
            "posted_date": str(listing_date),
        }
    except Exception as e:
        logger.warning(f"Failed to normalize Seek JSON job: {e}")
        return None


def _parse_seek_html(soup):
    """Fall back to parsing Seek HTML job cards."""
    jobs = []
    # Seek uses data-testid attributes on job card articles
    cards = soup.select('article[data-testid="job-card"]')
    if not cards:
        cards = soup.select('[data-card-type="JobCard"]')
    if not cards:
        # broader fallback
        cards = soup.select('article')

    for card in cards:
        try:
            link = card.find("a", href=re.compile(r"/job/\d+"))
            if not link:
                continue

            href = link.get("href", "")
            match = re.search(r"/job/(\d+)", href)
            if not match:
                continue

            external_id = match.group(1)
            title = link.get_text(strip=True)

            company_el = card.select_one('[data-testid="company-name"]') or card.find("a", {"data-type": "company"})
            company = company_el.get_text(strip=True) if company_el else ""

            location_el = card.select_one('[data-testid="job-location"]') or card.find("a", {"data-type": "location"})
            location = location_el.get_text(strip=True) if location_el else ""

            salary_el = card.select_one('[data-testid="job-salary"]')
            salary = salary_el.get_text(strip=True) if salary_el else ""

            teaser_el = card.select_one('[data-testid="job-teaser"]') or card.find("span", class_=re.compile("teaser"))
            teaser = teaser_el.get_text(strip=True) if teaser_el else ""

            date_el = card.select_one("time") or card.select_one('[data-testid="listing-date"]')
            posted = date_el.get("datetime", date_el.get_text(strip=True)) if date_el else ""

            jobs.append({
                "source": "seek",
                "external_id": external_id,
                "title": title,
                "company": company,
                "location": location,
                "description": teaser,
                "url": f"https://www.seek.com.au/job/{external_id}",
                "salary": salary,
                "posted_date": posted,
            })
        except Exception as e:
            logger.warning(f"Failed to parse Seek card: {e}")
            continue

    return jobs


def _seek_get(url, session):
    """Fetch a Seek URL using curl_cffi to bypass TLS fingerprinting."""
    resp = session.get(url, headers=SESSION_HEADERS, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def _fetch_seek_detail(job_url, session):
    """Fetch a Seek job detail page and extract full description + salary."""
    try:
        resp = _seek_get(job_url, session)
    except Exception as e:
        logger.warning(f"Failed to fetch Seek detail {job_url}: {e}")
        return "", ""

    soup = BeautifulSoup(resp.text, "lxml")

    description = ""
    salary = ""

    # Try JSON-LD structured data first (most reliable)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            ld = json.loads(script.string or "")
            if isinstance(ld, list):
                ld = next((x for x in ld if x.get("@type") == "JobPosting"), None)
            if ld and ld.get("@type") == "JobPosting":
                raw = ld.get("description", "")
                if raw:
                    # Strip HTML tags from the description
                    desc_soup = BeautifulSoup(raw, "lxml")
                    description = desc_soup.get_text("\n", strip=True)
                base_salary = ld.get("baseSalary") or {}
                if isinstance(base_salary, dict):
                    value = base_salary.get("value") or {}
                    if isinstance(value, dict):
                        min_val = value.get("minValue", "")
                        max_val = value.get("maxValue", "")
                        if min_val and max_val:
                            salary = f"${min_val} - ${max_val}"
                        elif min_val:
                            salary = f"${min_val}"
                break
        except (json.JSONDecodeError, TypeError):
            continue

    # Fallback: try embedded JSON (Seek's React data)
    if not description:
        for script in soup.find_all("script", type="application/json"):
            try:
                data = json.loads(script.string or "")
                desc = _find_description_in_data(data)
                if desc:
                    description = desc
                    break
            except (json.JSONDecodeError, TypeError):
                continue

    # Fallback: try HTML selectors
    if not description:
        for selector in [
            '[data-automation="jobAdDetails"]',
            '[data-automation="jobDescription"]',
            'div[class*="jobAdDetails"]',
            'div[class*="job-description"]',
        ]:
            el = soup.select_one(selector)
            if el:
                description = el.get_text("\n", strip=True)
                break

    return description, salary


def _find_description_in_data(data, depth=0):
    """Recursively search JSON for a job description field."""
    if depth > 12:
        return ""
    if isinstance(data, dict):
        # Look for description-like keys with substantial text
        for key in ("description", "content", "jobDetail", "jobDescription"):
            val = data.get(key)
            if isinstance(val, str) and len(val) > 100:
                # Strip HTML if present
                if "<" in val:
                    return BeautifulSoup(val, "lxml").get_text("\n", strip=True)
                return val
        for val in data.values():
            result = _find_description_in_data(val, depth + 1)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = _find_description_in_data(item, depth + 1)
            if result:
                return result
    return ""


def scrape_seek():
    """Scrape all configured Seek search URLs."""
    all_jobs = []
    errors = []
    session = cffi_requests.Session(impersonate="chrome")

    for url in config.SEEK_SEARCH_URLS:
        try:
            logger.info(f"Fetching Seek: {url}")
            resp = _seek_get(url, session)
            soup = BeautifulSoup(resp.text, "lxml")

            # Try JSON first, fall back to HTML
            jobs = _parse_seek_json(soup)
            if not jobs:
                jobs = _parse_seek_html(soup)

            logger.info(f"Found {len(jobs)} jobs from {url}")
            all_jobs.extend(jobs)
            _polite_delay()

        except Exception as e:
            msg = f"Seek error ({url}): {e}"
            logger.error(msg)
            errors.append(msg)

    # Fetch full detail pages for each job (like LinkedIn scraper does)
    logger.info(f"Fetching detail pages for {len(all_jobs)} Seek jobs")
    for job in all_jobs:
        detail_url = job.get("url")
        if not detail_url:
            continue
        description, detail_salary = _fetch_seek_detail(detail_url, session)
        if description:
            job["description"] = description
        if detail_salary and not job.get("salary"):
            job["salary"] = detail_salary
        _polite_delay()

    return all_jobs, errors


def refresh_job_details():
    """Re-fetch detail pages for existing Seek and LinkedIn jobs and update descriptions."""
    conn = database.get_db()
    jobs = database.get_jobs_for_refresh(conn)
    seek_session = cffi_requests.Session(impersonate="chrome")
    linkedin_session = requests.Session()

    updated = 0
    errors = []
    logger.info(f"Refreshing details for {len(jobs)} jobs")

    for job in jobs:
        job = dict(job)
        detail_url = job.get("url")
        if not detail_url:
            continue
        try:
            source = job.get("source")
            if source == "seek":
                description, detail_salary = _fetch_seek_detail(detail_url, seek_session)
            elif source == "linkedin":
                description, detail_salary = _fetch_linkedin_detail(detail_url, linkedin_session)
            else:
                continue

            if description and description != (job.get("description") or ""):
                salary = detail_salary if detail_salary and not job.get("salary") else None
                database.update_job_detail(conn, job["id"], description, salary)
                updated += 1
                logger.info(f"Updated description for {source} job {job['id']}")
            _polite_delay()
        except Exception as e:
            msg = f"Refresh error ({job.get('source')} job {job['id']}): {e}"
            logger.error(msg)
            errors.append(msg)

    conn.close()
    return updated, errors


# --- LinkedIn scraper ---

LINKEDIN_GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def _parse_linkedin_cards(html):
    """Parse LinkedIn guest job search results."""
    soup = BeautifulSoup(html, "lxml")
    jobs = []

    cards = soup.find_all("li")
    for card in cards:
        try:
            base = card.find("div", class_=re.compile("base-card"))
            if not base:
                continue

            link = base.find("a", class_=re.compile("base-card__full-link")) or base.find("a", href=re.compile(r"linkedin\.com/jobs/view"))
            if not link:
                continue

            url = link.get("href", "").split("?")[0]
            match = re.search(r"/view/(\d+)", url)
            external_id = match.group(1) if match else url

            title_el = base.find("h3", class_=re.compile("base-search-card__title")) or base.find("span", class_=re.compile("sr-only"))
            title = title_el.get_text(strip=True) if title_el else ""

            company_el = base.find("h4", class_=re.compile("base-search-card__subtitle")) or base.find("a", class_=re.compile("hidden-nested-link"))
            company = company_el.get_text(strip=True) if company_el else ""

            location_el = base.find("span", class_=re.compile("job-search-card__location"))
            location = location_el.get_text(strip=True) if location_el else ""

            date_el = base.find("time")
            posted = date_el.get("datetime", "") if date_el else ""

            salary_el = base.find("span", class_=re.compile("job-search-card__salary"))
            salary = salary_el.get_text(strip=True) if salary_el else ""

            if title and external_id:
                jobs.append({
                    "source": "linkedin",
                    "external_id": str(external_id),
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": "",
                    "url": url,
                    "salary": salary,
                    "posted_date": posted,
                })

        except Exception as e:
            logger.warning(f"Failed to parse LinkedIn card: {e}")
            continue

    return jobs


def _fetch_linkedin_detail(job_url, session):
    """Fetch a LinkedIn job detail page and extract description + salary."""
    try:
        resp = session.get(job_url, headers=SESSION_HEADERS, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to fetch LinkedIn detail {job_url}: {e}")
        return "", ""

    soup = BeautifulSoup(resp.text, "lxml")

    # Extract full description
    desc_el = soup.find("div", class_="show-more-less-html__markup")
    description = desc_el.get_text("\n", strip=True) if desc_el else ""

    # Try to extract salary from the description text using common patterns
    salary = ""
    if description:
        salary_pattern = re.search(
            r'\$[\d,]+(?:\.[\d]+)?(?:k)?\s*[-–]\s*\$[\d,]+(?:\.[\d]+)?(?:k)?'
            r'(?:\s*(?:\+\s*super(?:annuation)?|per\s+(?:annum|year)|p\.?a\.?|pa|base))?',
            description, re.IGNORECASE
        )
        if salary_pattern:
            salary = salary_pattern.group(0)

    return description, salary


def scrape_linkedin():
    """Scrape LinkedIn guest job search API."""
    all_jobs = []
    errors = []
    session = requests.Session()

    for search in config.LINKEDIN_SEARCHES:
        keywords = search["keywords"]
        location = search["location"]

        for page in range(config.LINKEDIN_MAX_PAGES):
            try:
                params = {
                    "keywords": keywords,
                    "location": location,
                    "start": page * config.LINKEDIN_PAGE_SIZE,
                    "f_TPR": "r604800",  # past week
                    "f_WT": "2,3",  # remote + hybrid
                }
                url = f"{LINKEDIN_GUEST_API}?{urlencode(params)}"
                logger.info(f"Fetching LinkedIn page {page + 1}: {keywords} in {location}")
                resp = _get(url, session)

                jobs = _parse_linkedin_cards(resp.text)
                if not jobs:
                    logger.info(f"No more LinkedIn results at page {page + 1}")
                    break

                logger.info(f"Found {len(jobs)} jobs on page {page + 1}")
                all_jobs.extend(jobs)
                _polite_delay()

            except Exception as e:
                msg = f"LinkedIn error ({keywords}, page {page + 1}): {e}"
                logger.error(msg)
                errors.append(msg)
                break

    # Fetch full detail pages for each job
    logger.info(f"Fetching detail pages for {len(all_jobs)} LinkedIn jobs")
    for job in all_jobs:
        detail_url = job["url"]
        if not detail_url:
            continue
        description, detail_salary = _fetch_linkedin_detail(detail_url, session)
        if description:
            job["description"] = description
        if detail_salary and not job.get("salary"):
            job["salary"] = detail_salary
        _polite_delay()

    return all_jobs, errors


# --- Orchestrator ---

def fetch_all_jobs():
    """Run all scrapers and store results in the database."""
    conn = database.get_db()
    results = []

    for scrape_fn, source_name in [(scrape_seek, "seek"), (scrape_linkedin, "linkedin")]:
        jobs, errors = scrape_fn()

        new_count = 0
        for job in jobs:
            job_id = database.insert_job(
                conn,
                source=job["source"],
                external_id=job["external_id"],
                title=job["title"],
                company=job.get("company", ""),
                location=job.get("location", ""),
                description=job.get("description", ""),
                url=job.get("url", ""),
                salary=job.get("salary", ""),
                posted_date=job.get("posted_date", ""),
            )
            if job_id is not None:
                new_count += 1

        database.log_fetch(
            conn,
            source=source_name,
            jobs_found=len(jobs),
            new_jobs=new_count,
            errors="; ".join(errors) if errors else None,
        )
        results.append({
            "source": source_name,
            "found": len(jobs),
            "new": new_count,
            "errors": errors,
        })
        logger.info(f"{source_name}: found {len(jobs)}, new {new_count}")

    conn.close()
    return results
