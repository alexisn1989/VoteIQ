# Bias Detection — Quick Reference

## The Rule

**After analyst reports a pattern, automatically check for three statistical biases:**
1. **Selection Bias** — Pattern only in non-representative sample?
2. **Temporal Confounding** — Both variables trending in same period?
3. **Simpson's Paradox** — Trend reverses when disaggregated?

---

## Three Biases at a Glance

| Bias | What Happens | Red Flags | Fix |
|------|--------------|-----------|-----|
| **Selection Bias** | Pattern appears stronger in biased sample | Data collection has systematic exclusions; pattern only in subset | Disaggregate to full population |
| **Temporal Confounding** | Third variable drives both variables at same time | Both increased 2022-2026; no mechanism; major external change | Control for time trend or confounding variable |
| **Simpson's Paradox** | Aggregate trend reverses in subgroups | Pattern only in aggregate; reverses by district/demographic | Disaggregate to see true pattern |

---

## Selection Bias

**Definition:** Data collection methodology systematically excludes groups, making pattern appear stronger

### Example
```
FINDING: "Education donors prefer Republicans (60% vs 40%)"

BIAS CHECK:
├─ Is this true in ALL districts? NO
│  ├─ Northern Virginia: Democrats 65%, Republicans 35%
│  ├─ Southwest Virginia: Democrats 35%, Republicans 65%
│  └─ Reason: Educational donors concentrate in SW (Republican area)
└─ RESULT: Selection bias — not donor preference, geographic concentration
```

### Red Flags
- ✗ Pattern only in certain subgroups
- ✗ Data collection had systematic exclusions
- ✗ Voluntary participation with unequal response rates
- ✗ Historical data with time-dependent collection start
- ✗ Sample clearly non-representative

### How to Fix
```
Step 1: Identify natural subgroups
        (geography, demographics, time period)

Step 2: Check pattern in EACH subgroup

Step 3: If pattern disappears or reverses
        → It was selection bias
        
Step 4: Report: "Pattern is geographic, not sectoral"
```

---

## Temporal Confounding

**Definition:** Third variable changes over time and influences both variables independently

### Example
```
FINDING: "As donations increased, yes-votes increased (r=0.75)"

BIAS CHECK:
├─ Both variables trend upward 2022-2026? YES
├─ Economic boom also 2023-2026? YES
├─ Mechanism: Does PAC $ cause yes-votes? UNCLEAR
└─ RESULT: Temporal confounding — both respond to economic conditions
```

### Red Flags
- ✗ Both variables show strong time trend
- ✗ Both increased during same period
- ✗ No clear mechanism by which X causes Y
- ✗ Time period includes major external event
- ✗ Could be coincidence of timing

### How to Fix
```
Step 1: Identify potential confounding variables
        (economic conditions, policy changes, etc.)

Step 2: Control for the confounding variable
        in statistical analysis

Step 3: Or use pre/post analysis
        (correlation before vs after event)

Step 4: Report: "Both increased during economic boom;
                 causation unclear"
```

---

## Simpson's Paradox

**Definition:** Aggregate trend reverses or disappears when data is disaggregated by relevant subgroup

### Example
```
FINDING: "Older legislators receive more education donations ($50K vs $30K)"

BIAS CHECK:
├─ Disaggregate by seniority:
│  ├─ Junior level: Younger get $35K, Older get $25K ← REVERSES
│  └─ Senior level: Younger get $40K, Older get $45K ← Close
├─ Pattern: Older ONLY appear higher because of seniority
└─ RESULT: Simpson's paradox — age effect is seniority effect
```

### Red Flags
- ✗ Pattern only visible in aggregate data
- ✗ Pattern seems intuitive but unusual
- ✗ Natural disaggregation variable exists (district, committee, etc.)
- ✗ Subgroups have very different compositions
- ✗ Trend REVERSES in subgroups

### How to Fix
```
Step 1: Identify natural subgroup variables
        (geography, demographics, seniority, etc.)

Step 2: Disaggregate and re-analyze

Step 3: Check: Does pattern hold in ALL subgroups?

Step 4: If pattern reverses:
        Report: "True effect is [subgroup effect],
                 not what aggregate suggests"
```

---

## Detection Process Flow

```
ANALYST REPORTS PATTERN
        ↓
BIAS_DETECTOR (automatic):
        ↓
CHECK 1: Selection Bias?
├─ Is pattern in ALL subgroups?
├─ Or only in non-representative sample?
└─ FLAG if: Pattern only in subset
        ↓
CHECK 2: Temporal Confounding?
├─ Do both variables trend same period?
├─ Is third variable driving both?
└─ FLAG if: Both trending, no mechanism
        ↓
CHECK 3: Simpson's Paradox?
├─ Does aggregate trend reverse in subgroups?
├─ Is confounding variable hidden?
└─ FLAG if: Aggregate misleads about within-group effect
        ↓
RESULT:
├─ ✓ No bias detected: Return finding as-is
└─ ⚠️ Bias detected: Return with warnings + fix suggestions
```

---

## Alert Templates

### Selection Bias Alert
```
⚠️ SELECTION BIAS DETECTED

Pattern: [description]

Why this matters: [How selection bias affects the pattern]

To verify: Disaggregate by [geography/demographic/time period]

Expected finding: [What correct analysis would show]
```

### Temporal Confounding Alert
```
⚠️ TEMPORAL CONFOUNDING POSSIBLE

Pattern: [description]

Why this matters: Both variables increased [same time period];
                 unmeasured variable [Z] may drive both

To verify: Control for [confounding variable]
           OR use pre/post analysis

Expected finding: [What correlation would be after control]
```

