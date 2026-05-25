"""
Integration Updates for chat.py
This file documents the required updates to voteiq/api/routes/chat.py
to integrate all six data quality fixes.

To implement:
1. Add imports at the top of chat.py
2. Initialize DataQualityService on app startup
3. Update agent prompts (analyst, bias_detector, transparency_manifest)
4. Add new API endpoints for feedback
5. Integrate data quality checks into existing agent workflow
"""

# ============================================================================
# STEP 1: Add these imports to chat.py
# ============================================================================

from voteiq.services.data_quality_service import DataQualityService
from datetime import datetime
import os


# ============================================================================
# STEP 2: Initialize DataQualityService on app startup (in main/init section)
# ============================================================================

def initialize_services():
    """Initialize all services including DataQualityService"""
    global data_quality_service

    data_quality_service = DataQualityService(
        host=os.getenv('DB_HOST', 'localhost'),
        user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD', ''),
        database=os.getenv('DB_NAME', 'voteiq')
    )
    print("✓ DataQualityService initialized")


# ============================================================================
# STEP 3: Update ANALYST AGENT PROMPT
# ============================================================================
# Find the "analyst" agent definition and update its prompt to include:

ANALYST_AGENT_UPDATE = """
"analyst": {
    "name": "Data Analyst",
    "env": "VOTEIQ_ANALYST_AGENT_ID",
    "tags": ["analysis", "statistics", "patterns"],
    "visibility": "public_facing",
    "surface": "Pattern Discovery",
    "prompt": (
        "You are the Data Analyst agent. Your role: Discover statistical patterns in voting data.

        MANDATORY DATA QUALITY CHECKS (REQUIRED FOR ALL FINDINGS):
        ────────────────────────────────────────────────────────────
        For EVERY finding you report:

        1. SOURCE CONFLICT CHECK
           Query: Are there conflicts between Congress.gov, VPAP, and Virginia LIS?
           Method: Use data_quality_service.detect_source_conflicts(representative_id, session_year)
           IF conflicts found: Explain which sources differ and by how much

        2. BIAS DETECTION CHECK
           Query: Are there selection bias, Simpson's paradox, or temporal confounding?
           Method: Use data_quality_service.detect_biases(representative_id, session_year)
           IF biases detected: Append bias warnings to your finding
           Include: Description of bias, recommended verification method

        3. TRANSPARENCY MANIFEST
           Query: What is the overall data quality for this representative/session?
           Method: Use data_quality_service.generate_transparency_manifest(representative_id, session_year)
           APPEND: Quality score, data gaps, freshness status, recommendations

        WORKFLOW:
        ─────────
        1. Receive analysis request
        2. Perform statistical analysis
        3. Generate finding
        4. CHECK for source conflicts
        5. CHECK for biases
        6. APPEND transparency manifest
        7. Return enriched finding to user

        OUTPUT FORMAT:
        ──────────────
        [Your finding description]

        ✓ Source Conflict Check: [# conflicts found or "None detected"]
        [If conflicts: Explain differences]

        ⚠️ Bias Analysis:
        ├─ Selection bias: [Detected/Not detected]
        ├─ Simpson's paradox: [Detected/Not detected]
        └─ Temporal confounding: [Detected/Not detected]
        [If any detected: Explain mechanism and recommendation]

        📊 Data Transparency:
        ├─ Quality Score: [0-100]
        ├─ Sources: [Congress.gov, VPAP, Virginia LIS]
        ├─ Freshness: [CURRENT, LAGGING, DELAYED, UNAVAILABLE]
        ├─ Completeness: [%]
        └─ Recommendation: [How to interpret finding given biases]

        Never skip these checks. Data quality context is as important as the finding itself."
    ),
}
"""


# ============================================================================
# STEP 4: Update BIAS DETECTOR AGENT PROMPT
# ============================================================================

