#!/usr/bin/env python3
"""
Test the /api/structured-extractor endpoint logic directly
"""
import sys
import json
sys.path.insert(0, '/c/Users/Alexis/OneDrive/Desktop/Vriginia_api_election')

from voteiq.api.routes.chat import (
    StructuredExtractorRequest,
    StructuredExtractorResponse,
    EXTRACTION_SCHEMAS,
    STRUCTURED_EXTRACTOR_SYSTEM_PROMPT,
    ConfidenceSummary,
    ExtractionValidation,
)
import os
os.environ['ANTHROPIC_API_KEY'] = os.getenv('ANTHROPIC_API_KEY', 'dummy')

from anthropic import Anthropic

# Test 1: Vote Extraction
print("=" * 80)
print("TEST 1: Vote Extraction from Meeting Minutes")
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

vote_request = StructuredExtractorRequest(
    raw_text=vote_text,
    extraction_type="vote",
    source_url="https://richmond.legistar.com/council-meetings/2026-05-15",
    source_label="Richmond City Council Meeting Minutes"
)

print(f"\nInput text:\n{vote_text}\n")
print(f"Request: extraction_type={vote_request.extraction_type}")
print(f"Expected: Extract 5 vote records with proper normalization\n")

# Test 2: Donation Extraction
print("=" * 80)
print("TEST 2: Donation Extraction from News Article")
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

donation_request = StructuredExtractorRequest(
    raw_text=donation_text,
    extraction_type="donation",
    source_url="https://virginiamercury.com/2026/05/18/tech-money",
    source_label="Virginia Mercury News Article"
)

print(f"\nInput text:\n{donation_text}\n")
print(f"Request: extraction_type={donation_request.extraction_type}")
print(f"Expected: Extract 3 donation records with proper date/amount normalization\n")

# Test 3: Check schemas
print("=" * 80)
print("TEST 3: Vote Schema Structure")
print("=" * 80)

vote_schema = EXTRACTION_SCHEMAS.get("vote")
print(json.dumps(vote_schema, indent=2)[:500] + "...")

print("\n" + "=" * 80)
print("TEST 4: Donation Schema Structure")
print("=" * 80)

donation_schema = EXTRACTION_SCHEMAS.get("donation")
print(json.dumps(donation_schema, indent=2)[:500] + "...")

print("\n" + "=" * 80)
print("READY TO TEST WITH CLAUDE")
print("=" * 80)
print("\nNext: Call Claude API with:")
print(f"  - Model: claude-sonnet-4-6")
print(f"  - Vote extraction on {len(vote_text)} chars of meeting minutes")
print(f"  - Donation extraction on {len(donation_text)} chars of news article")
print(f"\nExpected outputs:")
print(f"  - Vote: 5 records (Johnson YES, Williams NO, Chen ABSTAIN, Martinez YES, Davis YES)")
print(f"  - Donation: 3 records (Mitchell $50K contribution, Tech Leaders $100K contribution, Davis $15K in-kind)")
print(f"\nNote: Set environment variable ANTHROPIC_API_KEY before running")
