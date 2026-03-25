# ZERO — Progression & Engagement System Spec

```
VERSION   0.1.0
STATUS    DRAFT
DATE      2026-03-26
AUTHOR    zero/core
```

---

## Overview

This spec defines the complete progression and engagement layer for Zero. Every system here serves one goal: **make operators come back every day**. The design exploits seven behavioral hooks — variable reinforcement (sessions), loss aversion (streaks, rank decay), social proof (arena, rivals), time gates (weekly cycle), narrative identity (ranks, badges), near-miss feedback, and scarcity (genesis).

All systems share the terminal aesthetic: monospace type, phosphor green (`#00ff41`), dark backgrounds, minimal chrome. Badge names are callsigns, not corporate awards.

### Dependencies Map

```
STREAK ──────────────┐
                     ├──▶ MORNING BRIEF (external trigger)
RIVAL ───────────────┤
                     ├──▶ WEEKLY CYCLE (rewards reference all systems)
OPERATOR RANK ───────┤
                     ├──▶ BADGES (unlock criteria reference rank, streak, rival)
NEAR-MISS ───────────┘

GENESIS SCARCITY ──────▶ standalone (launch-only)
```

---

## 1. STREAK SYSTEM

**Priority: P0** — streaks are the single strongest daily retention mechanic.

### 1.1 What Counts as a Check-In

A daily check-in is recorded when the operator performs **any** of the following on a given UTC day:

| Action | Counts? |
|--------|---------|
| Open the app / load dashboard | YES |
| Have an active session running (auto-counted at 00:00 UTC) | YES |
| Read the Morning Brief (open notification / email) | YES |
| Activate a new session | YES |
| View result card from completed session | YES |

Only one check-in per UTC day. The first qualifying action stamps the day.

### 1.2 Streak Counter

- Visible on **every screen** in the top bar: `🔥 14d` (fire icon + day count)
- Streak counter pulses green on the day's first check-in (confirmation feedback)
- At milestones (7, 30, 100, 365), the counter briefly animates with a glow effect

### 1.3 Streak Rewards

| Milestone | Reward | Notification |
|-----------|--------|-------------|
| 3 days | 50 credits | `STREAK_03 — +50cr. you're building a habit.` |
| 7 days | 150 credits | `STREAK_07 — +150cr. one full week. locked in.` |
| 14 days | 300 credits | `STREAK_14 — +300cr. two weeks deep. most quit by now.` |
| 30 days | 750 credits + badge | `STREAK_30 — +750cr. 30 days. you're not most people.` |
| 60 days | 1,500 credits | `STREAK_60 — +1500cr. two months. this is who you are now.` |
| 100 days | 3,000 credits + badge | `STREAK_100 — +3000cr. triple digits. the machine runs.` |
| 200 days | 5,000 credits + badge | `STREAK_200 — +5000cr. 200 days. they'll write about this.` |
| 365 days | 10,000 credits + badge | `STREAK_365 — +10000cr. one year. legendary.` |

### 1.4 Streak Loss

- Missing a full UTC day (00:00–23:59 with zero qualifying actions) breaks the streak
- Streak resets to 0
- A **ghost streak** is preserved: "Previous best: 47 days" — visible on profile, fuels loss aversion
- On streak loss, show: `STREAK BROKEN — 23 days lost. rebuild starts now.`

### 1.5 Streak Freeze

| Item | Cost | Limit |
|------|------|-------|
| Streak Freeze (1 day) | 200 credits | Max 2 per 30-day window |
| Streak Freeze (purchased but unused) | Refunded after 30 days | — |

- Freeze must be purchased **before** the day is missed (proactive, not retroactive)
- Freeze is consumed automatically on a missed day
- Frozen days show as ❄️ in the streak calendar, not 🔥
- UI: "You have 1 freeze active. Miss tomorrow and it covers you."

### 1.6 Streak Notifications

| Timing | Channel | Message |
|--------|---------|---------|
| 18:00 local time (if no check-in yet) | Push / email | `⚠️ your 14-day streak expires in 6 hours. one tap to save it.` |
| 22:00 local time (if still no check-in) | Push (urgent) | `🔥 LAST CALL — streak dies at midnight. you've come too far.` |
| 00:05 UTC (streak broken, no freeze) | Push | `STREAK BROKEN — 14 days. gone. start over.` |
| 00:05 UTC (freeze consumed) | Push | `❄️ freeze activated. streak preserved at 14 days. you have 0 freezes left.` |

