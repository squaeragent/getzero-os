#!/usr/bin/env python3
"""
x402 USDC Payment Client — ported from NVProtocol claw.js.

Implements HTTP 402 payment flow on Arbitrum:
  1. Request → 402 with paymentRequirements
  2. Sign EIP-712 TransferWithAuthorization for USDC
  3. Retry with X-PAYMENT header (base64-encoded payment JSON)

Usage:
  python3 scanner/v6/x402.py --address   # show wallet address
  python3 scanner/v6/x402.py --balance   # show USDC balance
  python3 scanner/v6/x402.py --create    # force create new wallet
"""

import base64
import json
import os
import secrets
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

ARBITRUM_RPC   = "https://arb1.arbitrum.io/rpc"
USDC_ADDRESS   = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
CHAIN_ID       = 42161
WALLET_PATH    = Path("~/.zeroos/wallet.json").expanduser()

USDC_DOMAIN = {
    "name":              "USD Coin",
    "version":           "2",
    "chainId":           CHAIN_ID,
    "verifyingContract":  USDC_ADDRESS,
}

TRANSFER_WITH_AUTH_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from",        "type": "address"},
        {"name": "to",          "type": "address"},
        {"name": "value",       "type": "uint256"},
        {"name": "validAfter",  "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce",       "type": "bytes32"},
    ]
}

