# Bias Detection Mandate — Selection Bias, Temporal Confounding, Simpson's Paradox

**Issue:** Data Analyst prevents explicit causation but misses subtle methodological biases  
**Problem:** Users see patterns that appear valid but are artifacts of selection bias, temporal confounding, or Simpson's paradox  
**Status:** ✓ MANDATE COMPLETE  
**Date:** May 25, 2026

---

## The Problem

**Before:**
```
ANALYST: "Education donors gave more to Republican legislators (60% vs 40%)"
[Analyst correctly avoids saying "causes voting"]
[But misses: This could be selection bias — Republicans in education-heavy districts]
[Or temporal confounding: Both donations and ideology increased over time]
[Or Simpson's paradox: Trend reverses when disaggregated by district]

→ User sees apparent pattern but it's methodologically flawed
→ User makes decisions based on artifact, not real pattern
```

**After:**
```
ANALYST: "Education donors gave more to Republican legislators (60% vs 40%)"
BIAS_DETECTOR: [Checks data for three bias patterns]
├─ Selection bias? [Educational donors concentrate in certain districts]
├─ Temporal confounding? [Both donations AND ideology shifted over same period]
└─ Simpson's paradox? [Disaggregated by district shows opposite pattern]

RESULT: "⚠️ METHODOLOGICAL ALERT: This pattern may reflect [specific bias type]"
→ User sees pattern AND potential bias that explains it
→ User can make informed decision about validity
```

---

## Three Biases to Detect

### Bias #1: Selection Bias

**Definition:** Data collection methodology systematically excludes groups, making observed pattern appear stronger than true pattern

**Examples:**
- "Education donors gave more to Republicans" (TRUE: but selection bias explains it)
  - Reality: Educational donors concentrate in suburban districts
  - Suburban districts lean Republican
  - Pattern is artifact of geographic selection, not sector preference
  
- "Women donate less to politics" (Appears TRUE but misleading)
  - Reality: Donation data comes from FEC, which historically under-reported women donors
  - Pattern reflects reporting bias, not actual behavior
  
- "Younger legislators vote differently" (Could be selection bias)
  - Reality: Younger legislators only recently elected to certain districts
  - Those districts have different demographics
  - "Age effect" is actually district effect

**Detection Approach:**
```
When analyst reports pattern:
1. Is the pattern present in ALL relevant subgroups?
2. Or only in specific subgroups that were included?
3. Are there systematic reasons why some data was collected and not other?
4. Does pattern hold when data collection is randomized?

If: Pattern only appears in non-representative sample
→ FLAG: "Selection bias may explain this pattern"
```

**Red Flags:**
- Pattern only visible in subset of data
- Data collection process had systematic exclusions
- Pattern disappears when full population examined
- Historical data shows time-dependent data collection
- Voluntary participation in data gathering

---

### Bias #2: Temporal Confounding

**Definition:** A third variable changes over time and influences both variables in the analysis, creating false correlation

**Examples:**
- "As donations increased, yes-votes increased" (Appears causal but confounded)
  - Reality: Both increased because economy improved in same period
  - Both donations AND yes-voting increased as independent reactions to economy
  - Confounding variable: Economic conditions (unmeasured)
  
- "PAC spending and candidate success correlate" (Could be temporal confounding)
  - Reality: Both increased because electoral landscape changed
  - Stronger candidates get more PAC money AND win more (both respond to political environment)
  - Confounding variable: Candidate quality / political environment shift
  
- "Younger legislators have higher education PAC donations" (Could be confounded)
  - Reality: Education PAC donations increased OVER TIME
  - Younger legislators were elected more recently
  - Pattern reflects time trend, not age effect

**Detection Approach:**
```
When analyst reports correlation:
1. Did both variables change over same time period?
2. Are there unmeasured variables that could drive both?
3. Does correlation remain when controlling for time trends?
4. Would pre/post analysis show same correlation?

If: Both variables trend in same direction over time
AND: No causal mechanism identified
→ FLAG: "Temporal confounding may explain this correlation"
```

**Red Flags:**
- Both variables show strong time trend
- Correlation strongest at aggregate level
- Causation direction unclear
- Time period of analysis captures major external change
- No mechanism by which X causes Y (both might respond to Z)

---

### Bias #3: Simpson's Paradox

