# Correlation Reporting Rule — Quick Reference

## The Rule

```
✓ Report if: r > 0.60  OR  p < 0.05
✗ Don't report if: r < 0.60  AND  p ≥ 0.05
⚠️ Acknowledge but flag limitations if: borderline cases
```

---

## Decision Matrix

| r value | p-value | Report? | Example |
|---------|---------|---------|---------|
| r=0.85 | p<0.001 | ✓ YES | Strong education donor/vote alignment |
| r=0.70 | p=0.01 | ✓ YES | Significant sector overlap |
| r=0.55 | p<0.05 | ✓ YES (borderline) | Note as "just above threshold" |
| r=0.45 | p<0.05 | ✓ YES (due to p) | Technically significant, but weak |
| r=0.65 | p=0.08 | ✗ NO | Strong but not significant (n too small?) |
| r=0.35 | p=0.50 | ✗ NO | Weak and non-significant |
| r=0.58 | p=0.12 | ✗ NO | Near threshold but not quite |

---

## What to Write

### ✓ Strong, Significant
```
Education donors align with education votes (r=0.81, p<0.001, n=234)
Correlation does not imply causation.
```

### ⚠️ Weak but Significant
```
Modest alignment observed (r=0.42, p=0.02, n=156)
Note: Small effect size despite statistical significance.
Correlation does not imply causation.
```

### ✗ Weak & Non-Significant
```
No meaningful relationship (r=0.28, p=0.34, n=89)
Data insufficient to support a pattern.
```

---

## Checklists

### Before Publishing Any Correlation

- [ ] r value calculated (e.g., r=0.71)
- [ ] p-value calculated (e.g., p<0.05)
- [ ] r > 0.60 OR p < 0.05?
- [ ] Sample size included (n=)
- [ ] Methodology stated
- [ ] Limitation noted (e.g., "small sample," "confounded by X")
- [ ] Disclaimer: "Correlation does not imply causation"
- [ ] No causal language ("caused," "drove," "forced")

### Quick Check

```
Does correlation meet threshold?
  r > 0.60? → YES → REPORT
  p < 0.05? → YES → REPORT (even if r<0.60, note it's weak)
  Both NO? → DON'T REPORT as finding
```

---

## Common Values

| Strength | r value | Interpretation |
|----------|---------|---|
| Perfect | 1.0 | (theoretical only) |
| Very Strong | 0.80+ | Report immediately |
| Strong | 0.60-0.79 | Report |
| Moderate | 0.40-0.59 | Don't report unless p<0.05 |
| Weak | 0.20-0.39 | Don't report |
| Very Weak | <0.20 | Don't report |

---

## Examples from VoteIQ

### ✓ Report These

```
"Healthcare PACs donate more to healthcare committee members
- Correlation: r=0.74
- P-value: p<0.001
- Sample: n=89 PACs, 203 members
→ REPORT: Strong, statistically significant pattern"
```

### ✗ Don't Report These

```
"Tech donations may influence tech votes
- Correlation: r=0.38
- P-value: p=0.11
- Sample: n=45
→ DON'T REPORT: Weak correlation, not significant
→ SAY INSTEAD: 'Insufficient evidence of pattern'"
```

### ⚠️ Acknowledge These

```
"Geographic clustering in rural districts
- Correlation: r=0.69
- P-value: p<0.05
- Sample: n=12 (very small)
→ REPORT as: 'Possible pattern (r=0.69, n=12)
   Larger sample needed to confirm.'"
```

---

## Code Hints

### Python (scipy)
```python
from scipy.stats import pearsonr

r, p = pearsonr(donations, votes)
if r > 0.60 or p < 0.05:
    print(f"Report: r={r:.2f}, p={p:.4f}, n={len(donations)}")
else:
    print(f"Weak/not significant: r={r:.2f}, p={p:.4f}")
```

### R
```r
result <- cor.test(donations, votes)
r <- result$estimate
p <- result$p.value

if (r > 0.60 | p < 0.05) {
  cat(sprintf("Report: r=%.2f, p=%.4f\n", r, p))
} else {
  cat("Weak/not significant\n")
}
```

### Excel
```
CORREL(range1, range2)  → gives r value
Use T.DIST() for p-value
```

---

## When in Doubt

**Ask these questions:**

1. Is r > 0.60? (Is it strong?)
2. Is p < 0.05? (Is it significant?)
3. Is n > 30? (Sufficient sample?)
4. Are there confounders? (Other explanations?)
5. Does it make practical sense? (Not just statistical accident?)

**If all = YES:** Report
**If any = NO:** Flag limitation or don't report

---

## Definitions

**Pearson's r (correlation coefficient)**
- Range: -1 to +1
- -1: Perfect negative relationship
- 0: No relationship
- +1: Perfect positive relationship
- 0.60: Our threshold for "strong"

**P-value (significance)**
- Probability result is due to chance
- p<0.05: Less than 5% chance it's random
- p<0.01: Less than 1% chance it's random
- p<0.001: Less than 0.1% chance it's random
- Our threshold: p<0.05

**Sample size (n)**
- Number of data points in analysis
- Larger = more reliable correlation
- Small samples (n<30) less trustworthy
- Note n in all reports

---

## One-Liners

```
r > 0.60 → Strong correlation, likely report
p < 0.05 → Statistically significant, likely report
Both → Definitely report
Neither → Don't report as finding
r high + p high → Strong but fluke (collect more data)
r low + p low → Weak pattern (confirm with more data)
```

---

## Final Checklist Before Publishing

```
Correlation Finding Template:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[What correlates with what?]
- Strength: r=__ (>0.60 for strong)
- Significance: p=__ (<0.05 for significant)
- Sample: n=__ (>30 preferred)
- Methodology: [How calculated?]
- Limitations: [What confounds? Data gaps?]
- Disclaimer: Correlation does not imply causation
- Context: [What's the real-world story?]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

**Rule in effect:** May 24, 2026  
**All analysis agents comply**  
**Questions?** See DATA_ANALYSIS_GUIDELINES.md
