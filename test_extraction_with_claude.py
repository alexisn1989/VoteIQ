#!/usr/bin/env python3
"""
Test /api/structured-extractor with actual Claude API calls
"""
import sys
import json
import os

sys.path.insert(0, '/c/Users/Alexis/OneDrive/Desktop/Vriginia_api_election')

# Load environment variables manually
env_file = 'c:\\Users\\Alexis\\OneDrive\\Desktop\\Vriginia_api_election\\.env'
with open(env_file) as f:
    for line in f:
        if '=' in line and not line.startswith('#'):
            key, val = line.strip().split('=', 1)
            os.environ[key] = val

from voteiq.api.routes.chat import (
    StructuredExtractorRequest,
    EXTRACTION_SCHEMAS,
    STRUCTURED_EXTRACTOR_SYSTEM_PROMPT,
    ConfidenceSummary,
    ExtractionValidation,
)
from anthropic import Anthropic

api_key = os.environ.get('ANTHROPIC_API_KEY')
client = Anthropic(api_key=api_key)

# Test 1: Vote Extraction
print("\n" + "=" * 80)
print("TEST 1: VOTE EXTRACTION")
print("=" * 80)

vote_text = """Richmond City Council Meeting - May 15, 2026

Present: Councilmember Johnson, Williams, Chen, Martinez, and Davis

HB 456 - School Funding Bill

Mayor: "The motion is on HB 456. All in favor?"

Councilmember Johnson: "YES"
Councilmember Williams: "NO"
Councilmember Chen: "I abstain due to conflict of interest"
Councilmember Martinez: "YES"
Councilmember Davis: "YES"

Result: 4 Yes, 1 No, 1 Abstain. Motion passed on May 15, 2026 at 2:30 PM.
"""

extraction_prompt_vote = f"""Extract structured vote data from this text.

Schema (required fields marked with *):
{json.dumps(EXTRACTION_SCHEMAS["vote"], indent=2)}

Text:
{vote_text}

Emit only JSON (object or array of objects). No prose."""

print("\nCalling Claude Sonnet with:")
print(f"  - Extraction type: vote")
print(f"  - Text length: {len(vote_text)} chars")
print(f"  - Expected records: 5 votes (Johnson YES, Williams NO, Chen ABSTAIN, Martinez YES, Davis YES)")

try:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[
            {"role": "user", "content": extraction_prompt_vote}
        ],
        system=STRUCTURED_EXTRACTOR_SYSTEM_PROMPT,
        cache_control={"type": "ephemeral", "ttl": "5m"}
    )

    response_text = response.content[0].text.strip()

    # Parse JSON
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]

    records = json.loads(response_text)

    # Normalize to list
    is_single = not isinstance(records, list)
    records_list = [records] if is_single else records

    print(f"\n[OK] Success! Extracted {len(records_list)} vote records:")
    for i, record in enumerate(records_list, 1):
        print(f"  {i}. {record.get('official_name', 'Unknown')} voted {record.get('vote', 'unknown').upper()} on {record.get('bill_id', 'unknown')}")
        print(f"     Date: {record.get('vote_date')}, Confidence: {record.get('_confidence', 'unknown')}")

    print(f"\nFull response:\n{json.dumps(records, indent=2)}")

except Exception as e:
    print(f"\n[ERROR] {e}")

# Test 2: Donation Extraction
print("\n" + "=" * 80)
print("TEST 2: DONATION EXTRACTION")
print("=" * 80)

donation_text = """Tech Industry Pours $2.5M into Virginia Politics

Richmond-based tech entrepreneur Sarah Mitchell donated $50,000 to Councilmember
Robert Chen's re-election campaign on May 18, 2026. The donation is listed in VPAP
records and marked as a contribution.

According to FEC filings, federal PAC "Tech Leaders for Progress" made a $100,000
contribution to Rep. Elaine Luria on May 15, 2026. The contribution was made via
wire transfer.

Local builder John Davis gave $15,000 in in-kind donations (building materials) to
Mayor Johnson's campaign starting May 1, 2026.
"""

extraction_prompt_donation = f"""Extract structured donation data from this text.

Schema (required fields marked with *):
{json.dumps(EXTRACTION_SCHEMAS["donation"], indent=2)}

Text:
{donation_text}

Emit only JSON (object or array of objects). No prose."""

print("\nCalling Claude Sonnet with:")
print(f"  - Extraction type: donation")
print(f"  - Text length: {len(donation_text)} chars")
print(f"  - Expected records: 3 donations (Mitchell $50K, Tech Leaders $100K, Davis $15K in-kind)")

try:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[
            {"role": "user", "content": extraction_prompt_donation}
        ],
        system=STRUCTURED_EXTRACTOR_SYSTEM_PROMPT,
        cache_control={"type": "ephemeral", "ttl": "5m"}
    )

    response_text = response.content[0].text.strip()

    # Parse JSON
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]

    records = json.loads(response_text)

    # Normalize to list
    is_single = not isinstance(records, list)
    records_list = [records] if is_single else records

    print(f"\n[OK] Success! Extracted {len(records_list)} donation records:")
    for i, record in enumerate(records_list, 1):
        amount = record.get('amount', 'unknown')
        dtype = record.get('donation_type', 'unknown')
        print(f"  {i}. {record.get('donor_name', 'Unknown')} donated ${amount if amount else '?'} ({dtype}) to {record.get('recipient_name', 'Unknown')}")
        print(f"     Date: {record.get('donation_date')}, Source: {record.get('source', 'unknown')}, Confidence: {record.get('_confidence', 'unknown')}")

    print(f"\nFull response:\n{json.dumps(records, indent=2)}")

except Exception as e:
    print(f"\n[ERROR] {e}")

print("\n" + "=" * 80)
print("TESTS COMPLETE")
print("=" * 80)
