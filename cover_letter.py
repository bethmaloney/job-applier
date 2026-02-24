import logging
import os
import subprocess

import config

logger = logging.getLogger(__name__)

TONE_MAP = {
    "professional": "formal and professional, confident but not arrogant",
    "friendly": "warm and personable, conversational yet polished",
    "enthusiastic": "energetic and passionate, showing genuine excitement",
    "concise": "direct and to-the-point, no fluff, every sentence earns its place",
}

COVER_LETTER_PROMPT_TEMPLATE = """\
Write a cover letter body for the following job application.

## Candidate Profile
- **Skills**: {skills}
- **Target titles**: {target_titles}
- **Preferences**: {preferences}
- **Location**: {candidate_location}
- **Resume/Experience**: {resume_text}

## Job Details
- **Title**: {job_title}
- **Company**: {company}
- **Location**: {location}
- **Salary**: {salary}
- **Description**: {description}

## Instructions
- Write 3-4 paragraphs, under 300 words
- Tone: {tone_description}
- Do NOT include any header, address block, date, "Dear Hiring Manager", or sign-off — just the body paragraphs
- Write in first person
- Highlight 2-3 skills or experiences from the candidate profile that match the job requirements
- Be specific about why this role and company are a good fit
- Output ONLY the cover letter text, no commentary or labels
"""


def _call_claude(prompt, timeout=60):
    """Call claude CLI in headless mode and return the response text."""
    try:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            [config.CLAUDE_CLI_PATH, "-p", "--model", config.CLAUDE_MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
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


def generate(job, profile, tone="professional"):
    """Generate a cover letter for a job using the candidate profile."""
    tone_description = TONE_MAP.get(tone, TONE_MAP["professional"])

    prompt = COVER_LETTER_PROMPT_TEMPLATE.format(
        skills=profile.get("skills") or "Not specified",
        target_titles=profile.get("target_titles") or "Not specified",
        preferences=profile.get("preferences") or "Not specified",
        candidate_location=profile.get("location") or "Not specified",
        resume_text=profile.get("resume_text") or "Not provided",
        job_title=job["title"],
        company=job["company"] or "Unknown",
        location=job["location"] or "Unknown",
        salary=job["salary"] or "Not listed",
        description=job["description"] or "No description available",
        tone_description=tone_description,
    )

    return _call_claude(prompt)
