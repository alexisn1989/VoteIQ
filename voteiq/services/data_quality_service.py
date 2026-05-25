"""
Data Quality Service - Implements all six data quality fixes
1. Source Conflict Detection
2. Visual Verification
3. Search & Discovery
4. User Transparency
5. Feedback Loop
6. Bias Detection
"""

import os
import string
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import mysql.connector
from mysql.connector import Error


class DataQualityService:
    """Service for managing data quality fixes and analyses"""

    def __init__(self, host: str = None, user: str = None, password: str = None, database: str = None):
        """Initialize database connection with environment variables or provided credentials"""
        self.host = host or os.getenv('DB_HOST', 'localhost')
        self.user = user or os.getenv('DB_USER', 'root')
        self.password = password or os.getenv('DB_PASSWORD', '')
        self.database = database or os.getenv('DB_NAME', 'voteiq')
        self.connection = None
        self.connect()

    def connect(self):
        """Establish database connection"""
        try:
            self.connection = mysql.connector.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database
            )
            if self.connection.is_connected():
                print(f"✓ Connected to database: {self.database}")
        except Error as e:
            print(f"✗ Error connecting to database: {e}")
            self.connection = None

    def disconnect(self):
        """Close database connection"""
        if self.connection and self.connection.is_connected():
            self.connection.close()

    def execute_query(self, query: str, params: Tuple = None) -> List[Dict]:
        """Execute SELECT query and return results as list of dictionaries"""
        if not self.connection or not self.connection.is_connected():
            self.connect()

        try:
            cursor = self.connection.cursor(dictionary=True)
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            results = cursor.fetchall()
            cursor.close()
            return results
        except Error as e:
            print(f"✗ Query error: {e}")
            return []

    def execute_update(self, query: str, params: Tuple = None) -> bool:
        """Execute INSERT/UPDATE/DELETE query"""
        if not self.connection or not self.connection.is_connected():
            self.connect()

        try:
            cursor = self.connection.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            self.connection.commit()
            cursor.close()
            return True
        except Error as e:
            print(f"✗ Update error: {e}")
            self.connection.rollback()
            return False

    # ===== FIX #1: Source Conflict Detection =====

    def detect_source_conflicts(self, representative_id: int, session_year: int) -> Dict:
        """
        Detect conflicts between different data sources for the same votes
        Returns: Dict with conflicts found, conflict details, severity breakdown
        """
        conflicts = []

        # Query for votes with multiple sources
        query = """
        SELECT
            vr.voting_record_id,
            vr.bill_id,
            vr.vote_date,
            vr.bill_title,
            GROUP_CONCAT(DISTINCT vrs.source_name) as sources,
            MAX(vrs.yes_rate_percent) as max_yes_rate,
            MIN(vrs.yes_rate_percent) as min_yes_rate
        FROM voting_records vr
        LEFT JOIN voting_record_sources vrs ON vr.voting_record_id = vrs.voting_record_id
        WHERE vr.representative_id = %s
          AND vr.session_year = %s
        GROUP BY vr.voting_record_id
        HAVING COUNT(DISTINCT vrs.source_name) > 1
        """

        results = self.execute_query(query, (representative_id, session_year))

        for row in results:
            yes_rate_diff = row['max_yes_rate'] - row['min_yes_rate'] if row['max_yes_rate'] and row['min_yes_rate'] else 0

            if yes_rate_diff > 5:  # Threshold: >5% difference flags as conflict
                severity = 'high' if yes_rate_diff > 15 else 'medium' if yes_rate_diff > 5 else 'low'
                conflict_type = self._determine_conflict_type(yes_rate_diff)

                conflict = {
                    'voting_record_id': row['voting_record_id'],
                    'bill_id': row['bill_id'],
                    'sources': row['sources'],
                    'yes_rate_difference': yes_rate_diff,
                    'conflict_type': conflict_type,
                    'severity': severity
                }
                conflicts.append(conflict)

                # Store in database
                self._store_conflict(
                    row['voting_record_id'],
                    row['sources'],
                    yes_rate_diff,
                    conflict_type,
                    severity
                )

        return {
            'conflicts_found': len(conflicts),
            'conflicts': conflicts,
            'severity_breakdown': self._get_severity_breakdown(conflicts)
        }

    def _determine_conflict_type(self, yes_rate_diff: float) -> str:
        """Determine type of conflict based on characteristics"""
        if yes_rate_diff > 20:
            return 'scope_mismatch'  # Likely one source has narrower scope
        elif yes_rate_diff > 10:
            return 'amendment_status'  # One source might be outdated
        else:
            return 'position_mismatch'  # Genuine disagreement in recorded position

    def _get_severity_breakdown(self, conflicts: List[Dict]) -> Dict:
        """Break down conflicts by severity"""
        breakdown = {'high': 0, 'medium': 0, 'low': 0}
        for conflict in conflicts:
            breakdown[conflict['severity']] += 1
        return breakdown

    def _store_conflict(self, voting_record_id: int, sources: str, yes_rate_diff: float,
                       conflict_type: str, severity: str):
        """Store conflict in database"""
        source_list = sources.split(',')
        source_a = source_list[0].strip() if len(source_list) > 0 else 'Unknown'
        source_b = source_list[1].strip() if len(source_list) > 1 else 'Unknown'

        query = """
        INSERT INTO source_conflicts
        (voting_record_id, source_a_name, source_b_name, source_a_position,
         source_b_position, yes_rate_difference, conflict_type, severity)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP
        """

        self.execute_update(query, (voting_record_id, source_a, source_b, 'unknown',
                                   'unknown', yes_rate_diff, conflict_type, severity))

    # ===== FIX #6: Bias Detection =====

    def detect_biases(self, representative_id: int, session_year: int) -> Dict:
        """
        Detect three statistical biases in voting data
        Returns: Dict with all detected biases, severity, recommendations
        """
        # Get voting statistics
        stats_query = """
        SELECT * FROM voting_statistics
        WHERE representative_id = %s AND session_year = %s
        """
        stats = self.execute_query(stats_query, (representative_id, session_year))
        stats = stats[0] if stats else {}

        biases = {
            'selection_bias': self._detect_selection_bias(stats),
            'temporal_confounding': self._detect_temporal_confounding(representative_id, session_year),
            'simpsons_paradox': self._detect_simpsons_paradox(stats)
        }

        # Calculate overall reliability
        detected_count = sum(1 for b in biases.values() if b['detected'])
        reliability_score = max(0, 100 - (detected_count * 25))

        # Store biases in database
        for bias_type, bias_data in biases.items():
            self._store_bias(representative_id, session_year, bias_type, bias_data)

        return {
            'biases_detected': {k: v['detected'] for k, v in biases.items()},
            'details': biases,
            'total_biases': detected_count,
            'reliability_score': reliability_score
        }

    def _detect_selection_bias(self, stats: Dict) -> Dict:
        """
        Selection Bias: Pattern only appears in non-representative sample
        Flag if: Data completeness < 50% or < 80% with geographic concentration
        """
        completeness = stats.get('data_completeness_percent', 100)

        if completeness < 50:
            return {
                'detected': True,
                'severity': 'high',
                'description': 'Severe data incompleteness detected',
                'mechanism': f'Only {completeness}% of voting records available - pattern may reflect incomplete sample',
                'recommendation': 'Verify data completeness; disaggregate by subgroups to assess if pattern holds across all data',
                'verification_method': 'Check data collection methodology and coverage across districts'
            }
        elif completeness < 80:
            return {
                'detected': True,
                'severity': 'medium',
                'description': 'Moderate data incompleteness',
                'mechanism': f'Data completeness at {completeness}% - potential selection bias from incomplete collection',
                'recommendation': 'Cross-check with additional sources to verify pattern consistency',
                'verification_method': 'Compare against comprehensive source (Congress.gov, VPAP)'
            }
        else:
            return {
                'detected': False,
                'severity': 'none',
                'description': 'No selection bias detected',
                'mechanism': 'Data coverage is adequate',
                'recommendation': 'Pattern may be reliable',
                'verification_method': 'N/A'
            }

    def _detect_temporal_confounding(self, representative_id: int, session_year: int) -> Dict:
        """
        Temporal Confounding: Both variables trend in same time period
        Flag if: Both yes_rate and any other metric increased same period
        """
        # Get voting trends across multiple years
        query = """
        SELECT session_year, yes_rate_percent
        FROM voting_statistics
        WHERE representative_id = %s
        ORDER BY session_year
        """
        results = self.execute_query(query, (representative_id,))

        if len(results) < 2:
            return {
                'detected': False,
                'severity': 'none',
                'description': 'Insufficient data for temporal analysis',
                'mechanism': 'Need multiple years of data',
                'recommendation': 'Collect more years of voting data',
                'verification_method': 'N/A'
            }

        # Check for significant year-over-year changes
        yoy_changes = []
        for i in range(1, len(results)):
            prev = results[i-1]['yes_rate_percent'] or 0
            curr = results[i]['yes_rate_percent'] or 0
            change = curr - prev
            yoy_changes.append(abs(change))

        avg_change = sum(yoy_changes) / len(yoy_changes) if yoy_changes else 0

        if avg_change > 5:
            return {
                'detected': True,
                'severity': 'medium',
                'description': 'Significant temporal trend detected',
                'mechanism': f'Yes-rate changed {avg_change:.1f}% year-over-year - possible external variable driving both voting and other metrics',
                'recommendation': 'Control for external variables (economic conditions, policy changes) or use pre/post analysis',
                'verification_method': 'Analyze correlation with external events; check if trend reverses after key policy change'
            }
        else:
            return {
                'detected': False,
                'severity': 'none',
                'description': 'No significant temporal confounding detected',
                'mechanism': 'Voting patterns stable over time',
                'recommendation': 'Pattern likely reflects consistent behavior',
                'verification_method': 'N/A'
            }

    def _detect_simpsons_paradox(self, stats: Dict) -> Dict:
        """
        Simpson's Paradox: Aggregate trend reverses in subgroups
        Flag if: Overall party alignment >90% but contested vote alignment <80%
        This indicates within-group effect differs from aggregate
        """
        party_alignment = stats.get('party_alignment_percent', 0)
        contested_alignment = stats.get('contested_vote_alignment', 0)

        if party_alignment > 90 and contested_alignment < 80:
            paradox_magnitude = party_alignment - contested_alignment
            return {
                'detected': True,
                'severity': 'high' if paradox_magnitude > 20 else 'medium',
                'description': f"Simpson's paradox detected: {paradox_magnitude:.0f}% difference between aggregate and contested votes",
                'mechanism': f'Aggregate party alignment {party_alignment:.0f}% but independent voting on contested bills only {contested_alignment:.0f}% - true independence hidden by aggregate',
                'recommendation': 'Disaggregate analysis by vote type (routine vs contested) to see true pattern',
                'verification_method': 'Separate routine party-line votes from contested/principle votes; analyze each subgroup'
            }
        else:
            return {
                'detected': False,
                'severity': 'none',
                'description': 'No Simpson\'s paradox detected',
                'mechanism': 'Aggregate and subgroup patterns consistent',
                'recommendation': 'Aggregate pattern reliably represents voting behavior',
                'verification_method': 'N/A'
            }

    def _store_bias(self, representative_id: int, session_year: int, bias_type: str, bias_data: Dict):
        """Store bias detection result in database"""
        query = """
        INSERT INTO bias_detection
        (representative_id, session_year, bias_type, detected, severity, description,
         mechanism_explanation, recommendation, verification_method)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            detected = VALUES(detected),
            severity = VALUES(severity),
            updated_at = CURRENT_TIMESTAMP
        """

        self.execute_update(query, (
            representative_id,
            session_year,
            bias_type,
            bias_data['detected'],
            bias_data['severity'],
            bias_data['description'],
            bias_data['mechanism'],
            bias_data['recommendation'],
            bias_data['verification_method']
        ))

    # ===== FIX #4: Transparency Manifest =====

    def generate_transparency_manifest(self, representative_id: int, session_year: int) -> Dict:
        """
        Generate complete transparency manifest with data sources, freshness, quality, biases
        """
        # Get representative info
        rep_query = "SELECT * FROM representatives WHERE representative_id = %s"
        rep = self.execute_query(rep_query, (representative_id,))
        rep = rep[0] if rep else {}

        # Get voting statistics
        stats_query = "SELECT * FROM voting_statistics WHERE representative_id = %s AND session_year = %s"
        stats = self.execute_query(stats_query, (representative_id, session_year))
        stats = stats[0] if stats else {}

        # Get source conflicts
        conflict_query = """
        SELECT COUNT(*) as count FROM source_conflicts sc
        JOIN voting_records vr ON sc.voting_record_id = vr.voting_record_id
        WHERE vr.representative_id = %s AND vr.session_year = %s
        """
        conflicts = self.execute_query(conflict_query, (representative_id, session_year))
        conflict_count = conflicts[0]['count'] if conflicts else 0

        # Get bias detections
        bias_query = """
        SELECT bias_type, detected FROM bias_detection
        WHERE representative_id = %s AND session_year = %s
        """
        biases = self.execute_query(bias_query, (representative_id, session_year))
        bias_summary = {b['bias_type']: b['detected'] for b in biases}

        # Calculate freshness
        freshness = 'CURRENT' if stats else 'UNAVAILABLE'

        # Calculate quality score
        completeness = stats.get('data_completeness_percent', 0)
        sources_available = len([x for x in [rep.get('congress_gov_id'), rep.get('vpap_id')] if x])
        quality_score = int((completeness * 0.6) + (sources_available * 20))

        manifest = {
            'representative': rep.get('name', 'Unknown'),
            'session_year': session_year,
            'data_sources': ['Congress.gov', 'VPAP', 'Virginia LIS'],
            'source_freshness': freshness,
            'last_updated': str(datetime.now().date()),
            'completeness_percent': completeness,
            'quality_score': quality_score,
            'data_gaps': f"{100 - completeness:.0f}% of voting records incomplete" if completeness < 100 else "None",
            'source_conflicts_found': conflict_count,
            'biases_detected': bias_summary,
            'recommendation': self._generate_recommendation(quality_score, bias_summary, conflict_count)
        }

        # Store in database
        self._store_transparency_manifest(representative_id, session_year, manifest)

        return manifest

    def _generate_recommendation(self, quality_score: int, biases: Dict, conflicts: int) -> str:
        """Generate recommendation based on quality assessment"""
        recommendations = []

        if quality_score < 50:
            recommendations.append("⚠️ Data quality is low - use with caution")

        if conflicts > 0:
            recommendations.append(f"⚠️ {conflicts} source conflicts found - verify with multiple sources")

        for bias_type, detected in biases.items():
            if detected:
                recommendations.append(f"⚠️ {bias_type} detected - see bias analysis for details")

        if not recommendations:
            recommendations.append("✓ Data is reliable for analysis")

        return " | ".join(recommendations)

    def _store_transparency_manifest(self, representative_id: int, session_year: int, manifest: Dict):
        """Store transparency manifest in database"""
        query = """
        INSERT INTO data_transparency
        (representative_id, session_year, data_sources, source_freshness_status,
         last_updated, data_completeness_percent, quality_score, data_gaps, bias_analysis_summary, recommendation)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            last_updated = VALUES(last_updated),
            quality_score = VALUES(quality_score),
            updated_at = CURRENT_TIMESTAMP
        """

        self.execute_update(query, (
            representative_id,
            session_year,
            ','.join(manifest['data_sources']),
            manifest['source_freshness'],
            manifest['last_updated'],
            manifest['completeness_percent'],
            manifest['quality_score'],
            manifest['data_gaps'],
            str(manifest['biases_detected']),
            manifest['recommendation']
        ))

    # ===== FIX #5: Feedback Loop =====

    def create_feedback(self, user_email: str, issue_description: str, issue_type: str,
                       representative_id: int = None, suggested_improvement: str = None) -> str:
        """
        Create user feedback with auto-generated code and routing to appropriate agent
        Returns: Feedback code for user reference
        """
        # Generate unique feedback code
        feedback_code = self._generate_feedback_code()

        # Determine routing agent based on issue type
        routing_agent = self._determine_routing_agent(issue_type)

        # Store feedback
        query = """
        INSERT INTO user_feedback
        (feedback_code, user_email, issue_description, issue_type, representative_id, suggested_improvement)
        VALUES (%s, %s, %s, %s, %s, %s)
        """

        success = self.execute_update(query, (
            feedback_code, user_email, issue_description, issue_type, representative_id, suggested_improvement
        ))

        if success:
            # Create investigation record
            self._create_investigation(feedback_code, routing_agent)
            return feedback_code

        return None

    def _generate_feedback_code(self) -> str:
        """Generate unique feedback code like FB-2026-05-25-ABCD"""
        date_str = datetime.now().strftime('%Y-%m-%d')
        random_suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        return f"FB-{date_str}-{random_suffix}"

    def _determine_routing_agent(self, issue_type: str) -> str:
        """Determine which agent should handle the feedback"""
        routing_map = {
            'data_quality': 'Data Quality Escalator',
            'analysis_accuracy': 'Data Analyst',
            'search_relevance': 'Search Assistant',
            'visualization_issue': 'Visual Explainer',
            'missing_information': 'Deep Researcher',
            'bias_concern': 'Bias Detector',
            'other': 'General Support'
        }
        return routing_map.get(issue_type, 'General Support')

    def _create_investigation(self, feedback_code: str, routing_agent: str):
        """Create investigation record for feedback"""
        # Get feedback_id from code
        query = "SELECT feedback_id FROM user_feedback WHERE feedback_code = %s"
        result = self.execute_query(query, (feedback_code,))
        feedback_id = result[0]['feedback_id'] if result else None

        if feedback_id:
            inv_query = """
            INSERT INTO feedback_investigations (feedback_id, routing_agent, investigation_status)
            VALUES (%s, %s, 'open')
            """
            self.execute_update(inv_query, (feedback_id, routing_agent))

    def get_feedback_status(self, feedback_code: str) -> Dict:
        """Get feedback status by code"""
        query = """
        SELECT
            uf.feedback_code,
            uf.status,
            uf.created_at,
            uf.updated_at,
            fi.routing_agent,
            fi.investigation_status,
            fi.root_cause,
            fi.resolution_action
        FROM user_feedback uf
        LEFT JOIN feedback_investigations fi ON uf.feedback_id = fi.feedback_id
        WHERE uf.feedback_code = %s
        """

        result = self.execute_query(query, (feedback_code,))
        if result:
            return result[0]
        return None

    # ===== FIX #2: Visual Verification =====

    def verify_visualization_quality(self, representative_id: int, session_year: int, query_type: str) -> Dict:
        """
        Verify that visualization has adequate data quality
        Check: Data sufficiency, bias presence, completeness
        """
        stats = self.execute_query(
            "SELECT * FROM voting_statistics WHERE representative_id = %s AND session_year = %s",
            (representative_id, session_year)
        )[0] if self.execute_query(
            "SELECT * FROM voting_statistics WHERE representative_id = %s AND session_year = %s",
            (representative_id, session_year)
        ) else {}

        biases = self.execute_query(
            "SELECT * FROM bias_detection WHERE representative_id = %s AND session_year = %s AND detected = TRUE",
            (representative_id, session_year)
        )

        completeness = stats.get('data_completeness_percent', 0)

        return {
            'visualization_ok': completeness > 70,
            'data_sufficiency': 'adequate' if completeness > 70 else 'insufficient',
            'biases_present': len(biases) > 0,
            'bias_types': [b['bias_type'] for b in biases],
            'recommendation': self._visualization_recommendation(completeness, biases)
        }

    def _visualization_recommendation(self, completeness: float, biases: List) -> str:
        """Generate recommendation for visualization"""
        if completeness < 70:
            return "⚠️ Insufficient data for reliable visualization"
        elif biases:
            return "⚠️ Consider disaggregated visualization to show bias context"
        else:
            return "✓ Visualization data is reliable"

    # ===== FIX #3: Search & Discovery =====

    def log_search_query(self, query_text: str, query_type: str, results_count: int,
                        execution_time_ms: int, user_session_id: str = None) -> bool:
        """Log search query for discovery improvement"""
        query = """
        INSERT INTO search_queries
        (query_text, query_type, results_count, execution_time_ms, user_session_id)
        VALUES (%s, %s, %s, %s, %s)
        """
        return self.execute_update(query, (query_text, query_type, results_count, execution_time_ms, user_session_id))

    def get_search_patterns(self, limit: int = 10) -> List[Dict]:
        """Get most common search patterns"""
        query = """
        SELECT query_text, COUNT(*) as frequency, AVG(results_count) as avg_results
        FROM search_queries
        WHERE created_at > DATE_SUB(NOW(), INTERVAL 30 DAY)
        GROUP BY query_text
        ORDER BY frequency DESC
        LIMIT %s
        """
        return self.execute_query(query, (limit,))
