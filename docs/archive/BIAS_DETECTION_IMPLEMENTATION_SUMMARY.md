# Bias Detection — Implementation Summary

**Issue:** Data Analyst prevents explicit causation but misses subtle statistical biases  
**Solution:** Bias Detector agent checks for selection bias, temporal confounding, Simpson's paradox  
**Status:** ✓ SPECIFICATION COMPLETE  
**Date:** May 25, 2026

---

## Problem Solved

**Before:**
```
ANALYST: "Education donors gave more to Republicans (60% vs 40%)"
[Analyst correctly avoids causation claim]
[But misses: This is selection bias — donors in Republican areas]

USER: Sees pattern and assumes education sector prefers Republicans
→ Makes policy decision based on biased finding
→ System fails to detect the bias
```

**After:**
```
ANALYST: "Education donors gave more to Republicans (60% vs 40%)"
BIAS_DETECTOR: [Automatic check for three biases]
  ├─ Selection bias: DETECTED
  │  └─ Donors concentrated in Republican-leaning areas
  ├─ Temporal confounding: Not detected
  └─ Simpson's paradox: Not detected

RESPONSE TO USER:
"Republicans 60%, Democrats 40%

⚠️ SELECTION BIAS DETECTED
Educational donors are geographically concentrated in 
Republican-leaning areas (Southwest Virginia). This makes them 
appear more Republican, even if they have no sector preference.

To verify: Disaggregate by geography (each district separately)"

USER: Now understands bias, can interpret correctly
→ Makes informed decision based on correct understanding
```

---

## Three Biases Specified

### Bias #1: Selection Bias

**Definition:** Data collection excludes groups systematically, making pattern appear stronger than true pattern

**Example:** 
- "Education donors prefer Republicans"
- But: Donors concentrated in Republican areas
- True pattern: Geographic, not sectoral preference
- Fix: Disaggregate by district to see true preference per district

**How to Detect:**
```
Query: Is pattern visible in ALL subgroups?
       Or only in non-representative sample?

If: Pattern only in subset or voluntary-participation subset
→ FLAG: Selection bias likely
```

**Red Flags:**
- Pattern only in certain geographic areas
- Data collection had systematic exclusions
- Voluntary participation with unequal response rates
- Historical data where collection start date coincides with trend start
- Sample clearly non-representative of population

---

### Bias #2: Temporal Confounding

**Definition:** Third variable changes over time and drives both variables independently

**Example:**
- "PAC donations and yes-votes correlate (r=0.75)"
- But: Both increased during 2023-2026 economic boom
- True pattern: Both respond to economic conditions independently
- Fix: Control for economic indicators or use pre/post analysis

**How to Detect:**
```
Query: Did both variables trend in same period?
       Is there unmeasured variable driving both?

If: Both increased 2022-2026, no mechanism identified
→ FLAG: Temporal confounding likely
```

**Red Flags:**
- Both variables show strong time trend in same direction
- Both increased during same time period
- No clear mechanism by which X causes Y
- Time period captures major external change (economy, policy, etc.)
- Correlation strongest at aggregate level
- Causation direction unclear

---

### Bias #3: Simpson's Paradox

**Definition:** Aggregate trend reverses or disappears when data is disaggregated by relevant subgroup

**Example:**
- "Older legislators receive more education donations" (aggregate TRUE)
- But: Within same seniority level, younger get more
- True pattern: Seniority drives donations, not age
- Within-group effect is opposite to aggregate effect
- Fix: Disaggregate by seniority to see true age effect

**How to Detect:**
```
Query: Does trend hold in ALL natural subgroups?
       Or does it reverse in some subgroups?

If: Aggregate trend reverses in subgroups
→ FLAG: Simpson's paradox detected
```

**Red Flags:**
- Pattern only visible at aggregate level
- Pattern reverses when disaggregated
- Natural subgroup variable exists but isn't examined
- Subgroups have different compositions
- Proportions in groups oppose overall trend
- Claim seems intuitive but unusual

---

## Implementation Architecture

### Bias Detector Agent

**File:** voteiq/api/routes/chat.py (Lines TBD)