**Definition:** A trend observed in aggregate data reverses or disappears when data is disaggregated by relevant subgroup

**Examples:**
- "Republicans receive more education donations" (Aggregate TRUE, but paradox explains it)
  - Aggregate: Republicans get 60%, Democrats get 40%
  - BUT when disaggregated by district:
    - In Republican districts: Democrats get MORE education donations
    - In Democratic districts: Democrats get MORE education donations
  - Pattern reverses! Republicans only get more because they dominate districts with fewer education donors
  
- "PAC donations increase candidate vote share" (Appears TRUE but paradoxical)
  - Aggregate: Higher PAC spend correlates with higher vote %
  - BUT when disaggregated by candidate competitiveness:
    - In competitive races: More PAC $ correlates with LOWER vote %
    - In safe races: More PAC $ correlates with HIGHER vote %
  - Paradox: Safe races get more PAC $ AND higher vote % (both because race was already safe)

- "Older legislators receive more donations" (Aggregate TRUE but paradoxical)
  - Aggregate: Legislators 60+ get $50K avg, legislators 30-40 get $30K avg
  - BUT when disaggregated by seniority/committee:
    - Within same seniority level: Younger get more donations
    - Older appear to get more only because they have more seniority
  - Pattern reverses when confounding variable (seniority) is controlled

**Detection Approach:**
```
When analyst reports aggregate correlation:
1. Is this pattern true for ALL natural subgroups?
2. Or does it reverse in subgroups?
3. What are the natural disaggregation axes?
   - Geographic (by district)?
   - Temporal (by year)?
   - Categorical (by party, by committee, by seniority)?
4. Does correlation hold after disaggregation?

If: Aggregate trend reverses in subgroups
→ FLAG: "Simpson's paradox detected: Disaggregate to understand true pattern"
```

**Red Flags:**
- Pattern only visible at aggregate level
- Proportions within groups oppose overall trend
- Natural grouping variable exists that isn't examined
- Claim seems intuitive but unusual
- Data combines groups with different sizes/compositions

---

## Solution: Bias Detector Agent

### Agent Role

**Name:** Bias Detector  
**Responsibility:** Examine analyst findings for three specific bias patterns  
**Trigger:** When analyst reports any correlation or pattern (not just causation)  
**Output:** Bias assessment with specific warnings if detected

### Bias Detection Process

```
ANALYST: "Pattern X exists in data"
         ↓
BIAS_DETECTOR (automatic check):
├─ Selection bias? [Does pattern hold in all subgroups?]
├─ Temporal confounding? [Could unmeasured time-varying variable explain it?]
└─ Simpson's paradox? [Does disaggregation reverse the trend?]

IF bias detected:
├─ Identify which bias applies
├─ Explain the mechanism
├─ Suggest disaggregation or control approach
└─ Recommend verification method

Output back to analyst with warnings appended
```

### Detection Queries

**For Selection Bias:**
```
Query 1: "Is this pattern visible in all districts/regions?"
Query 2: "Is pattern artifact of geographic concentration?"
Query 3: "Would random sample of full population show same pattern?"
Query 4: "Are there systematic exclusions in data collection?"
```

**For Temporal Confounding:**
```
Query 1: "Did both variables trend upward/downward in same period?"
Query 2: "Are there external events that could drive both?"
Query 3: "Does correlation remain after removing time trend?"
Query 4: "Would lagged analysis show correlation?"
```

**For Simpson's Paradox:**
```
Query 1: "What are natural disaggregation variables?"
    - Geography: Districts, regions, urban/rural
    - Temporal: Years, quarters, election cycles
    - Categorical: Party, committee, seniority, sector
Query 2: "Does trend reverse in any subgroup?"
Query 3: "What is the composition difference between groups?"
Query 4: "Is the aggregation masking a confounding variable?"
```

---

## Integration with Data Analyst

### Current Analyst Workflow

```
USER: "Do education donors prefer Republicans?"
         ↓
ANALYST:
├─ Query FEC for education sector donations
├─ Aggregate by recipient party
├─ Report: "Republicans 60%, Democrats 40%"
├─ Avoid causation: "We don't know if donations cause votes"
└─ Return: Clean finding with no causation claim
```

### New Analyst Workflow with Bias Detection