### Simpson's Paradox Alert
```
⚠️ SIMPSON'S PARADOX DETECTED

Pattern (aggregate): [description]

Reality (disaggregated):
├─ In [subgroup A]: [opposite/different pattern]
├─ In [subgroup B]: [opposite/different pattern]
└─ Conclusion: Aggregate misleads

To verify: Disaggregate by [variable]

True finding: [What pattern really is]
```

---

## Integration with Other Fixes

### Bias Detector + Data Analyst
- Analyst reports pattern
- Bias Detector automatically checks
- Alert appended if bias found
- User sees pattern AND potential biases

### Bias Detector + Transparency Manifest
```
📊 Data Transparency
├─ Sources: FEC (2026-05-24)
├─ Freshness: Current
├─ Bias Analysis:
│  ├─ Selection bias: DETECTED (geographic concentration)
│  ├─ Temporal confounding: Not detected
│  └─ Simpson's paradox: DETECTED (disaggregate by district)
└─ Recommendation: [How to interpret correctly]
```

### Bias Detector + Visual Verification
- Golden Query checks not just quality but also biases
- Prevents weak-methodology visualizations
- Alerts user if bias detected
- Suggests disaggregated visualization

### Bias Detector + Feedback Loop
- Users report potential biases
- Feedback routes to Bias Detector
- Detector confirms/refutes
- System learns to detect pattern proactively

---

## User Questions Answered

```
"Is this pattern real or an artifact?"
→ Bias analysis reveals selection bias, temporal confounding,
  or Simpson's paradox if present

"Why does disaggregation show different results?"
→ Bias detector explains Simpson's paradox mechanism

"Could both variables be responding to same external event?"
→ Bias detector identifies temporal confounding

"Am I seeing a real pattern or data collection bias?"
→ Bias detector distinguishes true pattern from bias
```

---

## When to Check

**Always check for bias when:**
- ✓ Analyst reports correlation
- ✓ Pattern is reported as surprising or significant
- ✓ Causation might be inferred
- ✓ Policy decisions could be based on pattern
- ✓ Multiple subgroups exist but aren't examined

**Don't need to check:**
- Single fact ("How much did X donate?")
- Explicit causation already ruled out
- Simple aggregation with no inference

---

## What Users See

### Before Bias Detection
```
"Education donors prefer Republicans: 60% vs 40%"
[User might conclude: Education sector leans Republican]
```

### After Bias Detection
```
"Education donors prefer Republicans: 60% vs 40%

⚠️ BIAS ALERT: Selection bias detected
Educational donors concentrated in Republican-leaning areas.
Apparent preference is geographic, not sectoral.

Verification: Look at results by district (not aggregate)"

[User can now interpret correctly]
```

---

## Example: Complete Bias Analysis

**FINDING:** "Younger legislators have higher education PAC donations"
- Age 30-40: $35K average
- Age 50-60: $30K average
- Age 60+: $50K average

**BIAS CHECK #1: Selection Bias?**
```
✓ Check: Data covers all legislators
✓ Coverage: Consistent across age groups
✓ Participation: All public FEC records (not voluntary)
→ RESULT: No selection bias detected
```

**BIAS CHECK #2: Temporal Confounding?**
```
✗ Check: Do donations AND age trends align?
  Donations: Increased 2022-2026
  Age: No trend (constant)
✓ Different timing
→ RESULT: No temporal confounding detected
```

**BIAS CHECK #3: Simpson's Paradox?**
```
✗ Check: Does pattern reverse in subgroups?
  Among junior legislators:
  ├─ Age 30-40: $15K
  ├─ Age 50-60: $12K
  ├─ Age 60+: $8K
  └─ Pattern SAME (younger get more)
  
  Among senior legislators:
  ├─ Age 30-40: $55K
  ├─ Age 50-60: $48K
  ├─ Age 60+: $92K
  └─ Pattern SAME (age 60+ get more)

WAIT: Why age 60+ high in senior but not junior?
→ CONFOUNDING FOUND: Seniority, not age

Disaggregate more carefully:
Same seniority level:
├─ Junior: Younger get $15K, Older get $8K
├─ Mid: Younger get $35K, Older get $30K
└─ Senior: Younger get $55K, Older get $52K

Within same seniority: YOUNGER get more
Aggregate hides: Older concentrated in senior roles

→ RESULT: Simpson's paradox detected
```

**FINAL ALERT:**
```
⚠️ SIMPSON'S PARADOX DETECTED

Finding: Older legislators get more donations

Reality: When we control for seniority level:
├─ Junior members: Younger get more
├─ Mid-career: Younger get more
└─ Senior members: Younger get more slightly more

Pattern appears reversed only because older legislators
hold more senior positions, which attract more donations.

Age effect: YOUNGER get more (opposite to aggregate)
Real driver: SENIORITY gets more donations

Recommendation: Don't conclude "Age attracts donations"
             Instead: "Seniority attracts donations,
                       and age correlates with seniority"
```

---

## Implementation Status

- [x] Specification complete
- [ ] Agent implemented
- [ ] Detection logic coded
- [ ] Integration testing
- [ ] User testing
- [ ] Production deployment

---

**Status:** ✓ Ready for implementation  
**Trigger:** Automatic after analyst reports pattern  
**Output:** Bias analysis with specific alerts and recommendations  
**User Impact:** HIGH — Prevents misleading interpretations