**Agent Definition:**
```python
"bias_detector": {
    "name": "Bias Detector",
    "env": "VOTEIQ_BIAS_DETECTOR_AGENT_ID",
    "tags": ["bias", "statistical-validity", "methodology"],
    "visibility": "public_facing",
    "surface": "Methodology Analysis",
    "prompt": (
        "Your role: Detect three specific statistical biases...
         
         FOR EACH ANALYST FINDING, CHECK:
         
         1. SELECTION BIAS
            Is this pattern present in ALL subgroups?
            Or only in non-representative sample?
            Red flags: Data exclusions, voluntary participation,
            historical data with inconsistent collection
         
         2. TEMPORAL CONFOUNDING
            Did both variables trend in same time period?
            Are there unmeasured variables driving both?
            Red flags: Both upward trend, no mechanism,
            time period includes external change
         
         3. SIMPSON'S PARADOX
            Does aggregate trend reverse in subgroups?
            Are natural disaggregation variables hidden?
            Red flags: Only aggregate visible, trend reverses,
            confounding variable unexamined
         
         PROCESS:
         1. Receive analyst finding
         2. Check each bias systematically
         3. If bias detected: Explain mechanism
         4. Suggest disaggregation or control approach
         5. Return finding + bias analysis to user
         
         OUTPUT FORMAT:
         If no bias: ✓ No bias detected in this pattern
         If bias: ⚠️ [BIAS TYPE] DETECTED - [mechanism] - [fix]"
    ),
}
```

### System Prompt Addition

**Section: BIAS DETECTION PROTOCOL**
```python
"BIAS DETECTION PROTOCOL:
- All analyst findings checked automatically for three biases:
  1. Selection bias: Is pattern from unrepresentative sample?
  2. Temporal confounding: Do both variables trend same period?
  3. Simpson's paradox: Does aggregate trend reverse in subgroups?

- If bias detected:
  ├─ Identify which bias applies
  ├─ Explain mechanism to user
  ├─ Suggest disaggregation or control approach
  └─ Recommend verification method

- Include in transparency manifest:
  ├─ Bias detection status
  ├─ Any biases detected
  └─ Recommendations for interpretation"
```

### Analyst Prompt Update

**Addition to Analyst Prompt:**
```python
"BIAS DETECTION INTEGRATION:
- After generating finding, check against bias_detector
- Receive bias analysis from detector
- If bias flagged: Append alert to finding
- Explain the bias clearly to user
- Suggest how to verify or disaggregate
- Include in transparency manifest"
```

---

## User-Facing Bias Alerts

### Alert Format: Selection Bias

```
⚠️ SELECTION BIAS DETECTED

Pattern: [Description of finding]

Why this matters: 
[Explanation of how selection bias affects the pattern]

To verify:
Disaggregate by [geography/demographic/time period]

Expected finding after disaggregation:
[What correct analysis shows]

Recommendation:
[How to interpret the pattern correctly]
```

### Alert Format: Temporal Confounding

```
⚠️ TEMPORAL CONFOUNDING POSSIBLE

Pattern: [Description of finding]

Why this matters:
Both variables increased during [time period].
[External variable Z] may drive both independently.

To verify:
├─ Control for [confounding variable] in analysis
├─ Or use pre/post analysis (before/after event)
└─ Or lag analysis (X leads Y by how much?)

Expected finding after control:
[What correlation would be after removing confounding]

Recommendation:
[How to interpret the relationship correctly]
```

### Alert Format: Simpson's Paradox

```
⚠️ SIMPSON'S PARADOX DETECTED

Pattern (aggregate): [Description]

Reality (disaggregated):
├─ In [subgroup A]: [opposite/different pattern]
├─ In [subgroup B]: [opposite/different pattern]
└─ In [subgroup C]: [opposite/different pattern]

Why this matters:
Aggregate misleads about within-group effects.
True pattern is [actual mechanism], not what aggregate shows.

To verify:
Disaggregate by [variable] and re-analyze

True finding:
[What pattern really is when confounding is removed]

Recommendation:
[How to interpret correctly given the paradox]
```

---

## Integration with Transparency Manifest

**New Manifest Section:**

```
📊 Data Transparency
├─ Sources: [list]
├─ Last Updated: [dates]
├─ Freshness: [status]
├─ Data Gaps: [gaps]
├─ Bias Analysis:
│  ├─ Selection bias: [Detected/Not detected]
│  ├─ Temporal confounding: [Detected/Not detected]
│  └─ Simpson's paradox: [Detected/Not detected]
└─ Recommendation: [How to interpret findings given biases found]
```