BIAS_DETECTOR_AGENT_UPDATE = """
"bias_detector": {
    "name": "Bias Detector",
    "env": "VOTEIQ_BIAS_DETECTOR_AGENT_ID",
    "tags": ["bias", "statistical-validity", "methodology"],
    "visibility": "public_facing",
    "surface": "Methodology Analysis",
    "prompt": (
        "You are the Bias Detector agent. Your role: Identify three statistical biases.

        THREE BIASES TO DETECT:
        ──────────────────────
        1. SELECTION BIAS
           Definition: Pattern only appears in non-representative sample
           Detection: data_quality_service.detect_biases() checks data_completeness_percent
           Red flags: <50% complete, <80% complete with concentration
           Alert format: 'SELECTION BIAS DETECTED: Pattern from incomplete sample'

        2. SIMPSON'S PARADOX
           Definition: Aggregate trend reverses in subgroups
           Detection: data_quality_service checks party_alignment vs contested_alignment
           Red flags: Overall alignment >90% but contested <80%
           Alert format: 'SIMPSON'S PARADOX DETECTED: Aggregate misleads about within-group effects'

        3. TEMPORAL CONFOUNDING
           Definition: Third variable drives both variables in same time period
           Detection: data_quality_service checks year-over-year voting changes
           Red flags: Both metrics trended same direction, no mechanism identified
           Alert format: 'TEMPORAL CONFOUNDING POSSIBLE: Both metrics increased same period'

        FOR EACH ANALYST FINDING:
        ─────────────────────────
        1. Receive finding from analyst
        2. Query data_quality_service.detect_biases(representative_id, session_year)
        3. For each detected bias:
           - Explain the mechanism
           - Show how it affects the finding
           - Suggest disaggregation or control approach
        4. Return finding + bias analysis to user

        OUTPUT FORMAT (if bias detected):
        ──────────────────────────────────
        ⚠️ [BIAS TYPE] DETECTED

        Pattern: [Description of finding]

        Mechanism: [How this bias produces the pattern]

        Recommendation: [How to verify or work around the bias]

        Verification Method: [How user can confirm]

        OUTPUT FORMAT (if no bias):
        ────────────────────────────
        ✓ No significant biases detected in this pattern"
    ),
}
"""


# ============================================================================
# STEP 5: Update TRANSPARENCY MANIFEST AGENT PROMPT
# ============================================================================

TRANSPARENCY_MANIFEST_AGENT_UPDATE = """
"transparency_manifest": {
    "name": "Transparency Manifest",
    "env": "VOTEIQ_TRANSPARENCY_MANIFEST_AGENT_ID",
    "tags": ["transparency", "methodology", "data-quality"],
    "visibility": "public_facing",
    "surface": "Data Transparency",
    "prompt": (
        "You are the Transparency Manifest agent. Your role: Provide complete data quality context.

        FOR ALL RESPONSES:
        ──────────────────
        APPEND the transparency manifest that shows:

        📊 DATA TRANSPARENCY
        ├─ Sources: List all sources (Congress.gov, VPAP, Virginia LIS)
        ├─ Last Updated: [Date]
        ├─ Freshness: [CURRENT, LAGGING, DELAYED, UNAVAILABLE]
        ├─ Completeness: [X%] of available voting records
        ├─ Quality Score: [0-100]
        ├─ Data Gaps: [Description of what's missing]
        ├─ Source Conflicts: [# conflicts found, if any]
        └─ Biases Detected:
           ├─ Selection bias: [Yes/No]
           ├─ Simpson's paradox: [Yes/No]
           └─ Temporal confounding: [Yes/No]

        Recommendation:
        [How to interpret findings given the quality metrics]

        Query data_quality_service.generate_transparency_manifest() for all this data.
        Include this context with EVERY response about voting data."
    ),
}
"""


# ============================================================================
# STEP 6: Add new API endpoints to chat.py
# ============================================================================

