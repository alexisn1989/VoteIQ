#!/usr/bin/env python3
"""Fetch and parse a Virginia LIS bill summary page."""

import argparse
import json
import re
from urllib.parse import urljoin

import requests


LIS_SUMMARY_URL = "https://lis.virginia.gov/cgi-bin/legp604.exe?{session}+sum+{bill_id}"
LIS_FULL_TEXT_URL = "https://lis.virginia.gov/cgi-bin/legp604.exe?{session}+ful+{bill_id}"
LIS_CURRENT_URL = "https://lis.virginia.gov/bill-details/{session}/{bill_id}"


def _soup_for(html: str):
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: beautifulsoup4. Run `pip install -r requirements.txt` "
            "or `pip install beautifulsoup4`."
        ) from exc

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup


def _clean_text(value: str | None) -> str | None:
    if not value:
        return None
    text = value.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip() or None


def _first_selector_text(soup, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            text = _clean_text(node.get_text("\n"))
            if text:
                return text
    return None


def _fetch_soup(url: str):
    response = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "VoteIQ/1.0 (+https://lis.virginia.gov)"},
    )
    response.raise_for_status()
    return _soup_for(response.text)


def _section(raw_text: str, labels: tuple[str, ...], stop_labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in labels)
    stop_pattern = "|".join(re.escape(label) for label in stop_labels)
    match = re.search(
        rf"(?is)(?:^|\n)\s*(?:{label_pattern})\s*:?\s*(.*?)(?=\n\s*(?:{stop_pattern})\s*:?\s|\Z)",
        raw_text,
    )
    return _clean_text(match.group(1)) if match else None


def _bill_body(raw_text: str) -> str | None:
    starts = (
        "A BILL",
        "HOUSE BILL",
        "SENATE BILL",
        "HOUSE JOINT RESOLUTION",
        "SENATE JOINT RESOLUTION",
        "HOUSE RESOLUTION",
        "SENATE RESOLUTION",
    )
    positions = [raw_text.find(start) for start in starts if raw_text.find(start) >= 0]
    if not positions:
        return None
    return _clean_text(raw_text[min(positions) :])


def get_bill_text(bill_id: str, session: str = "261") -> dict:
    """Return parsed summary, bill text, and fiscal links for a LIS bill page."""
    bill_id = bill_id.strip().upper()
    current_session = session if len(session) == 5 else f"20{session}"
    url = LIS_SUMMARY_URL.format(session=session, bill_id=bill_id)
    current_url = LIS_CURRENT_URL.format(session=current_session, bill_id=bill_id)
    soup = _fetch_soup(url)
    raw_text = _clean_text(soup.get_text("\n")) or ""
    unavailable = "document you requested does not exist or is not available" in raw_text.lower()

    title = _clean_text(soup.title.get_text(" ")) if soup.title else None
    summary = _first_selector_text(
        soup,
        (
            "div.summary",
            ".summary",
            "#summary",
            "section.summary",
        ),
    ) or _section(
        raw_text,
        ("Summary", "Summary as introduced", "Summary as passed"),
        ("Full text", "Patrons", "History", "Fiscal impact", "Impact statement"),
    )

    pre_text = None
    pre = soup.find("pre")
    if pre:
        pre_text = _clean_text(pre.get_text("\n"))

    text = _section(
        raw_text,
        ("Full text", "Text"),
        ("History", "Patrons", "Fiscal impact", "Impact statement"),
    )
    full_text_url = LIS_FULL_TEXT_URL.format(session=session, bill_id=bill_id)
    if not unavailable:
        full_soup = _fetch_soup(full_text_url)
        full_raw_text = _clean_text(full_soup.get_text("\n")) or ""
        full_pre = full_soup.find("pre")
        text = (
            pre_text
            or (_clean_text(full_pre.get_text("\n")) if full_pre else None)
            or _bill_body(full_raw_text)
            or text
        )

    fiscal = _first_selector_text(
        soup,
        (
            "div.fiscal",
            ".fiscal",
            "#fiscal",
            "section.fiscal",
        ),
    ) or _section(
        raw_text,
        ("Fiscal impact", "Impact statement", "Fiscal"),
        ("History", "Patrons", "Full text", "Summary"),
    )

    fiscal_links = []
    for link in soup.find_all("a", href=True):
        label = _clean_text(link.get_text(" ")) or ""
        href = link["href"]
        if "fiscal" in label.lower() or "impact" in label.lower() or "fiscal" in href.lower():
            fiscal_links.append({"label": label, "url": urljoin(url, href)})

    return {
        "bill_id": bill_id,
        "session": session,
        "url": url,
        "full_text_url": full_text_url,
        "current_url": current_url,
        "available": not unavailable,
        "error": (
            "LIS says the legacy CGI document does not exist or is not available. "
            "For current sessions, try the current_url or OpenStates feed."
            if unavailable
            else None
        ),
        "title": title,
        "summary": summary,
        "text": text,
        "fiscal": fiscal,
        "fiscal_links": fiscal_links,
        "raw_text": raw_text,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a Virginia LIS bill summary page.")
    parser.add_argument("bill_id", nargs="?", default="SB583", help="Bill number, e.g. SB583")
    parser.add_argument("--session", default="261", help="LIS page session code, e.g. 261")
    args = parser.parse_args()

    print(json.dumps(get_bill_text(args.bill_id, args.session), indent=2))


if __name__ == "__main__":
    main()