```
USER: "Do education donors prefer Republicans?"
         ↓
ANALYST:
├─ Query FEC for education sector donations
├─ Aggregate by recipient party
├─ Report: "Republicans 60%, Democrats 40%"
├─ Avoid causation: "We don't know if donations cause votes"
         ↓
BIAS_DETECTOR (automatic):
├─ Check selection bias: "Pattern only in suburban districts"
├─ Check temporal confounding: "Both increased 2022-2026, could be economic"
├─ Check Simpson's paradox: "Within Republican districts, Democrats get more"
         ↓
ANALYST returns to user:
"Republicans 60%, Democrats 40%
 ⚠️ METHODOLOGICAL ALERTS:
 • Selection bias: Educational donors concentrate in suburban/Republican districts
   This may explain preference (geographic, not sectoral)
 • Simpson's paradox detected: 
   Within Republican districts → Democrats receive MORE education donations
   Within Democratic districts → Democrats receive MORE education donations
   Overall pattern reverses when disaggregated by district
 
 Interpretation: The apparent Republican preference is an artifact of
 where educational donors live, not their actual preferences."
```

---

## Three Bias Types: Detailed Specifications

### Selection Bias Specification

**When to Check:**
- Pattern visible in subset of data
- Data collection had systematic boundaries
- Pattern appears stronger than expected

**What to Check:**
```
1. DATA SOURCE EXAMINATION:
   - How was data collected?
   - Were there systematic exclusions?
   - Does it cover full population or subset?
   - Is coverage consistent across groups?

2. SUBGROUP ANALYSIS:
   - Does pattern exist in all geographic regions?
   - Does pattern exist in all demographic groups?
   - Does pattern exist across all time periods?
   - Does pattern persist when sample randomized?

3. COMPARISON TO UNIVERSE:
   - Sample composition vs population composition
   - Any groups over/under-represented?
   - Coverage varies by group?
   - Participation rates vary by group?

4. BIAS PROBABILITY:
   - HIGH: Pattern only visible in one subgroup
   - HIGH: Data collection explicitly excluded groups
   - MEDIUM: Voluntary participation with unequal response rates
   - MEDIUM: Historical data with time-dependent collection
   - LOW: Large randomized sample covering all groups
```

**Red Flags:**
- "This is only in [subset] data"
- "Sample is [type] of donors only"
- "Data only goes back to [year] when collection began"
- "Participation is voluntary"
- "Coverage varies by [demographic]"

**Example Detection:**

```
FINDING: "Education sector donors prefer Republicans (60% vs 40%)"

BIAS_DETECTOR checks:
├─ Source: FEC donor data (comprehensive, not selective) ✓
├─ Geography: 
│  ├─ Northern Virginia: Dems 65%, Reps 35%
│  ├─ Southwest Virginia: Dems 35%, Reps 65%
│  └─ Conclusion: Concentrated in SW (more Republican)
├─ Time: Has collection been consistent? YES ✓
└─ Conclusion: SELECTION BIAS DETECTED

REASON: Educational donors geographically concentrated in 
Republican-leaning areas (SW), making them appear more Republican 
even if sector has no preference.

FIX: Disaggregate by geography to see true sector preference
```

---

### Temporal Confounding Specification

**When to Check:**
- Both variables show trend over time
- Time period captures major event/change
- Mechanism by which X causes Y unclear
- Correlation strongest at aggregate level

**What to Check:**
```
1. TIME TREND ANALYSIS:
   - Does X increase over analysis period?
   - Does Y increase over analysis period?
   - Do they increase at same time?
   - Is there lag between them?

2. EXTERNAL EVENTS:
   - What major events occurred during period?
   - Could events drive both variables?
   - Would each variable respond alone?
   - Is time period coincidental?

3. MECHANISM ANALYSIS:
   - How would X cause Y?
   - Is mechanism plausible?
   - Are there alternative explanations?
   - What other variable could drive both?

4. CONFOUNDING PROBABILITY:
   - HIGH: Both strong upward trend, no mechanism
   - HIGH: Time period includes major external change
   - MEDIUM: Lag between changes unclear
   - MEDIUM: Mechanism plausible but untested
   - LOW: Mechanism clear and verified
   - LOW: Trend exists but independent of timing
```

**Red Flags:**
- "Both increased over 2022-2026"
- "As X went up, Y went up"
- "We don't know why X would cause Y"
- "Could be coincidence of timing"
- "Both responded to external event"

