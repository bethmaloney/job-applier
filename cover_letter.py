import logging
import os
import subprocess

import config

logger = logging.getLogger(__name__)

DEFAULT_COVER_LETTER_INSTRUCTIONS = (
    "Write like a real engineer, not a marketing brochure. "
    "Direct and confident but not robotic — normal human warmth is fine. "
    "Avoid over-the-top enthusiasm like 'genuinely excited', 'passionate about', "
    "'thrilled by the opportunity', 'resonates with me', 'incredible opportunity'. "
    "Saying you're drawn to something or excited about something specific is fine — "
    "just don't lay it on thick."
)

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
- Write 3-4 paragraphs, under 300 words total
- Do NOT include any header, address block, date, "Dear Hiring Manager", or sign-off — just the body paragraphs
- Write in first person
- Read the job description carefully and call out specifics from it — mention their tech stack, specific challenges they describe, team structure, compliance goals, or anything that shows you actually read the ad rather than sending a generic letter
- Match 2-3 candidate experiences to specific job requirements. Go deep on the overlap rather than listing everything.
- Vary paragraph structure — don't start every paragraph with "I" or follow the same pattern
- Prefer short, direct sentences. Cut filler words and corporate fluff.
- Output ONLY the cover letter text, no commentary or labels
- Tone and style: {instructions}
"""


def _call_claude(prompt, timeout=60):
    """Call claude CLI in headless mode and return the response text."""
    try:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        result = subprocess.run(
            [config.CLAUDE_CLI_PATH, "-p", "--model", config.CLAUDE_COVER_LETTER_MODEL],
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


def generate(job, profile):
    """Generate a cover letter for a job using the candidate profile."""
    instructions = (
        profile.get("cover_letter_instructions") or DEFAULT_COVER_LETTER_INSTRUCTIONS
    )

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
        instructions=instructions,
    )

    return _call_claude(prompt)
