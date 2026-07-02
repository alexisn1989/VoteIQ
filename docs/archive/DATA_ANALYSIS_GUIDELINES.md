# VoteIQ Data Analysis Guidelines

## Correlation Strength & Statistical Significance Rules

**EFFECTIVE IMMEDIATELY:** All analysis agents (data_analyst, deep_researcher) must follow strict correlation reporting rules.

---

## The Rule

### ✓ REPORT these correlations:
1. **Strong correlations:** r > 0.60 (strength exceeds 60%)
2. **Statistically significant:** p < 0.05 (5% significance threshold)
3. Both criteria: r > 0.60 AND p < 0.05

### ✗ DO NOT REPORT as findings:
- Weak correlations (r < 0.60)
- Non-significant correlations (p ≥ 0.05)
- Correlations without sample size or methodology

### ⚠️ ACKNOWLEDGE but DON'T EMPHASIZE:
- Weak correlations: Label as "weak correlation (r=0.45, not significant)"
- Exploratory findings: Mark as "preliminary" or "unexplored"

---

## Examples

### ✓ GOOD: Strong, Significant Correlation

```
Education donors align with education votes: 81% overlap
- Donors to education candidates: 487 individuals
- Those candidates' education votes: 234 YES votes (of 289)
- Correlation strength: r=0.81, p<0.001, n=487
- Conclusion: Strong statistically significant alignment

⚠️ Reminder: Correlation does not imply causation.
Donors may support candidates who already hold these positions.
```

### ✗ BAD: Weak Correlation Reported as Finding

```
WRONG:
"Technology donors increasingly support environmental bills"
- Data shows r=0.38 correlation (p=0.08, n=45)
- This is weak and not statistically significant

RIGHT:
"Weak exploratory trend (r=0.38, not statistically significant, n=45)
Additional data would be needed to draw conclusions."
```

### ⚠️ BORDERLINE: Low Sample Size

```
"Preliminary pattern observed in rural districts:
- r=0.72 correlation between logging donations and timber votes
- Sample size: n=12 (very small)
- Confidence: Low — not generalizable beyond these 12 cases
- Recommendation: Collect more rural district data before concluding"
```

### ✓ GOOD: Explicitly Reject Weak Pattern

```
"No significant relationship found between:
- PAC donations and voting patterns (r=0.23, p=0.34, n=156)
- Industry size and donation amounts (r=0.41, p=0.12, n=89)

These weak, non-significant correlations do not warrant interpretation."
```

---

## Statistical Thresholds Explained

### Correlation Coefficient (r)

**Range:** -1.0 to +1.0

| r value | Interpretation | Report? |
|---------|---|---|
| r > 0.80 | Very strong | ✓ YES |
| 0.60 < r ≤ 0.80 | Strong | ✓ YES |
| 0.40 < r ≤ 0.60 | Moderate | ✗ NO (unless p<0.05) |
| 0.20 < r ≤ 0.40 | Weak | ✗ NO |
| r < 0.20 | Very weak | ✗ NO |

**Example:**
```
r=0.75 between education donations and education votes → REPORT
r=0.55 between tech donations and tech votes → BORDERLINE (check p-value)
r=0.35 between healthcare donations and healthcare votes → DON'T REPORT
```

### P-Value (Significance)

**Meaning:** Probability that correlation is due to random chance

| p value | Interpretation | Report? |
|---------|---|---|
| p < 0.01 | Highly significant | ✓ YES |
| 0.01 ≤ p < 0.05 | Significant | ✓ YES |
| 0.05 ≤ p < 0.10 | Marginal (close) | ⚠️ MENTION but don't emphasize |
| p ≥ 0.10 | Not significant | ✗ NO |

**Example:**
```
p<0.001 (correlation is 99.9% reliable) → REPORT
p=0.03 (correlation is 97% reliable) → REPORT
p=0.08 (correlation is 92% reliable) → DON'T report as finding
p=0.50 (correlation is random chance) → DEFINITELY DON'T REPORT
```

