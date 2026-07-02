# VoteIQ Implementation Checklist

## PHASE 1: MVP (Civic Chat + Agents) — Weeks 1-4

### Core Infrastructure
- [ ] Tier/subscription system
  - [ ] Database schema for users, subscriptions, tier levels
  - [ ] Enum: free, pro, pro_plus, enterprise
  - [ ] Stripe integration (recurring billing)
  - [ ] Webhook handling for subscription events

- [ ] Agent access control
  - [ ] Define AGENT_TIERS mapping (which agents per tier)
  - [ ] Define USAGE_LIMITS mapping (query limits per tier)
  - [ ] Middleware: Check user tier before allowing agent access
  - [ ] Usage tracking: Count queries per user per day/month
  - [ ] Rate limiting: Enforce tier limits in chat endpoint

- [ ] Chat mode selector UI
  - [ ] Show available agents based on user tier
  - [ ] Gray out unavailable agents (show "upgrade" button)
  - [ ] Display usage (e.g., "95/100 queries left this month")
  - [ ] Mobile responsive

### Civic Chat Features
- [ ] Free tier implementation
  - [ ] analyst (10 queries/day)
  - [ ] support_drafts (unlimited)
  - [ ] "Report data issue" button in UI
  - [ ] Data limits disclaimer

- [ ] Pro tier implementation
  - [ ] analyst (100 queries/day)
  - [ ] news_monitor, field_monitor, data_analyst, visual_explainer, deep_researcher
  - [ ] Usage dashboard
  - [ ] Saved searches (DB + UI)
  - [ ] CSV export (limited - 10/month)

- [ ] Email digest system
  - [ ] news_monitor: Weekly summary email
  - [ ] field_monitor: Weekly summary email
  - [ ] Settings UI (frequency, topics, unsubscribe)
  - [ ] Email templates + Sendgrid integration
  - [ ] Cron job: Send digests weekly

### Billing
- [ ] Stripe setup
  - [ ] Create products: free, pro ($99/yr, $9/mo), pro_plus, enterprise
  - [ ] Setup webhooks: subscription created, updated, deleted, payment failed
  - [ ] Customer portal: Allow users to manage subscriptions

- [ ] User authentication
  - [ ] Login/signup page
  - [ ] Email verification
  - [ ] Password reset
  - [ ] Session management

### Testing
- [ ] Unit tests: tier checking, usage limits
- [ ] Integration tests: Stripe billing flow
- [ ] E2E tests: Free → Pro signup flow

---

## PHASE 2: News + Academic Verticals — Weeks 5-8

### News Chat Vertical
- [ ] Vertical selector on home page
- [ ] News-specific agents
  - [ ] analyst (25/day free, 500/day pro, unlimited pro+)
  - [ ] news_monitor (daily digest in pro)
  - [ ] field_monitor
  - [ ] data_analyst
  - [ ] visual_explainer

- [ ] News-specific features
  - [ ] Fact-checking mode (new agent mode)
    - [ ] UI: Paste claim → verify against records
    - [ ] Output: True/False/Unclear with sources
  - [ ] Story lead alerts
    - [ ] Track certain politicians/bills
    - [ ] Alert when new votes/donations/news
  - [ ] Citation export (APA, Chicago, MLA)
    - [ ] UI: "Export citation" button
    - [ ] Format selection dropdown
  - [ ] Embeddable fact boxes
    - [ ] Generate embed code for articles
    - [ ] Trackable (analytics on embeds)

### Academic Chat Vertical
- [ ] Vertical selector
- [ ] Academic-specific agents (same as news mostly)
- [ ] Academic-specific features
  - [ ] Citation export (APA, Chicago, BibTeX, CSL-JSON)
  - [ ] Statistical confidence scores
    - [ ] Add p-values, confidence intervals to analysis
  - [ ] Data download
    - [ ] CSV, Excel, JSON, STATA formats
  - [ ] Bulk research UI
    - [ ] Upload list of 100+ politicians
    - [ ] Batch analysis
  - [ ] Methodology logging
    - [ ] Track what queries were run
    - [ ] Generate methods section for papers

