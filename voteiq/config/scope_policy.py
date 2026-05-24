"""
VoteIQ Scope Policy Layer

Controls source selection, agent routing, and citation behavior based on query scope.
Allows conditional federal source access when queries clearly relate to federal topics.

Default: Virginia state / Hampton Roads local
Conditional: Federal sources available when query relates to federal topics
"""
import json
import re
import os
from typing import Optional, List, Dict, Tuple
from enum import Enum
from pathlib import Path


class QueryScope(Enum):
    """Query scope classification"""
    LOCAL_HAMPTON_ROADS = "local_hampton_roads"
    VIRGINIA_STATE = "virginia_state"
    FEDERAL = "federal"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ScopePolicy:
    """Manages query scope detection and source selection"""

    def __init__(self, policy_file: Optional[str] = None):
        """Initialize scope policy from config file"""
        if policy_file is None:
            # Default to bundled policy
            policy_file = os.path.join(
                os.path.dirname(__file__), "scope_policy.json"
            )

        with open(policy_file) as f:
            self.config = json.load(f)

        self.scopes = self.config.get("scopes", {})
        self.sources = self.config.get("source_definitions", {})
        self.detection_rules = self.config.get("scope_detection_rules", [])
        self.routing_rules = self.config.get("routing_rules", {})
        self.footer_rules = self.config.get("source_footer_rules", {})

    def detect_scope(self, query: str) -> Tuple[QueryScope, str, float]:
        """
        Detect query scope (local, state, federal, mixed, unknown)

        Returns: (scope, explanation, confidence)
        """
        query_lower = query.lower()

        # Apply detection rules in order of specificity
        matches = []
        for rule in self.detection_rules:
            pattern = rule.get("pattern", "")
            flags = re.IGNORECASE if rule.get("case_insensitive", False) else 0

            if re.search(pattern, query_lower, flags):
                scope_str = rule.get("scope", "unknown")
                confidence = self._confidence_to_float(rule.get("confidence", "low"))
                matches.append((scope_str, confidence, rule.get("explanation", "")))

        # Return highest confidence match
        if matches:
            scope_str, confidence, explanation = max(matches, key=lambda x: x[1])
            return QueryScope(scope_str), explanation, confidence

        # Fall back to unknown
        default_scope = self.config.get("defaults", {}).get("default_scope", "virginia_state")
        return QueryScope(default_scope), "No specific scope detected; defaulting to Virginia state", 0.3

    def get_enabled_sources(
        self, scope: QueryScope, allow_federal: bool = False
    ) -> List[str]:
        """
        Get enabled sources for a given scope

        Args:
            scope: Query scope (local, state, federal, mixed, unknown)
            allow_federal: Whether to allow federal sources (default: only for federal queries)

        Returns: List of enabled source names
        """
        scope_key = scope.value
        scope_config = self.scopes.get(scope_key, {})

        # Start with enabled sources for this scope
        sources = scope_config.get("enabled_sources", [])

        # If federal scope or allow_federal, ensure federal sources are included
        if scope == QueryScope.FEDERAL or allow_federal:
            disabled = scope_config.get("disabled_sources", [])
            federal_sources = ["FEC", "Congress.gov"]
            for fed_source in federal_sources:
                if fed_source not in disabled:
                    if fed_source not in sources:
                        sources.append(fed_source)

        return sources

    def get_disabled_sources(self, scope: QueryScope) -> List[str]:
        """Get disabled sources for a scope"""
        scope_key = scope.value
        scope_config = self.scopes.get(scope_key, {})
        return scope_config.get("disabled_sources", [])

    def filter_sources(self, sources: List[str], scope: QueryScope) -> List[str]:
        """
        Filter source list based on scope

        Removes sources not enabled for the scope.
        """
        enabled = self.get_enabled_sources(scope)
        disabled = self.get_disabled_sources(scope)

        filtered = []
        for source in sources:
            if source in disabled:
                continue
            if source in enabled or source in self.sources:
                filtered.append(source)

        return filtered

    def get_source_freshness(self, source: str) -> Optional[str]:
        """Get 'current through' date for a source"""
        source_config = self.sources.get(source, {})
        return source_config.get("current_through")

    def build_source_footer(
        self, sources_used: List[str], scope: QueryScope, include_explanation: bool = True
    ) -> str:
        """
        Build a source footer showing only sources actually used

        Args:
            sources_used: List of sources that were actually referenced in response
            scope: Query scope
            include_explanation: Whether to include scope explanation

        Returns: Formatted source footer
        """
        if not sources_used:
            return ""

        # Get freshness dates
        source_parts = []
        for source in sources_used:
            freshness = self.get_source_freshness(source)
            if freshness and freshness != "varies_by_municipality":
                source_parts.append(f"{source} (current through {freshness})")
            else:
                source_parts.append(source)

        footer = "Sources used: " + " · ".join(source_parts)

        # Add explanation if requested
        if include_explanation:
            if scope == QueryScope.FEDERAL:
                footer += " | Federal sources per congressional delegation query"
            elif scope == QueryScope.MIXED:
                footer += " | Combined state/federal sources per query scope"
            elif scope == QueryScope.LOCAL_HAMPTON_ROADS:
                footer += " | Local government sources"
            elif scope == QueryScope.VIRGINIA_STATE:
                footer += " | Virginia state sources"

        return footer

    def should_route_to_specialist(self, scope: QueryScope) -> bool:
        """
        Determine if query should be routed to a specialist agent

        Federal queries that are complex may be routed to specialized federal agents.
        """
        return scope == QueryScope.FEDERAL

    def get_routing_guidance(
        self, agent_name: str, scope: QueryScope
    ) -> Dict[str, any]:
        """
        Get routing guidance for an agent based on query scope

        Returns: {allowed: bool, preferred_scopes: [str], note: str}
        """
        agent_rules = self.routing_rules.get(agent_name, {})

        allowed_scopes = agent_rules.get("allowed_scopes", [])
        preferred = agent_rules.get("prefer_scopes", [])

        return {
            "allowed": scope.value in allowed_scopes,
            "preferred": scope.value in preferred,
            "note": agent_rules.get("note", ""),
        }

    def _confidence_to_float(self, confidence_str: str) -> float:
        """Convert confidence string to float (0.0-1.0)"""
        confidence_map = {
            "low": 0.3,
            "medium": 0.6,
            "high": 0.9,
        }
        return confidence_map.get(confidence_str, 0.5)

    def get_scope_summary(self, scope: QueryScope) -> Dict:
        """Get detailed summary of a scope"""
        scope_key = scope.value
        scope_config = self.scopes.get(scope_key, {})

        return {
            "scope": scope.value,
            "description": scope_config.get("description", ""),
            "enabled_sources": scope_config.get("enabled_sources", []),
            "disabled_sources": scope_config.get("disabled_sources", []),
        }


# Singleton instance
_policy_instance: Optional[ScopePolicy] = None


def get_scope_policy() -> ScopePolicy:
    """Get or create singleton scope policy instance"""
    global _policy_instance
    if _policy_instance is None:
        _policy_instance = ScopePolicy()
    return _policy_instance


def detect_query_scope(query: str) -> Tuple[QueryScope, str, float]:
    """Convenience function to detect scope of a query"""
    policy = get_scope_policy()
    return policy.detect_scope(query)


def get_enabled_sources_for_query(
    query: str, allow_federal: bool = False
) -> Tuple[List[str], QueryScope, str]:
    """
    Get enabled sources for a query

    Returns: (sources, scope, explanation)
    """
    policy = get_scope_policy()
    scope, explanation, confidence = policy.detect_scope(query)
    sources = policy.get_enabled_sources(scope, allow_federal)
    return sources, scope, explanation


def build_footer_for_response(
    sources_used: List[str], query: str, include_explanation: bool = True
) -> str:
    """Build source footer for a response"""
    policy = get_scope_policy()
    scope, _, _ = policy.detect_scope(query)
    return policy.build_source_footer(sources_used, scope, include_explanation)