### Sample Size (n)

**Rule of thumb:** Larger samples = more reliable correlations

| Sample Size | Reliability | Notes |
|---|---|---|
| n > 100 | Good | Standard threshold for civic data |
| 30 < n ≤ 100 | Fair | Acceptable but mention limitation |
| 10 < n ≤ 30 | Poor | Exploratory only, very limited |
| n < 10 | Very poor | Anecdotal, not generalizable |

**Example:**
```
n=500 legislators' voting records → EXCELLENT
n=85 donors in one sector → ACCEPTABLE
n=18 special election races → EXPLORATORY (mention n in text)
n=3 recent examples → ANECDOTAL (not a statistical finding)
```

---

## What To Include When Reporting Correlations

**Minimum required elements:**

1. **Correlation strength:** r=0.XX (the actual number)
2. **P-value:** p<0.05 or p=0.XX
3. **Sample size:** n=XXX (how many records)
4. **Methodology:** How was correlation calculated?
5. **Confidence interval:** Range of uncertainty (optional but recommended)
6. **Limitations:** Confounders, data gaps, potential biases
7. **Disclaimer:** "Correlation does not imply causation"

**Template:**
```
[Finding statement]
- Correlation: r=[strength], p=[significance], n=[sample size]
- Methodology: [Pearson? Spearman? Which fields?]
- Confidence Interval: [CI if available]
- Limitations: [Data gaps? Confounders?]
- Reminder: Correlation does not imply causation.
```

---

## Special Cases

### 1. **Categorical Data (Voting Patterns)**

When analyzing voting alignment (Yes/No/Abstain):

**Do:** Use percentage overlap with confidence intervals
```
"78% of votes align with party position (95% CI: 74-82%, n=456)"
```

**Don't:** Treat as numeric correlation
```
✗ "r=0.78 party alignment"
```

### 2. **Trending Data (Over Time)**

When analyzing changes across time:

**Do:** Report trend strength with p-value
```
"Education donations increase 5% per year (slope=5.2, p=0.002, R²=0.43, n=10 years)"
```

**Don't:** Assume recent change = significant change
```
✗ "Donations went up last year so there's a trend"
```

### 3. **Geographic Patterns (Regional)**

When analyzing state vs district vs locality:

**Do:** Include geographic breakdown
```
"Urban district correlation: r=0.71, p<0.01, n=42
Rural district correlation: r=0.22, p=0.31, n=18
Conclusion: Pattern strong in urban areas only"
```

**Don't:** Combine geographies if they differ
```
✗ "Overall correlation r=0.62" (masks regional differences)
```

### 4. **Confounding Variables**

When correlation might have hidden causes:

**Do:** Acknowledge and investigate
```
"Apparent education vote/donation correlation (r=0.68) is likely explained by:
- District demographics (more college-educated districts → more education funding sought)
- Committee assignments (education committee members both donate and vote on education)
- We cannot isolate the causal direction from this data alone."
```

**Don't:** Claim causation
```
✗ "Donations drive education votes"
```

---

## Rule Enforcement

### For Agents

**data_analyst:** Must enforce >60% threshold, cite p-values
**deep_researcher:** Must note when correlations are weak, explain limitations
**analyst:** Doesn't report correlations (facts only)
**news_monitor:** Doesn't report statistical findings (news only)

### For Code Review

Before deploying analysis:

- [ ] All reported correlations r > 0.60 OR p < 0.05
- [ ] Sample size (n=) included
- [ ] P-value included
- [ ] Methodology explained
- [ ] Disclaimer present: "Correlation does not imply causation"
- [ ] Weaker correlations noted but not emphasized
- [ ] No causal language ("caused," "drove," "forced")

### For Users

If you see a correlation without these elements, question it:

- "What's the sample size?"
- "Is this statistically significant?"
- "Could other factors explain this?"
- "Are they claiming causation?"

---

## Common Mistakes to Avoid

### ❌ Mistake 1: Cherry-Picking Strong Patterns