- [ ] .edu email verification
  - [ ] Sign up with .edu email → auto-pro tier
  - [ ] Verify email domain is real .edu
  - [ ] No credit card required for .edu

### Email/Alerts
- [ ] Story lead alerts (news vertical)
- [ ] Digest scheduling (per vertical)

---

## PHASE 3: Campaign Chat + Enterprise — Weeks 9-12

### Campaign Chat Vertical
- [ ] Vertical selector
- [ ] Campaign-specific agents
  - [ ] analyst (1,000/day)
  - [ ] field_monitor (on-demand + alerts)
  - [ ] data_analyst (unlimited)
  - [ ] visual_explainer
  - [ ] NEW: opposition_research agent

- [ ] Campaign-specific features
  - [ ] Opposition research mode
    - [ ] Deep dive on opponent (votes, donations, positions)
    - [ ] Controversy detection
    - [ ] Media coverage tracking
  - [ ] Voter alignment analysis
    - [ ] Match voter profile to candidate positions
    - [ ] Persuasion scoring
  - [ ] Polling deep dive
    - [ ] Compare to opponent
    - [ ] Compare to national
    - [ ] Trend analysis
  - [ ] Donor tracking
    - [ ] Who funds opponent
    - [ ] Sector breakdown
    - [ ] Individual donor lookups
  - [ ] Vote history analysis
    - [ ] Flip votes (changed position)
    - [ ] Controversial votes
  - [ ] Reports library (weekly email)
  - [ ] Slack integration
    - [ ] Alerts to Slack channel
    - [ ] Breaking news notifications

### Enterprise Tier (All Verticals)
- [ ] API access setup
  - [ ] API key generation
  - [ ] Documentation
  - [ ] Rate limiting (5,000 req/day or contracted)
  - [ ] Usage monitoring

- [ ] Team management
  - [ ] Add team members
  - [ ] Role assignment (admin, editor, viewer)
  - [ ] Billing per seat

- [ ] Custom dashboards
  - [ ] Drag-drop dashboard builder
  - [ ] Widgets for each agent
  - [ ] Save/share dashboards

- [ ] White-label option
  - [ ] Custom domain
  - [ ] Logo/branding
  - [ ] Custom data sources (optional)

### Alerts System (All Verticals)
- [ ] Database: alerts table (user, type, trigger, status)
- [ ] Types: bill vote, donation, polling update, news story, rep action
- [ ] Triggers: Specific politicians, bills, amounts, keywords
- [ ] Delivery: Email, Slack, SMS (enterprise)
- [ ] Real-time processing
  - [ ] Listener: When new data arrives, check alert rules
  - [ ] Notify: Send alert if matches
  - [ ] Dedup: Don't send same alert twice

---

## PHASE 4: Optimization + Launch Prep — Months 4+

### Analytics
- [ ] Google Analytics: User flow, conversion funnel
- [ ] Custom metrics: Usage per tier, agent popularity, retention
- [ ] Churn tracking: Why do users cancel?

### Marketing Setup
- [ ] Landing pages (per vertical)
- [ ] Pricing page
- [ ] Documentation + FAQs
- [ ] Email onboarding sequences
- [ ] Blog integration

### Compliance
- [ ] Terms of Service
- [ ] Privacy Policy
- [ ] Data retention policy
- [ ] GDPR compliance (if EU users)
- [ ] CCPA compliance (if CA users)

### Performance
- [ ] Load testing (1000+ concurrent users)
- [ ] Database query optimization
- [ ] API caching
- [ ] CDN for static assets

### Security
- [ ] Rate limiting per IP
- [ ] DDoS protection
- [ ] SQL injection tests
- [ ] XSS tests
- [ ] CSRF tokens
- [ ] API key rotation

---

## Database Schema (Core Tables)

