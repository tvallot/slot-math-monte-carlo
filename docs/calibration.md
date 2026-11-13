# Calibration log

How the game parameters were tuned to reach the target return, condensed from the development log. Version tags (`V0.3.x`) match the ones cited in code comments.

## Target and constraints

| Constraint | Value |
|---|---|
| Declared RTP (return to player) | 96.5 % |
| Upper limit for the declared RTP | 96.7 % (Stake Engine "3-star" limit) |
| Win cap | 1,000 × the bet |
| Paytable granularity | multiples of 0.10 × the bet |
| State | none between rounds: every bet is independent |

Design goals on top of that: the final level (N5) must stay rare, the Fly mini-bonus should trigger once every 15 to 20 rounds, and symbol values must follow a clear hierarchy (onion = garlic < butter = herbs < knife < dish < gloves < chicken).

## Method

Every parameter change follows the same loop:

1. simulate a library of rounds with fixed seeds (round *i* uses seed *i* + 1);
2. measure the RTP, the hit rate, how often each level is reached and how much each level contributes to the RTP;
3. replay every simulated round from its event log and check that the replay gives the same board and the same win (0 replay errors is a hard requirement);
4. change one lever at a time and simulate again.

Long runs are split into 10 independent batches of 300,000 rounds, so the spread between batches gives a direct view of the Monte Carlo noise. Paytable candidates were compared without resimulating: the clusters of each round are recorded, so a new paytable can be applied to the same rounds and the RTP recomputed in seconds.

## History

### 6 × 6 grid, "Candidate A" (V0.3.9): abandoned

Symbol weights 29 / 29 / 6 / 6 / 10 / 8 / 7 / 5, level thresholds 20 / 40 / 60 / 80.

| Metric (200,000 rounds) | Value |
|---|---:|
| RTP | 96.90 % |
| Hit rate | 61.1 % |
| N5 reached | 1 in 121 |
| Max win observed | 390 × |

The return was on target, but the declared RTP (96.9 %) was above the 96.7 % limit and lowering it would have broken the level frequencies. The grid was moved to 7 × 7.

### 7 × 7 grid, paytable rebuild (V0.3.13 to V0.3.16)

Symbol weights 19 / 19 / 15 / 15 / 11 / 9 / 7 / 5 (low symbols in pairs, as in the design), thresholds 10 / 25 / 40 / 55, new paytable, Fly mini-bonus disabled.

| Metric (3,000,000 rounds, 10 batches) | Value |
|---|---:|
| RTP | 96.567 % |
| Standard deviation between batches | 2.67 points (93.80 % to 102.48 %) |
| Hit rate | 30.68 % |
| N5 reached | 1 in 117.3 |
| Share of the RTP coming from N5 rounds | 85.9 % |
| p50 / p90 / p99 win | 0 × / 0.3 × / 11.3 × |

The game is highly volatile: less than 1 % of rounds (those reaching N5) return 86 % of the money.

### Enabling the Fly mini-bonus (V0.3.17): a bug found by simulation

The Fly drops 3 to 5 extra wilds at the start of a round. First measurement: **RTP ≈ 146 %**.

Cause: the winning positions of Fly wilds were credited to the progression meter, unlike the other wilds. On Fly rounds, N5 became about 10 times more likely.

Levers tried:

- raising all thresholds: rejected, since it affects every round; × 1.5 drops the global RTP to about 17 % and makes N5 nearly unreachable;
- fewer Fly wilds alone: only converges with a single wild, which is no longer the intended feature.

Fix kept: Fly wilds still explode and pay, but do not credit the progression meter; 2 or 3 wilds instead of 3 to 5. Result on 3,000,000 rounds: RTP 104.9 %, above 100 % in all 10 batches. The fix was kept but the Fly stayed disabled.

### Final configuration (V0.3.18 and V0.3.19)

A screening of threshold sets with the Fly enabled selected **10 / 26 / 40 / 55** (one point more for N3).

| Metric (3,000,000 rounds) | Value |
|---|---:|
| RTP | **96.501 %** |
| Standard error | ≈ 0.65 points |
| Hit rate | 32.30 % |
| N5 reached | 1 in 117.6 |
| Fly triggered | 1 in 17.54 |
| Win cap reached | 0 |
| Replay errors | 0 |

### Exact published RTP (V0.3.20)

The RTP published to the game server is computed from a weighted lookup table of simulated rounds. Instead of the SDK's optimizer, which redraws the win distribution, [`publication.py`](../kitchen_vs_fly/publication.py) gives the same integer weight to every winning round and another to every losing round, and solves for the two integers so that the published RTP equals 965 / 1000 **exactly**, as a fraction. The relative frequencies of all wins are those simulated; only the share of losing rounds (the hit rate) moves, from 32.30 % to 32.71 %.

## Remaining uncertainty

With a standard error of about 0.65 points on 3 million rounds, the probability that the true RTP is below 96 % is about 22 % (normal approximation). Because N5 rounds drive most of the return, the error shrinks slowly: halving it takes four times more rounds. See the convergence plot in the [analysis notebook](../notebooks/analysis.ipynb).