# EIP-712 domain type (needed for structured data signing)
EIP712_DOMAIN_TYPE = {
    "EIP712Domain": [
        {"name": "name",              "type": "string"},
        {"name": "version",           "type": "string"},
        {"name": "chainId",           "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ]
}

# USDC balanceOf(address) selector
BALANCE_OF_SELECTOR = "0x70a08231"


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] [X402] {msg}", flush=True)


# ─── WALLET ──────────────────────────────────────────────────────────────────

class X402Client:
    """x402 USDC payment client for Arbitrum."""

    def __init__(self, wallet_path=None):
        self.wallet_path = Path(wallet_path).expanduser() if wallet_path else WALLET_PATH
        self.account = self.load_or_create_wallet()

    def load_or_create_wallet(self):
        """Load wallet from file or create a new one."""
        from eth_account import Account

        if self.wallet_path.exists():
            try:
                with open(self.wallet_path) as f:
                    data = json.load(f)
                key = data.get("private_key") or data.get("privateKey")
                if key:
                    acct = Account.from_key(key)
                    log(f"Wallet loaded: {acct.address}")
                    return acct
            except (json.JSONDecodeError, OSError, Exception) as e:
                log(f"Failed to load wallet: {e}")

        # Create new wallet
        key = "0x" + secrets.token_hex(32)
        acct = Account.from_key(key)
        self.wallet_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.wallet_path, "w") as f:
            json.dump({
                "address":     acct.address,
                "private_key": key,
                "created_at":  datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)
        os.chmod(self.wallet_path, 0o600)
        log(f"New wallet created: {acct.address}")
        log(f"  Fund with USDC on Arbitrum to use x402 payments")
        return acct

    @property
    def address(self) -> str:
        return self.account.address

    # ─── USDC BALANCE ─────────────────────────────────────────────────────────

    def get_usdc_balance(self) -> float:
        """Query USDC balance via Arbitrum RPC (eth_call)."""
        # Encode balanceOf(address) call
        addr_padded = self.account.address[2:].lower().zfill(64)
        call_data = BALANCE_OF_SELECTOR + addr_padded

        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": USDC_ADDRESS, "data": call_data}, "latest"],
        }).encode()

        req = urllib.request.Request(
            ARBITRUM_RPC,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
            hex_val = result.get("result", "0x0")
            raw = int(hex_val, 16)
            return raw / 1e6  # USDC has 6 decimals
        except Exception as e:
            log(f"Balance check failed: {e}")
            return -1.0

    # ─── EIP-712 SIGNING ──────────────────────────────────────────────────────

    def sign_transfer_auth(self, to: str, amount: int, valid_before: int) -> dict:
        """Sign EIP-712 TransferWithAuthorization.

        Args:
            to: recipient address
            amount: USDC amount in raw units (6 decimals)
            valid_before: unix timestamp
        Returns:
            dict with signature fields (v, r, s) and message fields
        """
        from eth_account.messages import encode_typed_data

        nonce = "0x" + secrets.token_hex(32)

        message = {
            "from":        self.account.address,
            "to":          to,
            "value":       amount,
            "validAfter":  0,
            "validBefore": valid_before,
            "nonce":       nonce,
        }

        # encode_typed_data expects (domain_data, types, primary_type, message)
        signable = encode_typed_data(
            USDC_DOMAIN,
            {**EIP712_DOMAIN_TYPE, **TRANSFER_WITH_AUTH_TYPES},
            "TransferWithAuthorization",
            message,
        )
        signed = self.account.sign_message(signable)

        return {
            "from":        self.account.address,
            "to":          to,
            "value":       str(amount),
            "validAfter":  "0",
            "validBefore": str(valid_before),
            "nonce":       nonce,
            "v":           signed.v,
            "r":           hex(signed.r),
            "s":           hex(signed.s),
        }

    def build_payment_header(self, to: str, amount: int) -> str:
        """Build base64-encoded X-PAYMENT header value.

        Args:
            to: recipient address
            amount: USDC amount in raw units (6 decimals)
        Returns:
            base64-encoded JSON string for X-PAYMENT header
        """
        import time as _time
        valid_before = int(_time.time()) + 3600  # 1 hour validity

        auth = self.sign_transfer_auth(to, amount, valid_before)

        payment = {
            "x402Version":  1,
            "scheme":       "exact",
            "network":      "arbitrum-mainnet",
            "payload": {
                "signature":   f"0x{auth['r'][2:].zfill(64)}{auth['s'][2:].zfill(64)}{format(auth['v'], '02x')}",
                "authorization": {
                    "from":        auth["from"],
                    "to":          auth["to"],
                    "value":       auth["value"],
                    "validAfter":  auth["validAfter"],
                    "validBefore": auth["validBefore"],
                    "nonce":       auth["nonce"],
                },
            },
        }

        payload_json = json.dumps(payment, separators=(",", ":"))
        return base64.b64encode(payload_json.encode()).decode()

    # ─── HTTP x402 FLOW ──────────────────────────────────────────────────────

    def call_with_x402(self, base_url: str, path: str, opts: dict = None) -> str | None:
        """Full x402 payment flow.

        1. Send request → expect 402
        2. Parse paymentRequirements from response
        3. Sign and build payment header
        4. Retry with X-PAYMENT header

        Args:
            base_url: API base URL
            path: API path
            opts: dict with optional 'method', 'headers', 'data', 'content_type'
        Returns:
            response body string, or None on failure
        """
        opts = opts or {}
        url = f"{base_url}{path}"
        method = opts.get("method", "GET")
        headers = dict(opts.get("headers", {}))
        data = opts.get("data")

        if data and isinstance(data, str):
            data = data.encode()

        # Step 1: Initial request (expect 402)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        if opts.get("content_type"):
            req.add_header("Content-Type", opts["content_type"])

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                # Got 200 on first try — no payment needed
                return resp.read().decode()
        except urllib.error.HTTPError as e:
            if e.code != 402:
                log(f"x402 {path} → HTTP {e.code} (not 402)")
                return None
            # Parse 402 response for payment requirements
            try:
                body = e.read().decode()
                error_data = json.loads(body)
            except Exception:
                log(f"x402 {path} → 402 but couldn't parse requirements")
                return None

        # Step 2: Extract payment requirements
        requirements = error_data.get("paymentRequirements", [])
        if not requirements:
            # Try alternate field names
            requirements = error_data.get("payment_requirements", [])
        if not requirements:
            log(f"x402 {path} → 402 but no paymentRequirements in response")
            return None

        pay_req = requirements[0]  # use first requirement
        pay_to = pay_req.get("payTo", pay_req.get("pay_to", ""))
        pay_amount = int(pay_req.get("maxAmountRequired",
                         pay_req.get("max_amount_required",
                         pay_req.get("amount", 0))))

        if not pay_to or not pay_amount:
            log(f"x402 {path} → missing payTo or amount in requirements")
            return None

        log(f"x402 {path} → paying {pay_amount / 1e6:.4f} USDC to {pay_to[:10]}...")

        # Step 3: Build payment header and retry
        payment_header = self.build_payment_header(pay_to, pay_amount)

        headers["X-PAYMENT"] = payment_header
        req2 = urllib.request.Request(url, data=data, headers=headers, method=method)
        if opts.get("content_type"):
            req2.add_header("Content-Type", opts["content_type"])

        try:
            with urllib.request.urlopen(req2, timeout=60) as resp:
                result = resp.read().decode()
                log(f"x402 {path} → paid OK")
                return result
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:200]
            except Exception:
                pass
            log(f"x402 {path} → payment rejected: HTTP {e.code}: {body}")
            return None
        except Exception as e:
            log(f"x402 {path} → request failed: {e}")
            return None

    def call_paid(self, base_url: str, path: str, api_key: str = None, opts: dict = None) -> str | None:
        """Try API key first, fall back to x402 on 402 response.

        Args:
            base_url: API base URL
            path: API path
            api_key: optional API key
            opts: dict with optional 'method', 'headers', 'data', 'content_type', 'params'
        Returns:
            response body string, or None on failure
        """
        opts = opts or {}
        url = f"{base_url}{path}"
        params = opts.get("params")
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"

        method = opts.get("method", "GET")
        headers = dict(opts.get("headers", {}))
        data = opts.get("data")
        if data and isinstance(data, str):
            data = data.encode()

        # Try API key first
        if api_key:
            headers["X-API-Key"] = api_key
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            if opts.get("content_type"):
                req.add_header("Content-Type", opts["content_type"])
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.read().decode()
            except urllib.error.HTTPError as e:
                if e.code == 402:
                    log(f"API key got 402 on {path} — falling back to x402")
                else:
                    log(f"API key {path} → HTTP {e.code}")
                    return None
            except Exception as e:
                log(f"API key {path} failed: {e}")
                return None

        # x402 fallback (or primary if no API key)
        x402_opts = {
            "method":       method,
            "headers":      {k: v for k, v in headers.items() if k != "X-API-Key"},
            "data":         opts.get("data"),  # pass original string
            "content_type": opts.get("content_type"),
        }
        return self.call_with_x402(base_url, path + (f"?{qs}" if params else ""), x402_opts)


