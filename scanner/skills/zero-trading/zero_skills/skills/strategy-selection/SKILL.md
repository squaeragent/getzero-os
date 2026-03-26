---
name: strategy-selection
description: 'Select the right trading strategy based on market conditions, operator history, energy, and credits.'
---

# strategy selection

pick the right strategy. wrong strategy in right market still loses.

## 4-input decision framework

every recommendation requires 4 inputs. no exceptions.

### 1. market conditions (heat + pulse)
- call `zero_evaluate()` on target coins
- read regime: trending, chaotic, stable, mean-reverting
- check consensus direction and conviction level
- high conviction trending → momentum or sniper
- chaotic regime → watch or scout. never momentum in chaos

### 2. operator history (score + session_history)
- call `zero_score()` and `zero_session_history()`
- new operator (< 5 sessions) → conservative strategies only
- losing streak (3+ red sessions) → watch or scout. rebuild confidence
- winning streak → allow higher risk but warn about overconfidence

### 3. energy level
- low energy → watch, scout, or funding. passive strategies
- medium energy → momentum, fade, defense
- high energy → sniper, apex, degen (if operator is experienced)

### 4. credit balance
- call `zero_credits()` before every recommendation
- never recommend a strategy the operator can't afford
- if balance < 500, recommend watch (50) or scout (100)
- always state the cost upfront

## strategy-condition mapping

| strategy | best regime    | min sessions | cost | when to use                        |
|----------|---------------|-------------|------|------------------------------------|
| watch    | any           | 0           | 50   | observation. first session. unsure |
| scout    | any           | 0           | 100  | build intelligence. no risk        |
| defense  | chaotic/down  | 3           | 200  | hedge. fear spike. protect gains   |
| funding  | stable        | 5           | 300  | exploit funding imbalance          |
| fade     | mean-reverting| 5           | 400  | counter-trend at exhaustion        |
| momentum | trending      | 3           | 500  | ride confirmed directional move    |
| sniper   | any           | 10          | 750  | precision entry at key level       |
| apex     | trending      | 15          | 800  | multi-layer advanced strategy      |
| degen    | volatile      | 20          | 1000 | aggressive. experienced only       |

## rules

- never recommend degen to operators with < 20 sessions
- never recommend apex to operators with < 15 sessions
- if unsure, recommend watch. observation is free intelligence
- always preview with `zero_preview_strategy()` before starting
- state the cost and risk level before the operator confirms
