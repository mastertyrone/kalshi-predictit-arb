"""
kalshi_predictit_arb — Python client for the Kalshi <-> PredictIt arbitrage scanner.

Live endpoint (pay-per-call, $0.02 USDC on Base, x402 v2 "exact" scheme):
    https://x402.bankr.bot/0x69fb671637ed68881f66b9ebf305ec3ef5574f65/kalshi-predictit-arb

What it returns
----------------
GET ?q=<keywords>&limit=<1-25>&mode=opportunities|all

Each scan entity-resolves Kalshi markets against PredictIt contracts
(state / district / office / party / numeric-strike / cycle-year gating,
party-asymmetry hard-reject), prices BOTH arbitrage directions with real
fee drag, and reports worst-case net profit across settlement outcomes:

  * Kalshi taker fee:  ceil(7 * P * (1-P)) cents per contract per leg
  * PredictIt: 10% of winning-leg profit + 5% withdrawal drag on proceeds
  * executable: True only when worst-case net >= 1c after ALL fee drag

Opportunity fields you will actually use: ``pair`` (human label),
``best_direction`` with ``net_yield_c`` (worst-case net, cents),
``net_yield_pct``, ``stake_c``, ``annualized_roc_pct``, ``executable``,
plus ``kalshi_depth`` (VWAP walk to $100/$500/$1000) and a note that
PredictIt publishes no public depth (size within its $850/contract
position limit).

Usage
-----
    from kalshi_predictit_arb import ArbScanner

    scanner = ArbScanner()  # reads X402_WALLET_KEY from the environment
    for opp in scanner.opportunities(q="governor", limit=10):
        d = opp["best_direction"]
        print(opp.get("event"), d["net_yield_c"], "c net,", d["net_yield_pct"], "%")

Your wallet pays $0.02 USDC on Base per call. The private key NEVER leaves
your machine: it only signs an EIP-712 TransferWithAuthorization offline,
and the facilitator submits it (gasless for you). Never hardcode or commit
a key — pass it via the ``X402_WALLET_KEY`` env var or the constructor.

Dependencies: requests, eth-account
    pip install requests eth-account
"""

import base64
import json
import os
import secrets
import time

import requests
from eth_abi import encode as abi_encode
from eth_account import Account
from eth_utils import keccak

ENDPOINT = (
    "https://x402.bankr.bot/0x69fb671637ed68881f66b9ebf305ec3ef5574f65"
    "/kalshi-predictit-arb"
)
BASE_CHAIN_ID = 8453
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PRICE_USDC = 0.02

_EIP712_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ]
}


def _eip712_sign(account, domain, message):
    """Sign EIP-712 TransferWithAuthorization without web3.py."""
    type_hash = keccak(
        b"TransferWithAuthorization("
        b"address from,address to,uint256 value,"
        b"uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    )
    domain_separator = keccak(
        abi_encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                keccak(
                    b"EIP712Domain(string name,string version,"
                    b"uint256 chainId,address verifyingContract)"
                ),
                keccak(domain["name"].encode()),
                keccak(domain["version"].encode()),
                domain["chainId"],
                domain["verifyingContract"],
            ],
        )
    )
    struct_hash = keccak(
        abi_encode(
            ["bytes32", "address", "address", "uint256", "uint256", "uint256", "bytes32"],
            [
                type_hash,
                message["from"],
                message["to"],
                message["value"],
                message["validAfter"],
                message["validBefore"],
                message["nonce"],
            ],
        )
    )
    digest = keccak(b"\x19\x01" + domain_separator + struct_hash)
    sig_hex = account.unsafe_sign_hash(digest).signature.hex()
    return sig_hex if sig_hex.startswith("0x") else "0x" + sig_hex


class PaymentRefused(Exception):
    """Raised when the 402 challenge fails our safety checks."""


class ArbScanner:
    """Client for the Kalshi<->PredictIt arbitrage scanner API."""

    def __init__(self, private_key=None, max_usd_per_call=0.02, timeout=30):
        key = private_key or os.environ.get("X402_WALLET_KEY")
        if not key:
            raise ValueError(
                "No wallet key: pass private_key= or set X402_WALLET_KEY. "
                "The key only signs offline; it is never transmitted."
            )
        key = key if key.startswith("0x") else "0x" + key
        self.account = Account.from_key(key)
        self.max_usd_per_call = float(max_usd_per_call)
        self.timeout = timeout

    # -- public API -----------------------------------------------------
    def scan(self, q=None, limit=10, mode="opportunities"):
        """Full scan response dict (opportunities + fee model + echo)."""
        params = {"limit": max(1, min(25, int(limit))), "mode": mode}
        if q:
            params["q"] = q
        url = ENDPOINT + "?" + requests.compat.urlencode(params)
        res = requests.get(url, timeout=self.timeout)
        if res.status_code == 402:
            res = self._pay_and_retry(url, res)
        res.raise_for_status()
        return res.json()

    def opportunities(self, q=None, limit=10):
        """Just the executable opportunities, best net first."""
        return self.scan(q=q, limit=limit, mode="opportunities").get(
            "opportunities", []
        )

    # -- x402 v2 exact-scheme payment -----------------------------------
    def _pay_and_retry(self, url, res402):
        body = res402.json()
        req = self._select_requirement(body.get("accepts", []))
        header = self._build_payment_header(url, body, req)
        res = requests.get(
            url,
            headers={"PAYMENT-SIGNATURE": header, "X-PAYMENT": header},
            timeout=self.timeout,
        )
        return res

    def _select_requirement(self, accepts):
        if not accepts:
            raise PaymentRefused("402 with no payment requirements.")
        # Prefer Base + exact scheme.
        cands = [
            a
            for a in accepts
            if a.get("network") in ("eip155:8453", "base")
            and a.get("scheme") == "exact"
        ]
        req = (cands or accepts)[0]
        network = req.get("network")
        if network not in ("eip155:8453", "base"):
            raise PaymentRefused(f"Refusing non-Base network: {network}")
        if str(req.get("asset", "")).lower() != BASE_USDC.lower():
            raise PaymentRefused(f"Refusing non-USDC asset: {req.get('asset')}")
        amount_usd = int(req["amount"]) / 1e6
        if amount_usd <= 0:
            raise PaymentRefused("Refusing unreadable amount.")
        if amount_usd > self.max_usd_per_call:
            raise PaymentRefused(
                f"Refusing ${amount_usd} > cap ${self.max_usd_per_call}."
            )
        return req

    def _build_payment_header(self, url, body, req):
        now = int(time.time())
        authorization = {
            "from": self.account.address,
            "to": req["payTo"],
            "value": int(req["amount"]),
            "validAfter": now - 600,
            "validBefore": now + int(req.get("maxTimeoutSeconds", 300)),
            "nonce": secrets.token_bytes(32),
        }
        extra = req.get("extra", {}) or {}
        signature = _eip712_sign(
            self.account,
            {
                "name": extra.get("name", "USD Coin"),
                "version": extra.get("version", "2"),
                "chainId": BASE_CHAIN_ID,
                "verifyingContract": req["asset"],
            },
            authorization,
        )
        payload = {
            "x402Version": 2,
            "resource": body.get("resource") or {"url": url},
            "accepted": req,
            "payload": {
                "signature": signature,
                "authorization": {
                    "from": authorization["from"],
                    "to": authorization["to"],
                    "value": str(authorization["value"]),
                    "validAfter": str(authorization["validAfter"]),
                    "validBefore": str(authorization["validBefore"]),
                    "nonce": "0x" + authorization["nonce"].hex(),
                },
            },
        }
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