### 1.7 Streak × Sessions

- An active session auto-counts as a check-in for every UTC day it spans
- Example: a 168h Defense session auto-checks-in for all 7 days
- This incentivizes longer sessions for streak protection
- Completing a session during a streak adds a ⚡ marker to that day's streak calendar entry

---

## 2. OPERATOR RANK / TITLES

**Priority: P0** — rank is identity. Visible everywhere. Loss aversion via decay.

### 2.1 Rank Ladder

| Tier | Rank | Title | Icon | Sessions Required | Min Score | Min Streak | Total P&L |
|------|------|-------|------|-------------------|-----------|------------|-----------|
| 1 | R1 | `SIGNAL_NOISE` | `░` | 0 | 0 | 0 | — |
| 2 | R2 | `GRID_WALKER` | `▒` | 5 | 3.0 | 3 | — |
| 3 | R3 | `WIRE_RUNNER` | `▓` | 15 | 5.0 | 7 | > $0 |
| 4 | R4 | `EDGE_FINDER` | `◆` | 40 | 6.5 | 14 | > $500 |
| 5 | R5 | `COLD_READER` | `◈` | 100 | 7.5 | 30 | > $2,000 |
| 6 | R6 | `ZERO_POINT` | `⬡` | 250 | 8.5 | 60 | > $10,000 |
| 7 | R7 | `APEX_OPERATOR` | `⟐` | 500 | 9.0 | 100 | > $50,000 |

**Score** = rolling 30-day composite (session ROI, win rate, risk-adjusted return). Scale 0–10.

### 2.2 Rank Requirements

All requirements must be met **simultaneously** to hold a rank:

- **Sessions Required**: cumulative lifetime completed sessions
- **Min Score**: rolling 30-day performance score must stay above threshold
- **Min Streak**: current active streak must meet minimum (or have met it within last 7 days — grace period to avoid instant demote on one missed day)
- **Total P&L**: cumulative all-time paper P&L

### 2.3 Rank Decay (Loss Aversion)

Rank is **not permanent**. If your rolling score drops below threshold:

1. **Warning phase** (7 days): `⚠️ RANK ALERT — score 7.2 → 6.8. COLD_READER requires 7.5. 7 days to recover.`
2. **Demotion**: rank drops one tier. `RANK DOWN — COLD_READER → EDGE_FINDER. your score didn't hold.`
3. Demotion is **visible in the arena** to other operators
4. Streak loss can also trigger demotion if streak requirement is no longer met (with 7-day grace)

### 2.4 Rank Promotion

- When all thresholds for the next rank are met, promotion is instant
- Notification: `RANK UP — EDGE_FINDER → COLD_READER. new permissions unlocked.`
- Promotion animation: terminal-style ASCII art reveal of new rank icon
- Each rank unlocks a cosmetic perk:

| Rank | Unlock |
|------|--------|
| R2 GRID_WALKER | Custom callsign color |
| R3 WIRE_RUNNER | Pulse feed access |
| R4 EDGE_FINDER | Choose manual rival |
| R5 COLD_READER | Weekly narrative generation |
| R6 ZERO_POINT | Custom session names |
| R7 APEX_OPERATOR | Apex strategy unlocked, profile glow effect |

### 2.5 Rank Display

- Shown on: profile card, arena leaderboard, rival widget, session result cards, Morning Brief
- Format: `◈ COLD_READER` (icon + title, monospace, rank-tier color intensity)
- Other operators see your rank in all social contexts

---

## 3. BADGES / ACHIEVEMENTS

**Priority: P1** — badges are collectible identity. They tell your story.

### 3.1 Rarity Tiers

| Tier | Color | Expected % of Operators |
|------|-------|------------------------|
| COMMON | `#888888` (gray) | > 50% |
| UNCOMMON | `#00ff41` (green) | 20–50% |
| RARE | `#00bfff` (cyan) | 5–20% |
| EPIC | `#bf00ff` (purple) | 1–5% |
| LEGENDARY | `#ffbf00` (gold) | < 1% |

### 3.2 Session Milestone Badges