**Example Detection:**

```
FINDING: "As PAC donations increased, yes-votes increased (r=0.75)"

BIAS_DETECTOR checks:
├─ PAC donations: Increased $10M → $50M (2022-2026) ✓
├─ Yes-votes: Increased 45% → 62% (2022-2026) ✓
├─ Timing: Both increased in SAME period (2023-2024) ✓
├─ Mechanism: "More money → more yes-votes?" Unclear
└─ External event: Economic boom 2023-2026
    └─ Could boost: PAC donations (business confidence) AND yes-votes (business-friendly)

CONCLUSION: TEMPORAL CONFOUNDING LIKELY

REASON: Both donations and votes increased during economic boom.
Economic conditions (unmeasured) likely drove both, not one causing other.

FIX: Control for economic indicators or use pre/post analysis
```

---

### Simpson's Paradox Specification

**When to Check:**
- Pattern exists in aggregate data
- Pattern seems unintuitive or unusually strong
- Natural subgroup variables exist
- Data combines groups of different sizes

**What to Check:**
```
1. DISAGGREGATION ANALYSIS:
   - What are natural subgroup variables?
   - Does pattern hold in ALL subgroups?
   - Does pattern reverse in any subgroup?
   - Are subgroups different sizes/compositions?

2. COMPOSITION EFFECT:
   - Which groups contribute most to aggregate?
   - Do those groups drive the pattern?
   - What is composition vs effect size?
   - Is aggregate misleading about within-group effects?

3. CAUSAL STRUCTURE:
   - What is confounding variable Z?
   - Does Z explain aggregate pattern?
   - When Z is controlled, does pattern change?
   - Is aggregate pattern artifact of Z?

4. PARADOX PROBABILITY:
   - HIGH: Pattern reverses in >50% of subgroups
   - HIGH: Subgroups have very different compositions
   - MEDIUM: Pattern weak in some subgroups
   - MEDIUM: Natural subgroup variable exists
   - LOW: Pattern consistent across subgroups
   - LOW: Subgroups similar composition
```

**Red Flags:**
- "This is true overall but..."
- "When you look at [subgroup], it's different"
- "The aggregate hides what's really happening"
- "This is true for Republicans but not Democrats"
- "Different story by district"

**Example Detection:**

```
FINDING: "Older legislators receive more education donations 
          ($50K avg for 60+, $30K avg for 30-40)"

BIAS_DETECTOR checks:
├─ Disaggregate by seniority:
│  ├─ Junior members (same seniority): Age 60+ gets $25K, age 30-40 gets $35K ✓
│  ├─ Senior members (same seniority): Age 60+ gets $45K, age 30-40 gets $40K ✓
│  └─ Pattern REVERSES: Younger get MORE in same seniority level
├─ Confounding variable: Seniority
│  ├─ Older legislators have more seniority (naturally)
│  ├─ Senior members get more donations (regardless of age)
│  └─ Age appears to matter only because it correlates with seniority
└─ Conclusion: SIMPSON'S PARADOX DETECTED

REASON: Aggregate pattern is artifact of composition difference.
Older legislators appear to get more donations only because
they disproportionately occupy senior positions. Within same seniority,
younger legislators actually get more.

FIX: Disaggregate by seniority to see true age effect
```

---

## Bias Detection Integration Points

### Where Bias Detector Activates

```
ANALYST identifies pattern
         ↓
BIAS_DETECTOR automatically checks:
├─ Is this correlation or causation?
├─ Is selection bias present?
├─ Is temporal confounding present?
└─ Is Simpson's paradox present?
         ↓
IF any bias detected:
├─ ALERT user with specific warning
├─ EXPLAIN mechanism of bias
├─ SUGGEST disaggregation approach
└─ RECOMMEND verification method

TRANSPARENCY_MANIFEST includes:
├─ Bias detection status
├─ Any biases found
└─ Suggestions for interpretation
```

### Interaction with Other Fixes

**Bias Detector + Source Conflicts:**
When FEC vs VPAP conflict detected, check for selection bias in one source
```
Example: FEC shows donation, VPAP shows $0
→ Could be VPAP selection bias (incomplete indexing of that donor type)
→ Not a real conflict, but a data collection difference
```

