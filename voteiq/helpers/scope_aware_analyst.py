"""
Scope-Aware Public Record Analyst Helper

Wraps the public record analyst with scope policy enforcement.
Filters sources, provides scope context, and generates dynamic footers.

Not a full agent — just a helper that enforces scope rules.
"""
from typing import Optional, List, Dict, Tuple
from voteiq.config.scope_policy import (
    ScopePolicy,
    QueryScope,
    get_scope_policy,
)


class ScopeAwareAnalyst:
    """Wraps analyst calls with scope policy enforcement"""

    def __init__(self):
        self.policy = get_scope_policy()

    def analyze_query(self, query: str) -> Dict:
        """
        Analyze query scope and prepare context for analyst

        Returns: {
            query: str,
            detected_scope: QueryScope,
            scope_explanation: str,
            scope_confidence: float,
            allowed_sources: [str],
            blocked_sources: [str],
            freshness_dates: {source: date},
            scope_notes: str,
        }
        """
        # Detect scope
        scope, explanation, confidence = self.policy.detect_scope(query)

        # Get allowed/blocked sources
        allowed_sources = self.policy.get_enabled_sources(scope)
        blocked_sources = self.policy.get_disabled_sources(scope)

        # Get freshness dates for allowed sources
        freshness_dates = {}
        for source in allowed_sources:
            freshness = self.policy.get_source_freshness(source)
            if freshness:
                freshness_dates[source] = freshness

        # Build scope notes
        scope_notes = self._build_scope_notes(scope, confidence)

        return {
            "query": query,
            "detected_scope": scope.value,
            "scope_explanation": explanation,
            "scope_confidence": confidence,
            "allowed_sources": allowed_sources,
            "blocked_sources": blocked_sources,
            "freshness_dates": freshness_dates,
            "scope_notes": scope_notes,
        }

    def filter_sources_in_response(
        self, sources_mentioned: List[str], scope: QueryScope
    ) -> Tuple[List[str], List[str]]:
        """
        Filter sources in a response based on scope

        Returns: (allowed_sources_used, blocked_sources_found)
        """
        allowed = self.policy.get_enabled_sources(scope)
        blocked = self.policy.get_disabled_sources(scope)

        allowed_used = [s for s in sources_mentioned if s in allowed]
        blocked_found = [s for s in sources_mentioned if s in blocked]

        return allowed_used, blocked_found

    def build_analyst_prompt(self, query: str, scope_context: Dict) -> str:
        """
        Build analyst system prompt with scope guidance

        Tells analyst which sources are allowed/blocked
        """
        scope = scope_context["detected_scope"]
        allowed = scope_context["allowed_sources"]
        blocked = scope_context["blocked_sources"]
        explanation = scope_context["scope_explanation"]

        prompt_additions = f"""
## Scope Context for This Query

**Detected Scope:** {scope}
**Reason:** {explanation}

**Allowed Sources (use these):**
- {chr(10).join(['- ' + s for s in allowed])}

**Blocked Sources (do NOT use these):**
- {chr(10).join(['- ' + s for s in blocked]) if blocked else '(none)'}

**Important:**
Only cite sources from the Allowed Sources list above.
Do not reference or cite sources from the Blocked Sources list.
If you cannot answer with allowed sources, say so clearly.
"""
        return prompt_additions

    def generate_dynamic_footer(
        self, sources_used: List[str], query: str, include_explanation: bool = True
    ) -> str:
        """
        Generate a dynamic footer showing only sources used

        Returns formatted footer with freshness dates
        """
        scope, _, _ = self.policy.detect_scope(query)
        return self.policy.build_source_footer(sources_used, scope, include_explanation)

    def validate_response_sources(
        self, response_text: str, scope: QueryScope, expected_sources: Optional[List[str]] = None
    ) -> Dict:
        """
        Validate that response uses allowed sources only

        Returns: {
            valid: bool,
            allowed_sources_found: [str],
            blocked_sources_found: [str],
            warnings: [str],
        }
        """
        allowed = self.policy.get_enabled_sources(scope)
        blocked = self.policy.get_disabled_sources(scope)

        # Parse sources mentioned in response
        mentioned_sources = self._extract_source_mentions(response_text)

        allowed_found = [s for s in mentioned_sources if s in allowed]
        blocked_found = [s for s in mentioned_sources if s in blocked]

        warnings = []
        if blocked_found:
            warnings.append(
                f"Response mentions blocked sources for this scope: {', '.join(blocked_found)}"
            )

        return {
            "valid": len(blocked_found) == 0,
            "allowed_sources_found": allowed_found,
            "blocked_sources_found": blocked_found,
            "warnings": warnings,
        }

    def _build_scope_notes(self, scope: QueryScope, confidence: float) -> str:
        """Build human-readable scope notes"""
        if confidence < 0.5:
            return (
                f"Scope detection confidence is LOW ({confidence:.0%}). "
                "If you need federal sources, please clarify your question."
            )
        elif confidence < 0.8:
            return (
                f"Scope detection confidence is MEDIUM ({confidence:.0%}). "
                "Defaulting to Virginia/local sources. "
                "If you need federal sources, please ask explicitly."
            )
        else:
            # High confidence
            if scope == QueryScope.LOCAL_HAMPTON_ROADS:
                return "Local government query detected. Using local/municipal sources."
            elif scope == QueryScope.VIRGINIA_STATE:
                return "Virginia state query detected. Using Virginia state sources."
            elif scope == QueryScope.FEDERAL:
                return "Federal query detected. Using federal sources (FEC, Congress.gov)."
            elif scope == QueryScope.MIXED:
                return "Mixed state/federal query detected. Using all relevant sources."
            else:
                return "Scope unclear. Defaulting to Virginia/local sources."

    def _extract_source_mentions(self, text: str) -> List[str]:
        """Extract source names mentioned in text"""
        # Simple matching for common source names
        source_names = [
            "Virginia LIS",
            "OpenStates",
            "Virginia SBE",
            "VPAP",
            "Legistar",
            "Municipal Records",
            "FEC",
            "Congress.gov",
        ]

        mentioned = []
        for source in source_names:
            if source.lower() in text.lower():
                mentioned.append(source)

        return mentioned

    def get_scope_summary(self, scope: QueryScope) -> Dict:
        """Get detailed summary of a scope"""
        return self.policy.get_scope_summary(scope)


# Singleton instance
_analyst_instance: Optional[ScopeAwareAnalyst] = None


def get_scope_aware_analyst() -> ScopeAwareAnalyst:
    """Get or create singleton analyst instance"""
    global _analyst_instance
    if _analyst_instance is None:
        _analyst_instance = ScopeAwareAnalyst()
    return _analyst_instance
