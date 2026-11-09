import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

SEARCH_URL = os.environ["HIRINGCAFE_URL"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]

STATE_FILE = Path("seen_jobs.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131 Safari/537.36"
    )
}


def load_seen():
    if not STATE_FILE.exists():
        return set()

    try:
        data = json.loads(STATE_FILE.read_text())
        return set(data)
    except Exception:
        return set()


def save_seen(seen):
    STATE_FILE.write_text(
        json.dumps(sorted(seen), indent=2)
    )


def get_page():
    response = requests.get(
        SEARCH_URL,
        headers=HEADERS,
        timeout=30
    )
    response.raise_for_status()
    return response.text


def extract_jobs(html):
    jobs = {}

    # Find HiringCafe job links in the rendered/search HTML.
    patterns = [
        r'href=["\']([^"\']*/job/[^"\']+)["\']',
        r'href=["\']([^"\']*/jobs/[^"\']+)["\']'
    ]

    links = []

    for pattern in patterns:
        links.extend(
            re.findall(pattern, html, flags=re.IGNORECASE)
        )

    for link in links:
        full_url = urljoin(
            "https://hiring.cafe",
            link
        )

        # Remove query fragments so the same job has one ID.
        job_id = full_url.split("?")[0].split("#")[0]

        if "hiring.cafe" not in full_url:
            continue

        jobs[job_id] = {
            "id": job_id,
            "url": full_url
        }

    return jobs


def send_notification(job):
    endpoint = f"https://ntfy.sh/{NTFY_TOPIC}"

    headers = {
        "Title": "New UXR job on HiringCafe",
        "Priority": "high",
        "Tags": "mag,briefcase",
        "Click": job["url"]
    }

    message = (
        "A new job appeared in your saved "
        "HiringCafe UXR search.\n\n"
        "Tap to open it."
    )

    response = requests.post(
        endpoint,
        data=message.encode("utf-8"),
        headers=headers,
        timeout=30
    )

    response.raise_for_status()


def main():
    print("Checking HiringCafe...")

    html = get_page()
    jobs = extract_jobs(html)

    print(f"Found {len(jobs)} job links.")

    if not jobs:
        print(
            "WARNING: No job links were detected. "
            "Not updating state."
        )
        sys.exit(1)

    current_ids = set(jobs.keys())
    seen = load_seen()

    # First successful run establishes the baseline.
    if not seen:
        print(
            f"First run: saving {len(current_ids)} jobs "
            "as the baseline."
        )

        save_seen(current_ids)
        return

    new_ids = current_ids - seen

    print(f"New jobs: {len(new_ids)}")

    for job_id in new_ids:
        print(f"NEW: {job_id}")
        send_notification(jobs[job_id])

    # Keep both current + previously seen jobs so an older
    # posting disappearing/reappearing doesn't repeatedly alert.
    save_seen(seen | current_ids)


if __name__ == "__main__":
    main()