| Badge | Icon | Description | Requirement | Rarity |
|-------|------|-------------|-------------|--------|
| `FIRST_LIGHT` | `>_` | First session completed. | Complete 1 session | COMMON |
| `DOUBLE_TAP` | `>>` | Back-to-back sessions. | Complete 2 sessions in 24h | COMMON |
| `SERIAL_OPERATOR` | `###` | 10 sessions deep. | Complete 10 sessions | COMMON |
| `PIPELINE` | `═══` | The machine is running. | Complete 50 sessions | UNCOMMON |
| `HUNDRED_CYCLE` | `[C]` | Century. | Complete 100 sessions | RARE |
| `THOUSAND_HAND` | `[M]` | You've seen it all. | Complete 1,000 sessions | LEGENDARY |

### 3.3 Strategy Mastery Badges

| Badge | Icon | Description | Requirement | Rarity |
|-------|------|-------------|-------------|--------|
| `SURF_MASTER` | `🏄` | Momentum mastery. | 20 momentum sessions, avg ROI > 0% | UNCOMMON |
| `PYROMANIAC` | `🔥` | Degen mastery. Born in fire. | 20 degen sessions, avg ROI > 0% | UNCOMMON |
| `FORTRESS` | `🛡️` | Defense mastery. The wall holds. | 20 defense sessions, max DD < 2% | UNCOMMON |
| `ONE_SHOT` | `🎯` | Sniper mastery. One bullet, one kill. | 20 sniper sessions, win rate > 60% | RARE |
| `PATHFINDER` | `🏃` | Scout mastery. Mapped the territory. | 20 scout sessions | UNCOMMON |
| `CONTRARIAN` | `🔄` | Fade mastery. Against the crowd. | 20 fade sessions, avg ROI > 0% | RARE |
| `YIELD_FARMER` | `💰` | Funding mastery. The quiet game. | 20 funding sessions | UNCOMMON |
| `ALL_SEEING` | `👁️` | Watch mastery. You see everything. | 20 watch sessions | UNCOMMON |
| `FULL_SPECTRUM` | `◉` | Used all 9 strategies at least once. | 1+ session of each strategy type | RARE |

### 3.4 Performance Badges

| Badge | Icon | Description | Requirement | Rarity |
|-------|------|-------------|-------------|--------|
| `GREEN_RUN` | `+++` | 3 profitable sessions in a row. | 3 consecutive sessions with positive P&L | COMMON |
| `HOT_HAND` | `▲▲▲` | 5 profitable in a row. The hand is hot. | 5 consecutive profitable sessions | UNCOMMON |
| `UNTOUCHABLE` | `█▀█` | 10 in a row. They can't stop you. | 10 consecutive profitable sessions | EPIC |
| `FIRST_BLOOD` | `×` | First trade closed in profit. | Close 1 profitable trade | COMMON |
| `FIVE_BAGGER` | `×5` | 5x return on a single session. | Single session ROI > 500% | LEGENDARY |
| `ARENA_KING` | `♛` | #1 in weekly arena. | Finish week ranked #1 in arena | EPIC |
| `PODIUM` | `▐█▌` | Top 3 finish in weekly arena. | Finish week in top 3 | RARE |
| `NEVER_RED` | `■■■` | Full green week. Every session profitable. | 5+ sessions in one week, all profitable | EPIC |

### 3.5 Streak Badges

| Badge | Icon | Description | Requirement | Rarity |
|-------|------|-------------|-------------|--------|
| `LOCKED_IN` | `[7]` | 7-day streak. | Reach 7-day streak | COMMON |
| `NO_DAYS_OFF` | `[30]` | 30-day streak. A month of execution. | Reach 30-day streak | UNCOMMON |
| `IRON_WILL` | `[100]` | 100-day streak. Unbreakable. | Reach 100-day streak | RARE |
| `FULL_ORBIT` | `[365]` | 365-day streak. One full orbit. | Reach 365-day streak | LEGENDARY |

### 3.6 Discovery Badges

| Badge | Icon | Description | Requirement | Rarity |
|-------|------|-------------|-------------|--------|
| `FADE_TRIGGER` | `⟳` | First Fade activation. You saw the extremes. | Trigger Fade session activation conditions | UNCOMMON |
| `SNIPER_HIT` | `⊕` | First Sniper session with a trade. One shot landed. | Complete sniper session with 1+ trade | UNCOMMON |
| `APEX_UNLOCKED` | `⚡` | Gained access to Apex. The top strategy. | Reach score 7.0 and unlock Apex | RARE |
| `NIGHT_OWL` | `☾` | Session active past midnight UTC. | Have an active session at 00:00 UTC | COMMON |
| `REGIME_SURFER` | `~` | Held a position through a regime change. | Position open during regime transition | UNCOMMON |