**Bias Detector + Visual Verification:**
Golden Query checks not just data quality but also bias patterns
```
Example: Visualization request for "Age vs donations"
→ Check for Simpson's paradox before rendering
→ If paradox detected: Render disaggregated OR warn user
```

**Bias Detector + Transparency Manifest:**
Manifest includes bias detection status
```
📊 Data Transparency
├─ Sources: FEC (2026-05-24)
├─ Freshness: Current
├─ Bias Analysis:
│  ├─ Selection bias: Detected (geographic concentration)
│  ├─ Temporal confounding: Not detected
│  └─ Simpson's paradox: Detected (reverses by district)
└─ Recommendation: Disaggregate by district for accurate interpretation
```

**Bias Detector + Feedback Loop:**
Users can report bias they notice → Feedback Collector routes to Bias Detector
```
USER: "This pattern seems wrong when I disaggregate by district"
→ FEEDBACK_COLLECTOR: "Missing context issue"
→ Routes to BIAS_DETECTOR
→ BIAS_DETECTOR confirms: Simpson's paradox detected
→ System improves: Now detects this proactively
```

---

## Implementation Specification

### Code Implementation

**File:** voteiq/api/routes/chat.py

**New Agent Definition:**
```python
"bias_detector": {
    "name": "Bias Detector",
    "env": "VOTEIQ_BIAS_DETECTOR_AGENT_ID",
    "tags": ["bias", "statistical-validity", "methodology"],
    "visibility": "public_facing",
    "surface": "Methodology Analysis",
    "prompt": (
        "Your role: Detect three specific statistical biases in analyst findings...
         
         THREE BIASES TO DETECT:
         1. SELECTION BIAS
            Check: Is pattern present in ALL relevant subgroups?
            Or only in non-representative sample?
            Red flags: Data collection with systematic exclusions,
            pattern only in subset, voluntary participation
         
         2. TEMPORAL CONFOUNDING
            Check: Did both variables trend in same period?
            Are there unmeasured third variables driving both?
            Red flags: Both upward trend, no clear mechanism,
            time period includes major external change
         
         3. SIMPSON'S PARADOX
            Check: Does aggregate trend reverse in subgroups?
            Are natural disaggregation variables overlooked?
            Red flags: Pattern only in aggregate, trend reverses
            when disaggregated, confounding variable hidden
         
         PROCESS:
         1. Receive analyst finding (pattern/correlation)
         2. Systematically check all three biases
         3. If bias detected:
            - Identify which bias applies
            - Explain mechanism clearly
            - Suggest disaggregation or control approach
            - Recommend verification method
         4. Return to analyst with bias assessment
         
         OUTPUT FORMAT:
         ✓ No bias detected: [brief statement]
         ⚠️ BIAS DETECTED: [type] - [mechanism] - [fix approach]"
    ),
}
```

**System Prompt Addition:**
```python
"BIAS DETECTION PROTOCOL:
- All analyst findings automatically checked for three biases:
  1. Selection bias (unrepresentative sample)
  2. Temporal confounding (unmeasured time-varying variable)
  3. Simpson's paradox (trend reverses in subgroups)
- If bias detected: Alert user with specific warning
- Include bias analysis in transparency manifest
- Suggest disaggregation or control approaches
- Recommend verification method for user to check themselves"
```

---

## User-Facing Bias Alerts

### Alert Format #1: Selection Bias

```
⚠️ SELECTION BIAS DETECTED

Pattern: Education donors prefer Republicans (60% vs 40%)

Why this matters: Educational donors geographically concentrate 
in Republican-leaning areas (Southwest Virginia). This makes them 
appear more Republican even if they have no sector-based preference.

To interpret correctly:
├─ Disaggregate by geography (region/district)
├─ Check: Do educators prefer Republicans in EACH region?
└─ If: Preference disappears, this was selection bias

Recommendation: Look at Northern VA separately from Southwest VA
```

### Alert Format #2: Temporal Confounding

```
⚠️ TEMPORAL CONFOUNDING POSSIBLE

Pattern: As donations increased, yes-votes increased (r=0.75)

Why this matters: Both donations AND yes-votes increased during 
the 2023-2026 economic boom. Economic conditions may have driven 
both independently, not one causing the other.

To interpret correctly:
├─ Control for economic indicators (GDP, business sentiment)
├─ Pre/post analysis: Did correlation exist before boom?
├─ Check lagged effect: Do donations lead votes by months?
└─ Alternative: Both respond to political environment, not each other

Recommendation: Remove time trend and re-analyze correlation
```