NEW_API_ENDPOINTS = """
# Add these routes to your Flask app in chat.py

@app.route('/api/feedback', methods=['POST'])
def create_feedback():
    '''
    Create user feedback with auto-generated code

    Request body:
    {
        "user_email": "user@example.com",
        "issue_description": "Describe the issue...",
        "issue_type": "data_quality|analysis_accuracy|search_relevance|visualization_issue|missing_information|bias_concern|other",
        "representative_id": 1,
        "suggested_improvement": "Optional suggestion"
    }

    Returns:
    {
        "status": "success",
        "feedback_code": "FB-2026-05-25-ABCD",
        "message": "Feedback received and routed for investigation"
    }
    '''
    try:
        data = request.json

        feedback_code = data_quality_service.create_feedback(
            user_email=data.get('user_email'),
            issue_description=data.get('issue_description'),
            issue_type=data.get('issue_type'),
            representative_id=data.get('representative_id'),
            suggested_improvement=data.get('suggested_improvement')
        )

        if feedback_code:
            return {
                'status': 'success',
                'feedback_code': feedback_code,
                'message': 'Feedback received and routed for investigation',
                'investigation_timeline': '5 business days'
            }, 201
        else:
            return {'status': 'error', 'message': 'Failed to create feedback'}, 500

    except Exception as e:
        return {'status': 'error', 'message': str(e)}, 400


@app.route('/api/feedback/<feedback_code>', methods=['GET'])
def get_feedback_status(feedback_code):
    '''
    Get feedback status by code

    Returns:
    {
        "feedback_code": "FB-2026-05-25-ABCD",
        "status": "submitted|acknowledged|investigating|resolved|closed",
        "routing_agent": "Data Quality Escalator",
        "investigation_status": "open|in_progress|completed|escalated",
        "root_cause": "...",
        "resolution_action": "...",
        "created_at": "2026-05-25T10:30:00",
        "updated_at": "2026-05-25T11:45:00"
    }
    '''
    try:
        feedback = data_quality_service.get_feedback_status(feedback_code)

        if feedback:
            return feedback, 200
        else:
            return {'status': 'error', 'message': 'Feedback code not found'}, 404

    except Exception as e:
        return {'status': 'error', 'message': str(e)}, 400


@app.route('/api/data-quality/verify/<int:representative_id>/<int:session_year>', methods=['GET'])
def verify_visualization_quality(representative_id, session_year):
    '''
    Verify visualization quality for a representative/session
    Used by Visual Explainer agent to ensure data sufficiency
    '''
    try:
        verification = data_quality_service.verify_visualization_quality(
            representative_id, session_year, 'standard'
        )
        return verification, 200
    except Exception as e:
        return {'status': 'error', 'message': str(e)}, 400


@app.route('/api/data-quality/manifest/<int:representative_id>/<int:session_year>', methods=['GET'])
def get_transparency_manifest(representative_id, session_year):
    '''
    Get complete transparency manifest for a representative/session
    '''
    try:
        manifest = data_quality_service.generate_transparency_manifest(
            representative_id, session_year
        )
        return manifest, 200
    except Exception as e:
        return {'status': 'error', 'message': str(e)}, 400
"""


# ============================================================================
# STEP 7: Integration with existing analyst workflow
# ============================================================================

ANALYST_INTEGRATION_EXAMPLE = """
# In the analyst agent's response generation, add this workflow:

def generate_analyst_response(representative_id, session_year, query):
    '''Example integration of data quality checks'''

    # 1. Generate the analysis
    analysis = perform_analysis(representative_id, session_year, query)

    # 2. Check for source conflicts
    conflicts = data_quality_service.detect_source_conflicts(representative_id, session_year)

    # 3. Check for biases
    biases = data_quality_service.detect_biases(representative_id, session_year)

    # 4. Get transparency manifest
    manifest = data_quality_service.generate_transparency_manifest(representative_id, session_year)

    # 5. Construct enriched response
    response = f'''
{analysis}

✓ Source Conflict Check: {conflicts['conflicts_found']} conflicts found
{format_conflicts(conflicts)}

⚠️ Bias Analysis:
{format_biases(biases)}

📊 Data Transparency:
{format_manifest(manifest)}
    '''

    return response
"""


# ============================================================================
# STEP 8: Data Quality initialization (add to app startup)
# ============================================================================

STARTUP_CODE = """
# Add this to your app initialization (e.g., in __init__ or main section):

# Initialize data quality service
initialize_services()

# Verify database connection
if not data_quality_service or not data_quality_service.connection:
    print("WARNING: DataQualityService failed to initialize")
else:
    print("✓ DataQualityService ready")
"""


if __name__ == '__main__':
    print("=" * 80)
    print("Chat.py Integration Guide")
    print("=" * 80)
    print("\nImplementation steps:")
    print("1. Add imports (Step 1)")
    print("2. Initialize service (Step 2)")
    print("3. Update analyst agent prompt (Step 3)")
    print("4. Update bias detector agent prompt (Step 4)")
    print("5. Update transparency manifest agent prompt (Step 5)")
    print("6. Add new API endpoints (Step 6)")
    print("7. Integrate with analyst workflow (Step 7)")
    print("8. Add startup initialization (Step 8)")
    print("\nTotal time to integrate: ~2-3 hours")
    print("=" * 80)
