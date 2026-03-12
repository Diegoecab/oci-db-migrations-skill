# Cutover Readiness Prompt

Use this prompt when a user asks whether it's safe to perform cutover/switchover.

## Pre-Cutover Checklist

### 1. Migration State
- Must be ACTIVE with an executing job in WAITING phase
- This means initial load completed and CDC is running
- Verify: `oci database-migration migration get --migration-id <OCID>`

### 2. Replication Lag
- Current lag should be below `pre_cutover_max_lag_seconds` (default: 30s)
- Trend matters: stable low lag is better than fluctuating lag
- If lag is high: source may have heavy write activity; consider off-peak cutover
- Verify: `oci database-migration job get --job-id <JOB_OCID>` → check lag metrics

### 3. GoldenGate Fallback (if enabled)
- **STOPPED state** (default): Must be activated before cutover
  - Running `gg_activate_fallback.sh` re-positions SCN and starts processes
  - This ensures fallback captures from the point of cutover, not from creation time
- **RUNNING state**: Verify processes are healthy with no lag buildup
  - Check via GG REST API: `GET /services/v2/extracts/<name>/info/status`
- If fallback is critical: confirm both Extract and Replicat are healthy

### 4. Application Readiness
- App team ready to redirect connections to ADB
- DNS TTL lowered in advance (if using DNS-based routing)
- Connection strings updated or switchable via config/env var
- Rollback connection strings tested and ready

### 5. Timing
- Schedule during low-traffic window
- Cutover operation takes 2-5 minutes
- Total downtime = cutover time + app redirect time + smoke test
- Recommend: Friday evening or weekend for non-24x7 systems

## Go/No-Go Decision Framework

| Condition | Go | No-Go |
|-----------|-----|--------|
| Migration state | ACTIVE + WAITING | Any other state |
| Replication lag | ≤ 30s stable | > 60s or increasing |
| GG fallback (if req.) | STOPPED (ready) or RUNNING (healthy) | ABENDED or not created |
| Source load | Normal or low | Active batch jobs or heavy ETL |
| App team ready | Confirmed | Not available |
| Rollback tested | Yes | No |

## Cutover Sequence

```
1. [T-30 min]  Run pre-cutover validation
               python migrate.py assess-cutover --migration <key>

2. [T-15 min]  Activate GG fallback (if STOPPED)
               python migrate.py activate-fallback --migration <key>

3. [T-5 min]   Verify lag is within threshold
               Confirm app team is ready

4. [T-0]       Stop writes to source (quiesce application)
               Wait for lag to reach 0

5. [T+1 min]   Resume migration (cutover)
               oci database-migration migration resume --migration-id <OCID>

6. [T+3 min]   Migration completes (state → SUCCEEDED)
               Redirect application to ADB

7. [T+10 min]  Smoke test application against ADB

8. [T+30 min]  Confirm success or trigger rollback
```

## Rollback Plan

If issues detected post-cutover:
1. **Redirect app back to source** (app-level)
2. **Start GG reverse replication** if not already running
   - This replays transactions from ADB → Source
   - Any data written to ADB post-cutover flows back
3. **Assess data delta** — depending on how many transactions hit ADB,
   reverse replication handles synchronization
4. **RTO**: GG fallback activation ~2 min; data sync depends on transaction volume

## Response Template

When advising on cutover readiness:

```
## Current State Assessment
- Migration: [state, lag, duration]
- GG Fallback: [state of extract/replicat processes]
- Source Load: [if known]

## Recommendation: [GO / NO-GO / CONDITIONAL GO]
[Explain why]

## If GO — Execution Plan
[Customized sequence with specific commands]

## If NO-GO — What to Fix
[Specific items to resolve before re-evaluating]

## Rollback Plan
[Confirm fallback is in place, provide exact commands]
```
