# Gemini Polling Integration Guide

Your `ingest_va_polls.py` now has **integrated Gemini support** for extracting structured polling data.

## ✅ Quick Start

### **Option 1: News Feeds + Gemini (Recommended & Working)**
Extract polls from news articles using Gemini:

```bash
python ingest_va_polls.py --source news --use-gemini
```

With dry run:
```bash
python ingest_va_polls.py --source news --use-gemini --dry-run
```

**What it does:**
- Fetches Virginia election news from Google News, Virginia Mercury, WVTF, WHRO
- Uses Gemini Flash to extract structured poll data from article text
- Validates politician names against Virginia database
- Stores in `polls.db` automatically
- Available via API at `/api/polls`

---

### **Option 2: Run All Sources (Default)**
```bash
python ingest_va_polls.py
```

Runs: FiveThirtyEight + VoteHub + Ballotpedia + News feeds
*(Excludes Gemini by default - add `--use-gemini` to enable)*

---

### **Option 3: Just News with Gemini**
```bash
python ingest_va_polls.py --source news --use-gemini
```

---

## 🔍 How It Works

1. **News Feeds**: Fetches RSS/Atom feeds from Virginia news sources
2. **Gemini Extraction**: For each matching poll article:
   - Fetches full article text
   - Sends to Gemini 2.5 Flash
   - Extracts: pollster, race, dates, candidates, percentages, sample size, methodology
3. **Validation**: 
   - Fact-checks candidate names against Virginia politician database
   - Rejects if extracted percentages don't appear in article (hallucination guard)
   - Validates dates, percentages are realistic
4. **Storage**: Inserts into `polls` and `poll_results` tables in `polls.db`
5. **API**: Automatically available at `/api/polls`

---

## 📊 Data Model

After ingestion, polls are stored with:

| Field | Example |
|-------|---------|
| `source` | `news/gemini` |
| `pollster` | `Roanoke College IPOR` |
| `race_or_topic` | `Virginia Governor 2025` |
| `field_start` | `2024-11-01` |
| `field_end` | `2024-11-10` |
| `sample_size` | `500` |
| `population` | `Likely voters` |
| `methodology` | `Online` |
| `results` | `[{candidate: "Spanberger", pct: 45.2}, ...]` |
| `url` | Source article link |

---

## 🛠 Advanced Usage

### Custom news feeds:
```bash
python ingest_va_polls.py --source news --use-gemini \
  --news-feed "https://custom-source.com/rss"
```

### Combine sources:
```bash
python ingest_va_polls.py --source fivethirtyeight --source news --use-gemini
```

### Dry run (preview without writing):
```bash
python ingest_va_polls.py --source news --use-gemini --dry-run
```

---

## ⚠️ API Limitations

The `--source gemini` option uses Gemini's Google Search tool directly, but has limitations:
- Google Search tool doesn't support JSON response mode
- Requires text parsing which is less reliable

**Recommendation**: Use `--source news --use-gemini` instead, which:
- ✅ Works reliably with Gemini Flash
- ✅ Extracts from full article text (more context)
- ✅ Includes hallucination guards
- ✅ Better fact-checking
- ✅ Already battle-tested in your codebase

---

## 🔑 Requirements

1. **GEMINI_API_KEY** environment variable set (Gemini API key)
2. **internet connection** (for news feeds + Gemini API)
3. **polls.db** exists (auto-created on first run)

Test your API key:
```bash
echo $GEMINI_API_KEY
```

---

## 📈 Automation (Optional)

Add to crontab to run every 6 hours:

```bash
0 */6 * * * cd /path/to/vriginia-api-election && python ingest_va_polls.py --source news --use-gemini
```

Or schedule once per day at 2 AM:
```bash
0 2 * * * cd /path/to/vriginia-api-election && python ingest_va_polls.py --source news --use-gemini
```

---

## 📱 API Access

After ingestion, query polls via:

```bash
# All Virginia polls
curl "http://localhost:8000/api/polls"

# Filter by office
curl "http://localhost:8000/api/polls?office=governor"

# Limit results
curl "http://localhost:8000/api/polls?limit=10"

# Check ingest status
curl "http://localhost:8000/api/polls-debug"
```

---

## 🐛 Troubleshooting

### No polls found?
```bash
python ingest_va_polls.py --source news --use-gemini --dry-run
```
- Check if news feeds are returning content
- Verify GEMINI_API_KEY is set
- Ensure articles contain poll-related keywords

### Gemini errors?
```bash
# Test your API key
python -c "from google import genai; print('✓ API works')"
```

### Check database:
```bash
sqlite3 polls.db "SELECT COUNT(*) FROM polls"
sqlite3 polls.db "SELECT COUNT(*) FROM poll_articles"
```

---

## 📝 Summary

| Approach | Command | Status |
|----------|---------|--------|
| News + Gemini | `--source news --use-gemini` | ✅ **Working** |
| All sources | (no args) | ✅ Working |
| FiveThirtyEight | `--source fivethirtyeight` | ✅ Working |
| VoteHub | `--source votehub` | ✅ Working |
| Ballotpedia | `--source ballotpedia` | ✅ Working |
| Gemini direct search | `--source gemini` | ⚠️ Limited (not recommended) |

**Start with**: `python ingest_va_polls.py --source news --use-gemini --dry-run`
