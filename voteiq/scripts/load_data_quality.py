#!/usr/bin/env python3
"""
Load Data Quality Fixes - Processes all representatives
Runs: Source conflict detection, bias detection, transparency manifest generation
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.data_quality_service import DataQualityService
from datetime import datetime


def main():
    """Main execution function"""
    print("=" * 80)
    print("VoteIQ Data Quality Fixes - Batch Processing")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # Initialize service
    service = DataQualityService()

    if not service.connection:
        print("✗ Failed to connect to database. Check configuration.")
        return False

    # Representatives to process
    representatives = [
        (1, 'Jen Kiggans', 2026),
        (2, 'Jennifer Rouse', 2026),
        (3, 'Virginia Feggans', 2026)
    ]

    overall_success = True

    for rep_id, rep_name, session_year in representatives:
        print(f"\n{'-' * 80}")
        print(f"Processing: {rep_name} (ID: {rep_id}, Session: {session_year})")
        print(f"{'-' * 80}")

        # FIX #1: Source Conflict Detection
        print("\n[FIX #1] Detecting Source Conflicts...")
        conflicts = service.detect_source_conflicts(rep_id, session_year)
        print(f"  ✓ Conflicts found: {conflicts['conflicts_found']}")
        if conflicts['conflicts_found'] > 0:
            print(f"    - High: {conflicts['severity_breakdown']['high']}")
            print(f"    - Medium: {conflicts['severity_breakdown']['medium']}")
            print(f"    - Low: {conflicts['severity_breakdown']['low']}")
            for conflict in conflicts['conflicts']:
                print(f"    • {conflict['bill_id']}: {conflict['conflict_type']} ({conflict['severity']})")

        # FIX #6: Bias Detection
        print("\n[FIX #6] Detecting Statistical Biases...")
        biases = service.detect_biases(rep_id, session_year)
        print(f"  ✓ Biases detected: {biases['total_biases']}")
        for bias_type, bias_data in biases['details'].items():
            status = "✓" if not bias_data['detected'] else "⚠️"
            severity = bias_data['severity'].upper() if bias_data['detected'] else "N/A"
            print(f"    {status} {bias_type}: {severity}")
            if bias_data['detected']:
                print(f"       Mechanism: {bias_data['mechanism']}")
                print(f"       Recommendation: {bias_data['recommendation']}")

        print(f"  ✓ Reliability Score: {biases['reliability_score']}/100")

        # FIX #4: Transparency Manifest
        print("\n[FIX #4] Generating Transparency Manifest...")
        manifest = service.generate_transparency_manifest(rep_id, session_year)
        print(f"  ✓ Data Sources: {', '.join(manifest['data_sources'])}")
        print(f"  ✓ Freshness: {manifest['source_freshness']}")
        print(f"  ✓ Completeness: {manifest['completeness_percent']:.1f}%")
        print(f"  ✓ Quality Score: {manifest['quality_score']}/100")
        print(f"  ✓ Data Gaps: {manifest['data_gaps']}")
        print(f"  ✓ Recommendation: {manifest['recommendation']}")

    print(f"\n{'=' * 80}")
    print("✓ Batch processing complete")
    print(f"{'=' * 80}")

    service.disconnect()
    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