### 3.7 Social / Rival Badges

| Badge | Icon | Description | Requirement | Rarity |
|-------|------|-------------|-------------|--------|
| `MARKED` | `⊗` | Assigned your first rival. | Get auto-assigned a rival | COMMON |
| `RIVAL_CRUSHER` | `⊘` | Beat your rival 5 weeks in a row. | 5 consecutive weekly wins vs rival | RARE |
| `NEMESIS` | `☠` | Beat the same rival 10 times. | 10 total weekly wins vs same rival | EPIC |
| `TOP_TEN` | `⟦10⟧` | Broke into the arena top 10. | Reach top 10 in arena rankings | UNCOMMON |

### 3.8 Special Badges

| Badge | Icon | Description | Requirement | Rarity |
|-------|------|-------------|-------------|--------|
| `GENESIS` | `◊` | You were here at the beginning. | Sign up in first 100 operators | LEGENDARY |
| `PHOENIX` | `↑` | Lost your streak. Rebuilt to 30+. | Reach 30-day streak after a streak loss | RARE |
| `COMEBACK` | `⟲` | Promoted after demotion. You climbed back. | Reach a rank after being demoted from it | RARE |

**Total: 33 badges** (7 common, 10 uncommon, 9 rare, 4 epic, 3 legendary)

### 3.9 Badge Display

- Profile shows all earned badges in a grid, sorted by rarity (legendary first)
- Operator selects 1 "featured badge" shown next to their name in arena and rival views
- Unearned badges are shown as locked silhouettes with "???" description — curiosity driver
- Badge earn notification: terminal-style reveal animation
  ```
  ╔══════════════════════════════════╗
  ║  BADGE UNLOCKED                 ║
  ║                                 ║
  ║  ⊕  SNIPER_HIT                 ║
  ║  "one shot landed."            ║
  ║                                 ║
  ║  rarity: UNCOMMON               ║
  ╚══════════════════════════════════╝
  ```

---

## 4. WEEKLY ARENA CYCLE

**Priority: P0** — the weekly cycle creates rhythm, urgency, and recurring reward moments.

### 4.1 Weekly Schedule

| Day | Phase | What Happens |
|-----|-------|-------------|
| Monday 00:00 UTC | `WEEK_START` | New arena week begins. Scores reset to 0 for the week. Rank carries over. |
| Mon–Sat | `COMPETE` | Sessions run. Weekly score accumulates. Rank shifts possible. |
| Saturday 23:59 UTC | `WEEK_LOCK` | Final scores locked. No more sessions count toward this week. |
| Sunday 00:00 UTC | `WEEKLY_RESET` | Rewards distributed. Weekly narrative generated. New rival assignments. |
| Sunday | `REST_DAY` | Sessions still run (for streak), but scores count toward next week. |

### 4.2 Weekly Score

Weekly score is calculated from all sessions completed Mon–Sat:

```
weekly_score = Σ(session_roi × session_credit_weight) / total_credits_spent
```

Where `session_credit_weight` = credits spent on that session / total credits spent that week. This normalizes for activity level — an operator running one great Sniper session can compete with an operator running ten Scout sessions.

### 4.3 Weekly Rewards

| Arena Finish | Credits | Bonus |
|-------------|---------|-------|
| #1 | 2,000 | `ARENA_KING` badge (if not already earned) |
| #2 | 1,200 | `PODIUM` badge attempt |
| #3 | 800 | `PODIUM` badge attempt |
| #4–10 | 400 | — |
| #11–25 | 200 | — |
| #26–50 | 100 | — |
| #51+ | 50 | — |

### 4.4 Weekly Narrative

Generated Sunday at 06:00 UTC via LLM. Covers:

- **Your performance**: sessions run, best/worst, overall ROI
- **Your rank movement**: promotions, demotions, or held steady
- **Your rival**: head-to-head result, current record
- **Arena highlights**: who climbed, who fell, notable moves
- **What the agents did**: top sim agents and their strategies that week