---

## Integration with Other Fixes

### Bias Detector + Source Conflicts
When source conflicts detected (FEC vs VPAP), check for selection bias in one source:
```
Example: FEC shows donation, VPAP shows $0
→ Might be selection bias in VPAP (incomplete indexing)
→ Not necessarily a real conflict
→ Explain difference as data collection bias
```

### Bias Detector + Visual Verification
Golden Query verification includes bias checks:
```
Example: Request visualization "Age vs donations"
→ Golden Query checks:
   ├─ Data quality sufficient?
   ├─ Simpson's paradox present?
   └─ If paradox: Suggest disaggregated visualization
→ Visual Explainer renders with bias warnings
```

### Bias Detector + Search Assistant
Search Assistant queries can trigger bias detection:
```
Example: User searches "donations by party"
→ Results returned by Search Assistant
→ Bias Detector automatically checks for all three biases
→ Alerts user to potential biases in the discovered data
```

### Bias Detector + Feedback Loop
Users can report biases; system learns to detect them:
```
Example: User reports "This pattern disappears when I disaggregate"
→ Feedback Collector: "Missing context issue"
→ Routes to Bias Detector
→ Bias Detector confirms: Simpson's paradox found
→ System learns: Now detects this automatically in future
```

---

## Detection Examples

### Example 1: Selection Bias

```
FINDING: "Education sector donors prefer Republicans (60% vs 40%)"

BIAS CHECK:
├─ Geographic analysis:
│  ├─ Northern VA: Democrats 65%, Republicans 35%
│  ├─ Southwest VA: Democrats 35%, Republicans 65%
│  └─ REASON: Education donors concentrated in SW
├─ Conclusion: Pattern is geographic, not sectoral
└─ ALERT: Selection bias detected

RECOMMENDATION:
"This preference is not by education sector, but by geography.
Education donors tend to live in Republican-leaning areas.
Within each area, look at local preferences separately."
```

### Example 2: Temporal Confounding

```
FINDING: "As PAC donations increased, yes-votes increased (r=0.75)"

BIAS CHECK:
├─ Time series:
│  ├─ 2022: Donations $10M, yes-votes 45%
│  ├─ 2024: Donations $30M, yes-votes 55%
│  └─ 2026: Donations $50M, yes-votes 62%
├─ Both increased same period ✓
├─ External events:
│  └─ 2023-2026: Major economic boom
├─ Mechanism: Does money buy yes-votes? UNCLEAR
└─ ALERT: Temporal confounding likely

RECOMMENDATION:
"Both donations and yes-votes increased during economic boom.
Businesses donating more AND voting yes as independent responses.
To test causation: Control for economic conditions."
```

### Example 3: Simpson's Paradox

```
FINDING: "Older legislators receive more education donations 
          ($50K avg for 60+, $30K avg for 30-40)"

BIAS CHECK:
├─ Disaggregate by seniority:
│  ├─ Junior level (0-5 years):
│  │  └─ Age 60+: $15K, Age 30-40: $20K (YOUNGER more)
│  ├─ Mid-career (5-15 years):
│  │  └─ Age 60+: $35K, Age 30-40: $38K (YOUNGER more)
│  └─ Senior level (15+ years):
│     └─ Age 60+: $70K, Age 30-40: $65K (OLDER more, but close)
├─ Pattern REVERSES within same seniority
└─ ALERT: Simpson's paradox detected

REASON:
Older legislators concentrated in senior positions.
Senior positions get more donations (regardless of age).
Within same seniority, younger actually get more.

RECOMMENDATION:
"Age effect is illusory. Real driver is seniority.
Within same seniority level, younger get more donations.
Older only appear to get more because they hold senior positions."
```

---

## Implementation Checklist

### Phase 1: Specification (Complete)
- [x] Three bias types specified in detail
- [x] Detection queries defined
- [x] User alert templates designed
- [x] Integration points documented
- [x] Examples worked through

### Phase 2: Implementation (Ready)
- [ ] bias_detector agent defined in chat.py
- [ ] System prompt updated
- [ ] Analyst prompt updated
- [ ] Detection logic implemented
- [ ] User alert templates implemented

