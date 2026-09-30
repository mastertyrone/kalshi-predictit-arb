"""Example: scan for Kalshi<->PredictIt arbitrage with real fee drag.

Costs $0.02 USDC on Base per call. Set X402_WALLET_KEY first.
"""
from kalshi_predictit_arb import ArbScanner

scanner = ArbScanner()  # X402_WALLET_KEY env var; cap $0.02/call default (the exact API price)

print("=== executable opportunities: governor ===")
for opp in scanner.opportunities(q="governor", limit=5):
    d = opp["best_direction"]
    print(f"- {opp.get('event')}")
    print(
        f"  worst-case net {d['net_yield_c']}c "
        f"({d['net_yield_pct']}%), stake {d['stake_c']}c, "
        f"annualized ROC {d.get('annualized_roc_pct')}%, "
        f"executable={d['executable']}"
    )

print("\n=== full scan (mode=all, includes below-bar pairs) ===")
full = scanner.scan(q="senate", limit=3, mode="all")
print("fee model:", full.get("fee_model"))
for opp in full.get("opportunities", []):
    d = opp["best_direction"]
    print(f"- {opp.get('event')}: net {d['net_yield_c']}c, executable={d['executable']}")
