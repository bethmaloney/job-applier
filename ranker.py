import json
import logging
import os
import subprocess

import config
import database

logger = logging.getLogger(__name__)

RANKING_PROMPT_TEMPLATE = """\
You are a job relevance ranker. Score this job listing against the candidate's profile.

## Candidate Profile
- **Location**: {candidate_location}
- **Skills**: {skills}
- **Target titles**: {target_titles}
- **Preferences**: {preferences}
- **Min salary**: {min_salary}
- **Resume/Experience**: {resume_text}

## Job Listing
- **Title**: {job_title}
- **Company**: {company}
- **Location**: {location}
- **Salary**: {salary}
- **Description**: {description}

## Instructions
Rate relevance from 1-10 where:
- 1-3: Poor match (wrong field, wrong level, bad location)
- 4-5: Weak match (some overlap but missing key requirements)
- 6-7: Decent match (good overlap, worth reviewing)
- 8-9: Strong match (closely aligned with profile)
- 10: Perfect match

Respond with ONLY valid JSON, no other text:
{{"score": <number 1-10>, "explanation": "<one sentence>"}}
"""


def _call_claude(prompt):
    """Call claude CLI in headless mode and return the response text."""
    try:
        # Remove CLAUDECODE env var so claude CLI doesn't think it's nested
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            [config.CLAUDE_CLI_PATH, "-p", "--model", config.CLAUDE_MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode != 0:
            logger.error(f"Claude CLI error (rc={result.returncode}): {result.stderr}")
            return None
        return result.stdout.strip()

    except FileNotFoundError:
        logger.error("Claude CLI not found. Install it or update CLAUDE_CLI_PATH in config.")
        return None
    except subprocess.TimeoutExpired:
        logger.error("Claude CLI timed out")
        return None
    except Exception as e:
        logger.error(f"Claude CLI exception: {e}")
        return None


def _parse_score(response_text):
    """Extract score and explanation from Claude's JSON response."""
    if not response_text:
        return None, None

    # Try to find JSON in the response (Claude might wrap it in markdown)
    json_match = None
    # Try the whole response first
    try:
        data = json.loads(response_text)
        json_match = data
    except json.JSONDecodeError:
        pass

    # Try to find JSON block in response
    if not json_match:
        import re
        patterns = [
            r'```json\s*(\{.*?\})\s*```',
            r'```\s*(\{.*?\})\s*```',
            r'(\{[^{}]*"score"[^{}]*\})',
        ]
        for pattern in patterns:
            match = re.search(pattern, response_text, re.DOTALL)
            if match:
                try:
                    json_match = json.loads(match.group(1))
                    break
                except json.JSONDecodeError:
                    continue

    if not json_match:
        logger.warning(f"Could not parse JSON from response: {response_text[:200]}")
        return None, None

    score = json_match.get("score")
    explanation = json_match.get("explanation", "")

    if score is not None:
        try:
            score = float(score)
            score = max(1.0, min(10.0, score))
        except (ValueError, TypeError):
            return None, None

    return score, explanation


def rank_job(job, profile):
    """Rank a single job against the user profile."""
    prompt = RANKING_PROMPT_TEMPLATE.format(
        candidate_location=profile.get("location") or "Not specified",
        skills=profile.get("skills") or "Not specified",
        target_titles=profile.get("target_titles") or "Not specified",
        preferences=profile.get("preferences") or "Not specified",
        min_salary=profile.get("min_salary") or "Not specified",
        resume_text=profile.get("resume_text") or "Not provided",
        job_title=job["title"],
        company=job["company"] or "Unknown",
        location=job["location"] or "Unknown",
        salary=job["salary"] or "Not listed",
        description=job["description"] or "No description available",
    )

    response = _call_claude(prompt)
    return _parse_score(response)


def rank_new_jobs():
    """Rank all unranked jobs. Returns count of jobs ranked."""
    conn = database.get_db()
    profile = database.get_profile(conn)

    if not profile:
        logger.warning("No user profile set — skipping ranking")
        conn.close()
        return 0

    unranked = database.get_unranked_jobs(conn)
    logger.info(f"Ranking {len(unranked)} unranked jobs")

    ranked_count = 0
    for job in unranked:
        job_dict = dict(job)
        score, explanation = rank_job(job_dict, profile)

        if score is not None:
            database.update_relevance(conn, job_dict["id"], score, explanation)
            ranked_count += 1
            logger.info(f"Ranked job {job_dict['id']} ({job_dict['title']}): {score}")
        else:
            logger.warning(f"Failed to rank job {job_dict['id']} ({job_dict['title']})")

    conn.close()
    return ranked_count