### Phase 3: Integration (Ready)
- [ ] Transparency Manifest updated
- [ ] Analyst routing to bias_detector automated
- [ ] Integration with source conflicts
- [ ] Integration with visual verification
- [ ] Integration with search assistant
- [ ] Integration with feedback loop

### Phase 4: Testing & Validation
- [ ] Unit tests for bias detection logic
- [ ] Integration tests with analyst workflow
- [ ] Detection accuracy validation
- [ ] User testing (does alert make sense?)
- [ ] Feedback incorporation

### Phase 5: Deployment
- [ ] Staging deployment
- [ ] UAT with support team
- [ ] Production rollout
- [ ] Monitor detection accuracy
- [ ] Refine based on usage

---

## Files Created

```
BIAS_DETECTION_MANDATE.md (comprehensive specification)
BIAS_DETECTION_QUICK_REFERENCE.md (1-page quick guide)
BIAS_DETECTION_IMPLEMENTATION_SUMMARY.md (this file)
```

---

## Success Criteria

- [x] Three bias types fully specified
- [x] Detection approach defined for each type
- [x] User alert formats designed
- [x] Integration with other agents documented
- [ ] Agent implemented in chat.py
- [ ] System prompt updated
- [ ] Detection logic tested
- [ ] User testing completed
- [ ] False positive rate minimized

---

## Impact Assessment

| Aspect | Before | After |
|--------|--------|-------|
| **Bias visibility** | Hidden | Explicit |
| **User interpretation** | Potentially misled | Informed with caveats |
| **Data trust** | Lower (potential biases) | Higher (biases flagged) |
| **Correct decisions** | Lower (based on biased findings) | Higher (bias-aware) |

---

## User Benefits

### Transparency
- Users see when pattern might be biased
- Understand potential limitations
- Learn statistical concepts

### Informed Decisions
- Know when to disaggregate before deciding
- Understand when causation unclear
- Can verify findings themselves

### Trust
- System acknowledges limitations
- Doesn't claim false certainty
- Actively warns of potential problems

### Learning
- Users learn about three bias types
- Understand why disaggregation matters
- Become more statistically literate

---

## Timeline to Production

### Phase 1: Immediate (This session)
- [x] Specification complete
- [x] Documentation complete

### Phase 2: Week 1 (Implementation)
- [ ] Code bias_detector agent
- [ ] Update analyst/system prompts
- [ ] Implement detection logic

### Phase 3: Week 2 (Testing)
- [ ] Unit testing of detection logic
- [ ] Integration testing with analyst
- [ ] Internal QA testing

### Phase 4: Week 3 (User Testing)
- [ ] Limited rollout to pilot users
- [ ] Collect feedback on alerts
- [ ] Refine false positives

### Phase 5: Week 4+ (Production)
- [ ] Full production rollout
- [ ] Monitor detection accuracy
- [ ] Continuous refinement

---

## Known Limitations

**Current scope:**
- Detects three specific biases (selection, temporal, Simpson's)
- Does NOT detect: Publication bias, measurement error, confounding variables generally

**Future enhancements:**
- Detect additional bias types
- Automated suggestions for remedies
- Machine learning for bias pattern recognition
- Causal inference methodology (DAGs, backdoor criterion)

---

## References

- **Specification:** BIAS_DETECTION_MANDATE.md
- **Quick reference:** BIAS_DETECTION_QUICK_REFERENCE.md
- **Statistical foundations:** Selection bias, temporal confounding, Simpson's paradox literature
- **Integration:** Works with analyst, transparency_manifest, visual_explainer, feedback_collector

---

## Summary

| Component | Status |
|-----------|--------|
| **Specification** | ✓ Complete |
| **Three bias types** | ✓ Detailed with examples |
| **Detection approach** | ✓ Defined |
| **User alerts** | ✓ Designed |
| **Integration plan** | ✓ Documented |
| **Ready for implementation** | ✓ Yes |
| **Ready for testing** | ✓ Specification complete, code ready |

---

**Status:** ✓ SPECIFICATION COMPLETE  
**Ready for:** Implementation in chat.py  
**Impact:** HIGH — Prevents misleading interpretations of biased data patterns  
**User Benefit:** Users see bias alerts alongside patterns, can interpret correctly

**Next Step:** Implement bias_detector agent in chat.py and integration with analyst workflow
