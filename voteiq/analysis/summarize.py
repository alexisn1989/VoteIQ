from voteiq.analysis.classify import _LARGE_DONOR_LABELS, classify_sectors


def summarize_profile(classified: list, config: dict) -> dict:
    """
    Summarize a classified sector list into a funding profile dict.

    Returns both precise and alias field names:
      large_donor_pct_by_dollars  — authoritative name
      corporate_pct               — alias (used by prompt builders)
      grassroots_pct              — alias for small_dollar_pct
    """
    total_dollars        = sum(c["total"] for c in classified)
    total_donors         = sum(c["donor_count"] for c in classified)
    small_dollar_dollars = sum(
        c["total"] for c in classified if c["concentration"] == "Small-dollar base"
    )
    large_donor_dollars  = sum(
        c["total"] for c in classified if c["concentration"] in _LARGE_DONOR_LABELS
    )

    large_donor_pct = (
        large_donor_dollars / total_dollars * 100
    ) if total_dollars else 0.0

    small_dollar_pct = (
        small_dollar_dollars / total_dollars * 100
    ) if total_dollars else 0.0

    limit = (
        config.limits.individual
        if hasattr(config, "limits")
        else config.get("contribution_limit")
    )

    return {
        # Precise names
        "level":                    config.level if hasattr(config, "level") else config.get("level"),
        "total_raised":             total_dollars,
        "total_donors":             total_donors,
        "large_donor_pct_by_dollars": large_donor_pct,
        "small_dollar_pct":         small_dollar_pct,
        "has_limits":               limit is not None,
        "contribution_limit":       limit,
        # Aliases expected by build_triangle_prompt
        "corporate_pct":            large_donor_pct,
        "grassroots_pct":           small_dollar_pct,
        # Backward-compat alias
        "large_donor_pct":          large_donor_pct,
    }


def build_classified(sector_totals: list,
                     config,
                     defections: list | None = None) -> dict:
    """
    Build the full classified bundle for all build_*_prompt functions.

    Returns:
        {
          "profile":    dict from summarize_profile(),
          "sectors":    list from classify_sectors(),
          "defections": list of defection dicts,
        }

    Defection dict shape:
        {
          "bill_id":       str,
          "bill_title":    str,
          "vote":          str,   # "YES" | "NO" | "NV"
          "party_majority": str,
          "classification": str,  # e.g. "Policy area overlaps with top donor sector"
        }
    """
    # Support both LevelConfig and legacy dict
    cfg_dict = (
        {
            "level":              config.level,
            "contribution_limit": config.limits.individual if hasattr(config, "limits") else None,
            "grassroots_threshold": config.grassroots_threshold,
            "major_donor":        config.major_donor,
        }
        if hasattr(config, "limits")
        else config
    )

    sectors = classify_sectors(sector_totals, cfg_dict)
    profile = summarize_profile(sectors, config)

    return {
        "profile":    profile,
        "sectors":    sectors,
        "defections": defections or [],
    }
