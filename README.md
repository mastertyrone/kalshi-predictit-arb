# kalshi-predictit-arb Python client

Live cross-venue arbitrage scanner: entity-resolved **Kalshi vs PredictIt**
political markets, priced in **both directions** with real fee drag, reporting
**worst-case net profit** across settlement outcomes.

Built and operated by **Team Takatini**.
Docs + live sample: https://mastertyrone.github.io/kalshi-predictit-arb/

## Install

```bash
pip install requests eth-account
```

## Quickstart

```python
from kalshi_predictit_arb import ArbScanner

scanner = ArbScanner()  # reads X402_WALLET_KEY from the environment
for opp in scanner.opportunities(q="governor", limit=10):
    d = opp["best_direction"]
    print(opp.get("event"), "->", d["net_yield_c"], "c worst-case net")
```

Each call costs **$0.02 USDC on Base**, settled via the x402 v2 `exact`
scheme. Your key only signs an EIP-712 `TransferWithAuthorization` offline —
it is never transmitted, logged, or stored anywhere but your machine.
`ArbScanner` refuses any charge above its per-call cap (default $0.02, the
exact API price) and any non-Base / non-USDC payment requirement.

## What "net" means

No phantom arbs. Every pair is gated on state, district, office, party,
numeric strike, and cycle year (candidate-vs-party mismatches are
hard-rejected), then charged:

- Kalshi taker fee: `ceil(7 * P * (1-P))` cents per contract per leg
- PredictIt: 10% of winning-leg profit + 5% withdrawal drag
- `executable = True` only at **>= 1c worst-case net** after all fee drag

The response also walks the Kalshi book to $100/$500/$1000 (VWAP + depth).
PredictIt publishes no public depth — size that leg within its
$850/contract position limit.

## API

- `ArbScanner(private_key=None, max_usd_per_call=0.02)` — key from arg or
  `X402_WALLET_KEY` env var.
- `.scan(q=None, limit=10, mode="opportunities")` — full response dict.
- `.opportunities(q=None, limit=10)` — executable opps, best net first.

`q` filters by keyword (default scans major races), `limit` 1–25,
`mode="all"` includes below-bar pairs for research.