```
WRONG:
"Education donors give to education candidates (r=0.80)
Healthcare donors give to healthcare candidates (r=0.75)
We found clear special interest patterns!"

RIGHT:
"Some sector alignment observed (education: r=0.80, healthcare: r=0.75).
We tested 14 sectors; these 2 showed strong alignment.
Remaining 12 showed weak correlation (r<0.50).
This could indicate genuine sector focus OR selection bias in candidate recruitment."
```

### ❌ Mistake 2: Ignoring Sample Size

```
WRONG:
"87% of IT donations go to tech-focused candidates (r=0.92)"
[But n=8 total IT donors]

RIGHT:
"Strong alignment in IT sector (r=0.92) among limited sample (n=8).
Larger sample needed to confirm whether this is a real pattern or random variation."
```

### ❌ Mistake 3: Confusing Strength with Significance

```
WRONG:
"Large correlation (r=0.35) between donation size and votes received"

RIGHT:
"Weak correlation (r=0.35, p=0.45, n=200) — not statistically significant.
Insufficient evidence to claim relationship between donation size and voting."
```

### ❌ Mistake 4: Causal Language

```
WRONG:
"Large donations caused support for the bill"
"Education money influenced education votes"
"PAC contributions drove voting behavior"

RIGHT:
"Donations and votes align (r=0.71, p<0.05)"
"Possible explanations: candidates with education focus attract education donors; education committee members both vote and receive education donations; or other confounders"
```

### ❌ Mistake 5: Reporting Without Caveats

```
WRONG:
"GOP votes strongly correlate with corporate donations"

RIGHT:
"GOP votes show higher correlation with corporate donations (r=0.62, p<0.05) vs Democratic votes (r=0.38, p=0.12).
Note: Party affiliation is a confounder. We cannot determine whether the party or donation type drives votes."
```

---

## FAQ

**Q: What if I have a really interesting pattern at r=0.58?**
A: Note it as "near-threshold correlation (r=0.58)" and either:
- Collect more data to strengthen it
- Investigate potential explanations
- Label as exploratory/preliminary

**Q: What about qualitative patterns (anecdotal)?**
A: Anecdotes are valuable for storytelling but aren't statistical findings. Label them:
```
"While most donors focus on their sector, notable exceptions include:
[anecdotal examples]
These are examples, not generalizable patterns."
```

**Q: Can I combine multiple weak correlations?**
A: Generally no. If individual correlations are weak (r<0.60), combining them doesn't strengthen the claim. However, if multiple independent studies all show r>0.60 p<0.05, then you can say "multiple studies confirm..."

**Q: What if the data source doesn't provide p-values?**
A: Calculate them or note the limitation:
```
"Observed pattern: r=0.71 (p-value not available in source data)
Sample size sufficient (n=245) but without p-value, cannot confirm significance."
```

**Q: How do I report "no relationship found"?**
A: Clearly and definitively:
```
"No significant correlation found between [variable A] and [variable B] (r=0.18, p=0.42, n=167).
Data do not support a relationship."
```

---

## Resources

- **Statistical Primer:** See data_analyst agent documentation
- **Python Example:** Calculate correlation with scipy.stats.pearsonr()
- **R Example:** cor.test() function for p-values
- **Excel:** CORREL() for r, =CORREL()+T-test for p-value

---

## Implementation Checklist

- [x] Rule documented (r>0.60 OR p<0.05)
- [x] Examples provided (good vs bad)
- [x] Common mistakes listed
- [x] Updated data_analyst agent prompt
- [x] Updated deep_researcher agent prompt
- [x] Updated SYSTEM_PROMPT
- [x] Updated AGENT_DATA_SOURCES.md
- [ ] Test with real analysis workflows
- [ ] Train team on new standards
- [ ] Add to code review checklist

---

## Questions?

Reference the examples in this document or contact the VoteIQ team with specific correlation questions before publishing.

**Remember:** Weak correlations aren't bad — they're just weak. Acknowledge them, report exact values, and don't oversell them.
