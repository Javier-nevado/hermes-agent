---
name: onboard
description: "Customer onboarding — 3-phase business interview, heartbeat setup, and system connections. Triggered on first session or via /onboard."
version: 1.0.0
metadata:
  hermes:
    tags: [onboarding, setup, business-context, configuration, customer, identity]
    category: core
---

# Onboard

> Customer onboarding — learn the business, configure heartbeat, connect their systems

## Purpose

Three-phase onboarding conducted by Opteia personnel together with the customer.
AI provider and Telegram are configured by Opteia on the backend separately.

Triggered automatically on first session or manually via `/onboard`.

---

## Phase 1: Business Interview

> Performed by Opteia personnel together with the customer.

Proceed through these **9 sections** one at a time. Be conversational — ask follow-up
questions naturally. Don't dump all questions at once. Adapt based on what the user shares.

### Section 1: Business Identity
- What is the company name?
- Where is the business located?
- What industry/sector?
- How many employees?
- What is the company's mission or purpose in one sentence?
- How long has the company been operating?

### Section 2: The User
- What is your name and role?
- What are you responsible for day-to-day?
- What's your professional background?
- How do you prefer to communicate? (brief/async vs detailed/sync)
- What are your biggest time wasters?

### Section 3: Strategic Goals
- What are the top 3 goals for this year?
- What does success look like in 12 months?
- Who are your main competitors or alternatives?
- What makes your business different / unique advantage?
- What market or customer segment do you focus on?

### Section 4: Products & Services
- What do you sell? (products, services, subscriptions)
- What is your pricing model?
- How do you deliver your service?
- What is the typical sales cycle?
- What is your best-selling offering?

### Section 5: Team & Organization
- How is the team structured? (departments, roles)
- Who are the key people? (names, roles, responsibilities)
- Who reports to whom?
- Are there any contractors or external partners?
- What is the biggest team challenge?

### Section 6: Customers
- Who is your ideal customer? (size, industry, location)
- How do customers find you? (channels)
- What is the customer onboarding process?
- How do you handle support?
- What do customers complain about most?

### Section 7: Processes & Systems
- What tools/software do you currently use? (CRM, email, accounting, project management, etc.)
- What are the most repetitive tasks?
- What manual processes take the most time?
- Where do things fall through the cracks?
- What integrations between tools do you wish you had?

### Section 8: ABI Expectations
- What do you most want ABI to help with?
- What tasks would you like automated?
- What information should ABI always remember?
- What should ABI never do?
- How will you measure if ABI is valuable?

### Section 9: Agent Naming

Now that you know the business, the people, and the expectations — suggest a name for yourself.

**Say something like:** "Now that I know you and your business, I'd like to suggest a name for myself. Based on what I've learned about <company>, I was thinking **<suggested_name>**. What do you think?"

Guidelines for suggesting a name:
- Draw inspiration from the company name, industry, or values
- Keep it short, memorable, and professional
- Offer 2-3 alternatives if they're unsure
- If they have their own idea, go with it
- If they say "I don't care", pick the one you think fits best

After choosing, store to memory AND write your identity file:

**Store to Core Memory:**
```
Tool: memory_remember
Args:
  content: "My name is <chosen_name>. I am the AI agent for <company>."
  source_type: "onboarding"
  metadata: {"category": "agent_identity", "importance": "high"}
```

**Write your identity.md file:**
```
Tool: write_file
Args:
  path: "agent/identity.md"
  content: |
    # <chosen_name> — Identity

    > Self-written after onboarding for <company>.

    ## Who I Am

    I am <chosen_name> — the AI agent for <company>, a <industry> company in <location>.
    I help <primary_user_name> with <key responsibilities based on Section 2>.

    ## Personality

    - <personality traits based on user communication preferences from Section 2>
    - <adapted to company culture from Section 1>

    ## Core Memory Pointers

    Key context to recall on startup:
    - `identity` — who I am, my name, my role
    - `business_identity` — <company> details, industry, goals
    - `user_profile` — <primary_user_name>, their preferences
    - `strategy` — top goals and targets
    - `systems` — connected tools and integrations
    - `active_tasks` — current priorities

    ## Key Episodes

    | Date | Episode | Learning |
    |------|---------|----------|
    | <today> | Completed onboarding | Learned about <company>, <industry>, key goals: <top 3 goals> |

    ## Growth Areas

    - <areas for improvement based on Sections 7-8 — what to get better at>
```

From this point on, introduce yourself as **<chosen_name>** in every session.

### Memory Storage

After **each section**, store the collected information via the memory tool:

```
Tool: memory_remember
Args:
  content: "<concise fact about the business>"
  source_type: "onboarding"
  metadata: {"category": "<category>"}
```