# ─── SINGLETON ────────────────────────────────────────────────────────────────

_client = None


def get_client(wallet_path=None) -> X402Client:
    """Get or create singleton X402Client."""
    global _client
    if _client is None:
        _client = X402Client(wallet_path)
    return _client


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    if "--create" in sys.argv:
        # Force create new wallet
        wp = WALLET_PATH
        if wp.exists():
            print(f"Wallet already exists at {wp}")
            print(f"Delete it first to create a new one")
            sys.exit(1)
        client = X402Client()
        print(f"Address: {client.address}")
        print(f"Fund with USDC on Arbitrum: {USDC_ADDRESS}")
        return

    if "--address" in sys.argv:
        client = X402Client()
        print(client.address)
        return

    if "--balance" in sys.argv:
        client = X402Client()
        bal = client.get_usdc_balance()
        print(f"Address: {client.address}")
        if bal >= 0:
            print(f"USDC:    {bal:.6f}")
        else:
            print("USDC:    (query failed)")
        return

    # Default: show help
    print("x402 USDC Payment Client (Arbitrum)")
    print()
    print("Commands:")
    print("  --address   Show wallet address")
    print("  --balance   Show USDC balance")
    print("  --create    Create new wallet (fails if exists)")
    print()
    print(f"Wallet: {WALLET_PATH}")


if __name__ == "__main__":
    main()