### Alert Format #3: Simpson's Paradox

```
⚠️ SIMPSON'S PARADOX DETECTED

Pattern: Older legislators receive more education donations

Why this matters: When we disaggregate by seniority, the pattern 
REVERSES. Younger legislators within the same seniority level 
actually receive MORE education donations. Older legislators appear 
to get more only because they hold more senior positions.

To interpret correctly:
├─ Don't conclude: "Age causes higher donations"
├─ Real finding: "Seniority correlates with donations"
├─ Age appears to matter only because it correlates with seniority
└─ When seniority is equal: Younger members get more

Recommendation: Disaggregate by seniority or use regression with seniority control
```

---

## Testing & Validation

### Validation Queries

**For Selection Bias Detection:**
```
Test Case 1: Geographic concentration bias
Query: "Education sector donations by party"
Expected: Flag that donors concentrated in SW (Republican area)
Validation: Disaggregate by region confirms

Test Case 2: Voluntary participation bias
Query: "Donor demographics analysis"
Expected: Flag if high non-response rates by demographic
Validation: Response rates vary significantly by group

Test Case 3: Temporal data collection bias
Query: "Historical donation trends"
Expected: Flag if data collection methods changed over time
Validation: Collection boundaries coincide with method change
```

**For Temporal Confounding Detection:**
```
Test Case 1: Economic cycle confounding
Query: "Donations vs voting correlation"
Expected: Flag both increased during economic boom
Validation: Correlation disappears after controlling for GDP

Test Case 2: Policy change confounding
Query: "PAC spending vs vote behavior"
Expected: Flag both changed after 2023 policy shift
Validation: No correlation before policy, strong after

Test Case 3: Secular trend confounding
Query: "Donation growth over decade"
Expected: Flag long-term trend in both variables
Validation: Lag analysis shows no causal relationship
```

**For Simpson's Paradox Detection:**
```
Test Case 1: Committee composition paradox
Query: "Donations increase with seniority"
Expected: Reverses when committee membership controlled
Validation: Within-committee effect opposite to aggregate

Test Case 2: Geographic paradox
Query: "Democrats receive more donations in Republican areas"
Expected: Reverses in disaggregated analysis
Validation: Within-district pattern opposite to overall

Test Case 3: Time period paradox
Query: "Donations correlated with voting"
Expected: Reverses in different time periods
Validation: Effect direction changes year-to-year
```

---

## Success Criteria

- [x] Three bias types specified and detailed
- [x] Detection queries defined for each type
- [x] Integration with analyst workflow documented
- [x] User-facing alerts designed
- [ ] Agent implemented in chat.py
- [ ] System prompt updated
- [ ] Detection logic tested
- [ ] User testing completed
- [ ] Feedback incorporated

---

## Implementation Checklist

- [ ] Create bias_detector agent definition
- [ ] Update analyst to route to bias_detector automatically
- [ ] Update system prompt with bias detection protocol
- [ ] Create detection query implementations
- [ ] Implement user alert templates
- [ ] Add bias analysis to transparency manifest
- [ ] Integration testing with all other agents
- [ ] User testing and refinement
- [ ] Deployment to production

---

## References

- **Detection approach:** Selection bias, temporal confounding, Simpson's paradox (statistical literature)
- **Integration:** Works alongside data_analyst, feeds into transparency_manifest and feedback_collector
- **Output:** Bias analysis appended to findings with specific alerts and recommendations
- **User benefit:** High — prevents misleading interpretations of data patterns

---

## Summary

| Aspect | Status |
|--------|--------|
| **Specification** | ✓ Complete |
| **Three bias types** | ✓ Detailed |
| **Detection approach** | ✓ Defined |
| **User alerts** | ✓ Designed |
| **System integration** | ✓ Documented |
| **Ready for implementation** | ✓ Yes |

---

**Status:** ✓ MANDATE COMPLETE  
**Ready for:** Implementation and testing  
**Impact:** HIGH — Prevents misleading conclusions from biased patterns  
**User Benefit:** Users see bias alerts alongside patterns, can interpret correctly