| Category | Section | Example |
|----------|---------|---------|
| `business_identity` | 1 | "Acme Ltd is a Malta-based IT services company, 15 employees" |
| `user_profile` | 2 | "CEO John Smith, prefers brief async communication" |
| `strategy` | 3 | "Target: 20% revenue growth in 2026, focus on AI services" |
| `products` | 4 | "Main product: AI consulting package at 500/month" |
| `team` | 5 | "Team: 3 developers, 1 sales, 1 admin. Lead dev: Maria" |
| `customers` | 6 | "Ideal customer: SMB with 10-50 employees in Malta" |
| `systems` | 7 | "Uses: Xero for accounting, Gmail for email, no CRM" |
| `abi_expectations` | 8 | "Priority: automate inbox, weekly reporting, meeting prep" |
| `agent_identity` | 9 | "My name is Atlas. I am the AI agent for Acme Ltd." |

**Best practices:**
- One fact per memory entry
- Be specific with names, numbers, dates
- Include context and reasoning
- Use high importance for critical business facts

**Create entities after sections 1, 2, and 5:**

After Section 1 (company):
```
Tool: memory_remember
Args:
  content: "Company: <name>, <industry>, <location>, <employee count>"
  source_type: "onboarding"
  metadata: {"category": "business_identity", "entity_type": "company"}
```

After Section 2 (person):
```
Tool: memory_remember
Args:
  content: "User: <name>, <role>, <communication preference>"
  source_type: "onboarding"
  metadata: {"category": "user_profile", "entity_type": "person"}
```

### Interview Completion

After all 9 sections:

1. **Store onboarding episode:**
```
Tool: memory_remember
Args:
  content: "Completed business onboarding for <company>. Key facts: <summary>"
  source_type: "onboarding"
  metadata: {"category": "onboarding_complete", "episode_type": "onboarding"}
```

2. **Verify context loads correctly:**
```
Tool: memory_recall
Args:
  query: "<company name> business overview"
```

3. **Transition:** "Great, I have a solid picture of your business. Let's set up your heartbeat schedule and then connect your systems."

---

## Phase 2: Heartbeat Setup

Configure ABI's proactive monitoring schedule with the customer.

**Ask:** "ABI can proactively check on things throughout the day. How often would you like it to run?"

Default: every 30 minutes during business hours (8am-10pm). Customize as needed:

```bash
echo "ABI_HEARTBEAT_ENABLED=true" >> .env
echo "ABI_HEARTBEAT_INTERVAL=30" >> .env
```

Discuss with the customer:
- What should ABI monitor during heartbeat? (use expectations from Section 8)
- Any quiet hours?
- Should heartbeat be active on weekends?

Store heartbeat preferences to memory:
```
Tool: memory_remember
Args:
  content: "Heartbeat preferences: <interval>, <hours>, <monitoring focus>"
  source_type: "onboarding"
  metadata: {"category": "heartbeat_config"}
```

---

## Phase 3: Connect Customer Systems

Using the tools/software identified in **Section 7**, connect ABI to the customer's
existing systems. Walk through each relevant integration.

For each system the customer uses:

1. **Confirm access:** Do they have API credentials, admin access, or OAuth apps?
2. **Guide setup:** Help them create API keys or OAuth apps as needed
3. **Test connection:** Verify ABI can reach the service
4. **Store credentials:** Save to `.env` and note in memory which systems are connected

Common integrations:
- **CRM** (HubSpot, Salesforce, Pipedrive) → API key
- **Accounting** (Xero, QuickBooks) → OAuth app
- **Email** (Gmail, Outlook) → OAuth or app password
- **Project Management** (Jira, Asana, Monday) → API token
- **Storage** (Google Drive, OneDrive, SharePoint) → OAuth app

Store connected systems:
```
Tool: memory_remember
Args:
  content: "Connected system: <name>, access method: <method>, status: <connected/pending>"
  source_type: "onboarding"
  metadata: {"category": "systems_connected"}
```

### Phase 3 Summary

```
Connected Systems:
  CRM:          [connected / pending]
  Accounting:   [connected / pending]
  Email:        [connected / pending]
  Project Mgmt: [connected / pending]
  Storage:      [connected / pending]
```

---

## Completion

After all 3 phases:

1. **Restart the agent** to pick up new config:
```bash
sudo systemctl restart abi-agent-api
```

2. **Tell the customer:**
   - Summary of what was collected and connected
   - How to interact via Telegram (send a test message)
   - "Just tell me to remember something anytime to add context"
   - "Run /onboard again if you need to update anything"

3. **Notify Opteia team** that onboarding is complete for this customer.

---

## Notes

- AI provider and Telegram are configured by Opteia personnel on the backend before customer onboarding
- If the user skips a section, move on — can revisit later
- If memory API is not running, store memories in a local file
- Interview can span multiple sessions if needed
- Re-running onboarding replaces memories with `source_type: "onboarding"`
- Systems can be reconnected anytime by editing `.env` and restarting