Example:
```
════════════════════════════════════════════════════
 WEEKLY DEBRIEF — WEEK 12, 2026
════════════════════════════════════════════════════

 YOU: ◈ COLD_READER
 Sessions: 4 (momentum ×2, sniper ×1, fade ×1)
 Weekly ROI: +3.8%
 Arena rank: #7 (↑3 from last week)

 RIVAL: ◆ zr_phantom — EDGE_FINDER
 Result: YOU WIN (3.8% vs 2.1%)
 H2H record: 4-2 (you lead)

 ARENA: @grid took #1 with a 12.4% sniper hit on SOL.
 @pulse dropped from #3 to #18 after a bad degen run.

 NEAR MISS: your fade session ended 2h before ETH
 dumped -6.2%. fade_the_crowd would have caught it.
════════════════════════════════════════════════════
```

### 4.5 Seasonal Reset (Quarterly)

Every 13 weeks (quarterly):

- Arena rankings reset to 0
- Ranks reset to R1 (SIGNAL_NOISE) — everyone re-climbs
- Badges are **permanent** (never reset)
- Seasonal leaderboard snapshot preserved: "Season 1 — #7 overall"
- Top 3 of the season get a seasonal badge variant (e.g., `S1_CHAMPION`)
- Season start notification: `NEW SEASON — all ranks reset. the climb begins again.`

---

## 5. RIVAL SYSTEM (Enhanced)

**Priority: P1** — rivals create personal stakes beyond abstract leaderboard position.

### 5.1 Rival Assignment

| Type | How Assigned | When Reassigned |
|------|-------------|-----------------|
| **Auto-rival** | Closest weekly score from previous week. Assigned at Sunday reset. | Every Sunday |
| **Manual rival** | Operator chooses from arena. Unlocked at R4 (EDGE_FINDER). | Operator changes it (max 1 change/week) |

If the operator has both auto and manual rivals, both are tracked. Manual rival takes priority in the dashboard widget.

### 5.2 Rival Widget (Dashboard)

Always visible in the sidebar:

```
┌─────────────────────────────┐
│  RIVAL: zr_phantom          │
│  ◆ EDGE_FINDER              │
│                             │
│  THIS WEEK                  │
│  you: +2.4%  them: +1.8%   │
│  ■■■■■■░░░░  (you lead)    │
│                             │
│  H2H: 4-2 (you lead)       │
│  current streak: W2         │
│                             │
│  LAST SESSION               │
│  🔥 degen — +$28.40        │
│  completed 3h ago           │
└─────────────────────────────┘
```

### 5.3 Head-to-Head Tracking

- Weekly winner: whoever has higher weekly score at Sunday lock
- Ties: both get a draw (counts as 0.5 for each)
- H2H record is cumulative and visible on both profiles
- Streak tracking: "W3" means you've beaten this rival 3 weeks in a row

### 5.4 Rival Notifications

| Event | Notification |
|-------|-------------|
| Rival completes session | `zr_phantom just finished a degen session. +$28.40. you're still ahead.` |
| Rival overtakes you | `zr_phantom just passed you. they're at +3.1%, you're at +2.4%.` |
| You overtake rival | `you just passed zr_phantom. hold the lead.` |
| Rival gets promoted | `zr_phantom just ranked up to COLD_READER. you're still EDGE_FINDER.` |
| Rival earns rare badge | `zr_phantom just earned UNTOUCHABLE. 10 in a row.` |

### 5.5 Rival Showdown

If auto-rivals are tied (within 0.5% weekly score) for 3+ consecutive weeks:

1. System declares a **SHOWDOWN**: `SHOWDOWN DECLARED — you and zr_phantom. 3 weeks tied. this week decides it.`
2. Showdown week: both operators' sessions get a 1.5x credit bonus
3. Winner gets `SHOWDOWN_VICTOR` (tracked internally, contributes to rival badges)
4. Showdown result highlighted in weekly narrative

---

## 6. NEAR-MISS SYSTEM

**Priority: P1** — near-misses are the "one more pull" mechanic. They reframe inaction as loss.

### 6.1 Post-Session Near-Misses

After every session completes, analyze what happened in the 4 hours after session end:

| Near-Miss Type | Condition | Display |
|---------------|-----------|---------|
| **Price near-miss** | A coin in scope moved > 3% in a favorable direction within 4h of session end | `SOL moved +4.2% in the 4 hours after your session ended.` |
| **Entry near-miss** | A coin was within 5% of triggering session entry criteria but didn't fire | `ETH was 0.3% from triggering. sniper would have entered at 7.1/7.0.` |
| **Strategy near-miss** | A different strategy would have profited on the same market conditions | `fade_the_crowd would have caught the BTC reversal. +2.8% estimated.` |