```sql
-- Users
CREATE TABLE users (
  id UUID PRIMARY KEY,
  email VARCHAR UNIQUE NOT NULL,
  password_hash VARCHAR,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

-- Subscriptions
CREATE TABLE subscriptions (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users,
  vertical VARCHAR ('civic', 'news', 'academic', 'campaign'),
  tier VARCHAR ('free', 'pro', 'pro_plus', 'enterprise'),
  stripe_customer_id VARCHAR,
  stripe_subscription_id VARCHAR,
  status VARCHAR ('active', 'cancelled', 'past_due'),
  current_period_start DATE,
  current_period_end DATE,
  created_at TIMESTAMP,
  cancelled_at TIMESTAMP
);

-- Usage Tracking
CREATE TABLE usage (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users,
  agent VARCHAR ('analyst', 'news_monitor', etc),
  vertical VARCHAR,
  timestamp TIMESTAMP,
  tokens_used INT,
  query_type VARCHAR
);

-- Saved Searches
CREATE TABLE saved_searches (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users,
  vertical VARCHAR,
  name VARCHAR,
  query VARCHAR,
  created_at TIMESTAMP
);

-- Alerts
CREATE TABLE alerts (
  id UUID PRIMARY KEY,
  user_id UUID REFERENCES users,
  type VARCHAR ('bill_vote', 'donation', 'polling', 'news'),
  trigger VARCHAR (the search criteria),
  delivery VARCHAR ('email', 'slack', 'sms'),
  status VARCHAR ('active', 'paused', 'triggered'),
  created_at TIMESTAMP
);

-- Agent Access Control
CREATE TABLE agent_access (
  id UUID PRIMARY KEY,
  tier VARCHAR,
  agent VARCHAR,
  query_limit INT (daily or monthly),
  limit_period VARCHAR ('daily', 'monthly'),
  created_at TIMESTAMP
);
```

---

## Code Structure

```
voteiq/
├── api/
│   ├── routes/
│   │   ├── chat.py (agent orchestration)
│   │   ├── auth.py (login, signup)
│   │   ├── subscriptions.py (billing)
│   │   ├── usage.py (track queries)
│   │   └── alerts.py (notification system)
│   ├── middleware/
│   │   ├── tier_check.py (verify user tier for agent)
│   │   ├── rate_limit.py (enforce usage limits)
│   │   └── auth_required.py (check login)
│   └── models/
│       ├── user.py
│       ├── subscription.py
│       ├── usage.py
│       └── alert.py
├── verticals/
│   ├── civic/
│   │   ├── config.py (agents, limits for civic)
│   │   └── features/ (civic-specific)
│   ├── news/
│   │   ├── config.py
│   │   └── features/ (fact-checking, citations)
│   ├── academic/
│   │   ├── config.py
│   │   └── features/ (methodology, citations)
│   └── campaign/
│       ├── config.py
│       └── features/ (opposition research, polling)
├── agents/
│   ├── analyst/
│   ├── news_monitor/
│   ├── field_monitor/
│   └── ... (other agents)
└── email/
    ├── templates/
    │   ├── news_digest.html
    │   ├── field_monitor_digest.html
    │   └── alert.html
    └── sender.py (Sendgrid)
```

---

## Deployment Checklist

- [ ] Database migrations (set up all tables)
- [ ] Environment variables (Stripe keys, Sendgrid, etc.)
- [ ] Stripe keys (test + production)
- [ ] Email templates uploaded
- [ ] API keys configured
- [ ] SSL certificates
- [ ] CDN configured
- [ ] Monitoring alerts set up
- [ ] Backup system
- [ ] Load balancing configured

---

## Go-Live Checklist

- [ ] All Phase 1 complete + tested
- [ ] Marketing materials ready
- [ ] Email onboarding sequences
- [ ] Support email monitored
- [ ] Monitoring/alerting active
- [ ] Rollback plan documented
- [ ] Customer support trained

---

## Success Metrics

**Phase 1 (4 weeks):**
- ✓ 1K signups to Civic free tier
- ✓ 5% conversion to Pro
- ✓ No critical bugs in chat

**Phase 2 (8 weeks):**
- ✓ News Chat launched
- ✓ 100 news org signups
- ✓ Academic Chat free tier active
- ✓ 200 .edu signups

**Phase 3 (12 weeks):**
- ✓ Campaign Chat launched
- ✓ 20 campaign Pro signups
- ✓ 2 Enterprise deals
- ✓ $50K MRR

---

## Questions?

This checklist is your implementation roadmap. What should we tackle first?
