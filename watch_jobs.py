import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

MCP_URL = "https://hiringcafe.com/api/mcp"
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
STATE_FILE = Path("seen_jobs.json")

# Quoting matters. Our testing showed that quoted searches are
# dramatically narrower than unquoted searches.
SEARCHES = [
    '"UX Researcher"',
    '"User Researcher"',
    '"User Experience Researcher"',
    '"Experience Researcher"',
    '"XD Researcher"',
    '"Usability Researcher"',
    '"Design Researcher"',
    '"Product Researcher"',
    '"Human Factors Researcher"',
    '"Principal Researcher"',
    '"Lead Researcher"',
    '"Staff Researcher"',
]

# Strong research-title signals.
KEEP_TITLE_PATTERNS = [
    r"\bux researcher\b",
    r"\bux research\b",
    r"\buser researcher\b",
    r"\buser research\b",
    r"\buser experience researcher\b",
    r"\buser experience research\b",
    r"\bexperience researcher\b",
    r"\bxd researcher\b",
    r"\busability researcher\b",
    r"\bdesign researcher\b",
    r"\bproduct researcher\b",
    r"\bproduct design researcher\b",
    r"\bhuman factors researcher\b",
    r"\bprincipal researcher\b",
    r"\blead researcher\b",
    r"\bstaff researcher\b",
]

# Titles that we never want notifications for.
REJECT_TITLE_PATTERNS = [
    r"\bintern\b",
    r"\binternship\b",
    r"\bpost[\s-]?doc",
    r"\bprofessor\b",
    r"\bstudent\b",
    r"\bteaching assistant\b",
    r"\bresearch scientist\b",
    r"\bchemist\b",
    r"\beconomist\b",
    r"\bsecurity researcher\b",
    r"\brobotics researcher\b",
    r"\bneuroscience researcher\b",
    r"\bsensory scientist\b",
    r"\bresearch fellow\b",
    r"\bprogram manager\b",
    r"\bmember of technical staff\b",
]

# For ambiguous senior researcher titles, require product/UX context.
CONTEXT_SIGNALS = [
    "ux",
    "user",
    "usability",
    "experience",
    "human-centered",
    "human centered",
    "design research",
    "product research",
    "customer research",
    "consumer research",
    "discovery",
    "qualitative",
    "mixed methods",
    "product strategy",
]


def call_mcp(tool_name, arguments, request_id):
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    req = urllib.request.Request(
        MCP_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))

    except urllib.error.HTTPError as exc:
        # Respect HiringCafe rate limiting.
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After", "unknown")
            print(
                f"HiringCafe rate limit reached. "
                f"Retry-After: {retry_after}"
            )
        raise


def extract_jobs(result):
    meta = (
        result
        .get("result", {})
        .get("_meta", {})
        .get("hiringcafe.com/job-cards", {})
    )

    return meta.get("jobs", [])


def search_jobs(term, request_id):
    result = call_mcp(
        "search_jobs",
        {
            "search": term,
            "location": "United States",
            "posted_within_days": 1,
            "limit": 25,
        },
        request_id,
    )

    return extract_jobs(result)


def load_seen():
    if not STATE_FILE.exists():
        return set()

    try:
        return set(json.loads(STATE_FILE.read_text()))
    except Exception:
        return set()


def save_seen(ids):
    STATE_FILE.write_text(
        json.dumps(sorted(ids), indent=2)
    )


def title_is_relevant(title):
    title_lower = title.lower()

    for pattern in REJECT_TITLE_PATTERNS:
        if re.search(pattern, title_lower):
            return False

    for pattern in KEEP_TITLE_PATTERNS:
        if re.search(pattern, title_lower):
            return True

    return False


def remote_label(job):
    meta = " | ".join(job.get("meta", []))
    meta_lower = meta.lower()

    if "remote" in meta_lower:
        return "Confirmed remote"

    if "hybrid" in meta_lower:
        return "Remote eligibility unclear (listed Hybrid)"

    if "onsite" in meta_lower:
        return "Remote eligibility unclear (listed Onsite)"

    return "Remote eligibility unclear"


def send_notification(job):
    title = job.get("title", "New research job")
    company = job.get("company", "Unknown company")
    url = job.get("url", "https://hiringcafe.com")
    details = job.get("meta", [])
    status = remote_label(job)

    message_lines = [
        company,
        status,
    ]

    message_lines.extend(details)

    message = "\n".join(message_lines)

    endpoint = f"https://ntfy.sh/{NTFY_TOPIC}"

    headers = {
        "Title": title,
        "Priority": "high",
        "Tags": "mag,briefcase",
        "Click": url,
    }

    req = urllib.request.Request(
        endpoint,
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        response.read()


def main():
    all_jobs = {}

    for index, term in enumerate(SEARCHES, 1):
        print(f"Searching: {term}")

        try:
            jobs = search_jobs(term, index)
            print(f"Returned {len(jobs)} jobs")

            for job in jobs:
                job_id = job.get("id")
                title = job.get("title", "")

                if not job_id:
                    continue

                if not title_is_relevant(title):
                    continue

                all_jobs[job_id] = job

        except Exception as exc:
            print(f"Search failed for {term}: {exc}")

        # Small courtesy pause between connector calls.
        time.sleep(1)

    print()
    print(f"Relevant unique jobs found: {len(all_jobs)}")

    if not all_jobs:
        print(
            "No relevant jobs found. "
            "State will not be changed."
        )
        return

    current_ids = set(all_jobs.keys())
    seen_ids = load_seen()

    # First successful run creates the baseline.
    if not seen_ids:
        print(
            f"First run: establishing baseline with "
            f"{len(current_ids)} jobs."
        )

        save_seen(current_ids)
        return

    new_ids = current_ids - seen_ids

    print(f"New jobs since previous runs: {len(new_ids)}")

    for job_id in new_ids:
        job = all_jobs[job_id]

        print(
            f"NEW: {job.get('title')} - "
            f"{job.get('company')}"
        )

        send_notification(job)

    # Remember everything ever seen.
    save_seen(seen_ids | current_ids)


if __name__ == "__main__":
    main()