### 6.2 Near-Miss Card

Shown on the session result card, below the P&L:

```
┌─────────────────────────────────────────┐
│  NEAR MISSES                            │
│                                         │
│  ▸ AVAX moved +8.2% 4h after session   │
│    ended. scout would have caught it.   │
│                                         │
│  ▸ SOL was 0.3% from sniper trigger.   │
│    score: 6.8/7.0. so close.           │
│                                         │
│  ▸ your momentum session missed the     │
│    ETH dip. fade was the right call.    │
└─────────────────────────────────────────┘
```

### 6.3 Pulse Feed Near-Misses

Between sessions, the pulse feed shows real-time near-misses:

- `right now: BTC is 0.5% from sniper trigger. score 6.5/7.0.`
- `in the last hour: SOL spiked +2.1%. a momentum session would be surfing.`
- `AVAX funding rate just hit -0.04%. funding_farm territory.`

These serve as **session activation triggers** — they show what you're missing by not having a session running.

### 6.4 Near-Miss Limits

- Max 3 near-misses per session result card (show the most dramatic ones)
- Max 5 near-misses per day in pulse feed
- Never show near-misses that would have resulted in a loss (only show missed gains)
- Cooldown: don't show near-misses for the same coin within 6 hours

---

## 7. GENESIS SCARCITY

**Priority: P0** — scarcity drives launch urgency. One-time system.

### 7.1 Genesis Allocation

- First **100** operators to create an account get **Genesis** status
- Genesis grants: 10,000 free credits + `GENESIS` badge (LEGENDARY)
- Visible countdown on landing page and sign-up flow

### 7.2 Landing Page Counter

```
┌─────────────────────────────────────────┐
│                                         │
│  GENESIS OPERATORS: 87/100 remaining    │
│  ████████░░  87%                        │
│                                         │
│  10,000 credits. legendary badge.       │
│  never available again.                 │
│                                         │
└─────────────────────────────────────────┘
```

- Counter updates in real-time (websocket or polling)
- Below 20 remaining: counter turns amber
- Below 5 remaining: counter turns red, pulses
- At 0: "GENESIS CLOSED" — permanent display, badge of exclusivity for those who got in

### 7.3 Genesis Badge Properties

- **Permanent**: can never be removed, even on account deletion + re-creation
- **Non-transferable**: tied to account
- **Visible**: always shown on profile, even if not "featured"
- **Time-stamped**: shows exact sign-up date: `GENESIS #42 — 2026-04-01`
- **Appreciating value**: as total operator count grows, genesis becomes relatively rarer

### 7.4 Post-Genesis

After 100 spots fill:
- New operators get 1,000 credits (standard sign-up bonus)
- No badge
- Landing page shows: `GENESIS CLOSED — 100 operators. you missed it. standard allocation: 1,000cr.`

---

## 8. MORNING BRIEF

**Priority: P0** — the Morning Brief is the external trigger that starts the daily engagement loop.

### 8.1 Delivery

| Channel | Timing | Format |
|---------|--------|--------|
| Push notification | Operator's preferred time (default: 08:00 local) | Summary preview (2 lines) |
| Email | Same time as push | Full brief (HTML, terminal-styled) |
| In-app | Always available on dashboard | Full brief, interactive |

Operator can configure preferred time in settings. Available in 1-hour increments.

### 8.2 Brief Contents

```
════════════════════════════════════════
 MORNING BRIEF — 2026-03-26
 ◈ COLD_READER | 🔥 14d streak
════════════════════════════════════════

 OVERNIGHT
 ▸ BTC: +1.2% | ETH: -0.4% | SOL: +2.8%
 ▸ regime: TRENDING (stable since 3d)
 ▸ fear/greed: 62 (greed — rising)

 YOUR SESSIONS
 ▸ 🏄 momentum_surf completed — +$42.18
 ▸ result card ready. 2 near-misses.

 RECOMMENDED
 ▸ market favors: momentum, sniper
 ▸ funding opportunity: AVAX (-0.035%)
 ▸ suggested: 🎯 sniper on SOL (score 6.8/7.0)

 STREAK
 ▸ 🔥 14 days. check in to keep it alive.
 ▸ next milestone: 30 days (+750cr)

 RIVAL
 ▸ zr_phantom: ran degen overnight. +$28.40.
 ▸ weekly H2H: you +2.4% vs them +1.8%
 ▸ H2H record: 4-2 (you lead)

 ARENA
 ▸ your rank: #7 (↑3)
 ▸ leader: @grid (+8.2%)
════════════════════════════════════════
```

### 8.3 Personalization Tiers

| Operator History | Brief Style |
|-----------------|-------------|
| 0–3 sessions | **Onboarding**: explains concepts, suggests Watch mode, highlights what to try |
| 4–10 sessions | **Guided**: recommends strategies based on market + their past performance |
| 11+ sessions | **Personalized**: pattern-based recommendations, strategy-specific insights |
| 50+ sessions | **Expert**: compressed format, only anomalies and opportunities, skip basics |

### 8.4 Brief as Check-In

Opening the Morning Brief (push notification tap, email open, or in-app view) counts as the daily check-in for streak purposes. This ensures the brief itself is the habit-forming trigger.

---

## Implementation Priority

### P0 — Launch Blockers (build first)

| System | Reason | Estimated Scope |
|--------|--------|-----------------|
| Streak System | Strongest retention mechanic. Drives daily return. | Streak counter, check-in logic, notifications, freeze purchase |
| Operator Rank | Identity and loss aversion. Visible everywhere. | Rank calculation, decay/promotion, display on all surfaces |
| Weekly Arena Cycle | Creates rhythm and recurring reward moments. | Weekly score calc, reset logic, reward distribution |
| Genesis Scarcity | Launch-only. Must be ready at day one. | Counter, credit grant, badge, landing page widget |
| Morning Brief | External trigger that starts the loop. | Brief generation, delivery (push + email), preference settings |

### P1 — Week 2–4

| System | Reason |
|--------|--------|
| Badges (core set) | First 15 badges — session milestones, streaks, discovery. Expand later. |
| Rival System | Auto-assignment + widget + notifications. Manual rival at R4. |
| Near-Miss System | Post-session near-miss card. Pulse feed integration later. |

### P2 — Month 2

| System | Reason |
|--------|--------|
| Badges (full set) | Remaining 18 badges — mastery, performance, social, special. |
| Seasonal Reset | Quarterly cycle. Not needed until Season 1 ends. |
| Rival Showdown | Edge case mechanic. Build after core rival system proves engagement. |
| Weekly Narrative (LLM) | Requires LLM integration. Manual summaries can bridge the gap. |
| Near-Miss Pulse Feed | Real-time feed integration. Post-session card is sufficient for launch. |

---

## Data Model (Key Entities)

```
operator_profile:
  id, handle, created_at
  rank: int (1-7)
  rank_title: str
  score_30d: float
  streak_current: int
  streak_best: int
  streak_freezes_available: int
  streak_last_checkin: datetime
  total_sessions: int
  total_pnl: float
  badges: [badge_id]
  featured_badge: badge_id
  genesis: bool
  genesis_number: int | null
  rival_auto: operator_id
  rival_manual: operator_id | null
  brief_time_utc: int (hour)

weekly_record:
  operator_id, week_number, year
  weekly_score: float
  sessions_completed: int
  arena_rank: int
  rival_result: "win" | "loss" | "draw"
  credits_earned: int

badge_earn:
  operator_id, badge_id, earned_at

rival_matchup:
  operator_a, operator_b
  type: "auto" | "manual"
  weekly_wins_a: int
  weekly_wins_b: int
  current_streak: int (positive = A leads)
  showdown_active: bool

streak_calendar:
  operator_id, date
  checked_in: bool
  source: "app_open" | "session_active" | "brief" | "session_activate" | "result_view"
  freeze_used: bool
  session_completed: bool
```

---

## Key Metrics to Track

| Metric | Target | System |
|--------|--------|--------|
| D1 retention | > 60% | Morning Brief, Streak |
| D7 retention | > 40% | Streak rewards, Weekly cycle |
| D30 retention | > 25% | Rank progression, Badges |
| Daily active rate | > 50% of registered | Streak, Brief |
| Avg streak length | > 10 days | Streak, Freeze |
| Sessions per operator per week | > 3 | Near-miss, Rival, Weekly cycle |
| Genesis conversion rate | 100% fill in < 7 days | Scarcity counter |
| Rival widget engagement | > 30% click-through | Rival notifications |
| Morning Brief open rate | > 45% | Personalization, Streak tie-in |
