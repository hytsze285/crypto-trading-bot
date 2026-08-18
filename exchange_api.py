"""
exchange_api.py – Safety-first OKX REST execution layer.

Design principles
-----------------
* All order placement is guarded by an instrument whitelist and a
  live-trading feature flag so accidental trades are impossible unless
  the operator has explicitly opted in.
* Signing follows OKX's HMAC-SHA256 scheme for private endpoints.
* Public self-check helpers validate API credentials, instrument
  tradability, and server-time drift before the bot enters its main loop.
* Normalisation helpers round order sizes and prices to the increments
  dictated by OKX instrument rules (tickSz / lotSz / minSz).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, Optional

import requests

from config import (
    ALLOWED_INST_ID,
    ENABLE_LIVE_TRADING,
    OKX_API_KEY,
    OKX_HTTP_TIMEOUT,
    OKX_PASSPHRASE,
    OKX_REST_URL,
    OKX_SECRET_KEY,
    USE_SIMULATED_TRADING,
)

logger = logging.getLogger("crypto_bot.exchange_api")

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------


class OKXApiError(Exception):
    """Raised when OKX returns a non-zero error code or an HTTP error."""


class OKXSafetyError(Exception):
    """Raised when a safety guard (whitelist, live-trading gate, etc.) blocks an action."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MAX_CLOCK_DRIFT_SECONDS = 5


def _utc_iso_now() -> str:
    """Return current UTC time in OKX's required ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _sign(timestamp: str, method: str, request_path: str, body: str, secret: str) -> str:
    """Compute HMAC-SHA256 signature for an OKX private request."""
    pre_hash = timestamp + method.upper() + request_path + body
    mac = hmac.new(secret.encode("utf-8"), pre_hash.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


def _quantize(value: Decimal, step: Decimal) -> Decimal:
    """Round *value* down to the nearest multiple of *step*."""
    if step <= Decimal(0):
        return value
    return (value // step) * step


# ---------------------------------------------------------------------------
# Public OKX REST client
# ---------------------------------------------------------------------------


class OKXClient:
    """
    Thin wrapper around the OKX REST v5 API.

    Parameters
    ----------
    simulated:
        When *True* the sandbox base URL is used (set automatically from
        ``config.USE_SIMULATED_TRADING``).
    allowed_inst_id:
        The single instrument the bot is permitted to trade.  Any attempt
        to trade a different symbol raises ``OKXSafetyError``.
    enable_live_trading:
        Must be explicitly *True* to allow order placement on live
        endpoints.  When *False* all order methods raise ``OKXSafetyError``.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        simulated: bool = USE_SIMULATED_TRADING,
        allowed_inst_id: str = ALLOWED_INST_ID,
        enable_live_trading: bool = ENABLE_LIVE_TRADING,
        timeout: int = OKX_HTTP_TIMEOUT,
    ) -> None:
        self._base_url = OKX_REST_URL.rstrip("/")
        self._api_key = OKX_API_KEY
        self._secret = OKX_SECRET_KEY
        self._passphrase = OKX_PASSPHRASE
        self._simulated = simulated
        self._allowed_inst_id = allowed_inst_id
        self._enable_live_trading = enable_live_trading
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------

    def _public_get(self, path: str, params: Optional[Dict[str, str]] = None) -> Any:
        url = self._base_url + path
        resp = self._session.get(url, params=params, timeout=self._timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise OKXApiError(f"OKX public GET {path} error: {data}")
        return data

    def _private_request(self, method: str, path: str, body_dict: Optional[dict] = None) -> Any:
        import json as _json

        body_str = _json.dumps(body_dict, separators=(",", ":")) if body_dict else ""
        ts = _utc_iso_now()
        sig = _sign(ts, method, path, body_str, self._secret)

        headers = {
            "Content-Type": "application/json",
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": sig,
            "OK-ACCESS-TIMESTAMP": ts,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
        }
        if self._simulated:
            headers["x-simulated-trading"] = "1"

        url = self._base_url + path
        if method.upper() == "GET":
            resp = self._session.get(url, headers=headers, timeout=self._timeout)
        else:
            resp = self._session.post(url, headers=headers, data=body_str, timeout=self._timeout)

        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise OKXApiError(f"OKX {method} {path} error: {data}")
        return data

    # ------------------------------------------------------------------
    # Safety guard
    # ------------------------------------------------------------------

    def _assert_tradable(self, inst_id: str) -> None:
        """Raise OKXSafetyError unless the caller is allowed to trade *inst_id*."""
        if inst_id != self._allowed_inst_id:
            raise OKXSafetyError(
                f"Instrument '{inst_id}' is not in the whitelist "
                f"(allowed: '{self._allowed_inst_id}')."
            )
        if not self._enable_live_trading:
            raise OKXSafetyError(
                "Live trading is disabled.  Set ENABLE_LIVE_TRADING=true in your .env "
                "and verify you understand the risks before proceeding."
            )

    # ------------------------------------------------------------------
    # Startup self-check helpers
    # ------------------------------------------------------------------

    def _check_api_credentials(self) -> str:
        """Validate that OKX accepts the configured API credentials."""
        data = self._private_request("GET", "/api/v5/account/balance")
        return f"credentials_ok (account balance fetched, code={data['code']})"

    def _check_instrument_whitelist(self, inst_id: str) -> str:
        """Confirm the whitelisted inst_id matches the requested inst_id."""
        if inst_id != self._allowed_inst_id:
            raise OKXSafetyError(
                f"Requested instrument '{inst_id}' does not match "
                f"ALLOWED_INST_ID='{self._allowed_inst_id}'."
            )
        return f"whitelist_ok (inst_id={inst_id})"

    def _check_server_time_drift(self) -> str:
        """Verify local clock is within ±5 s of OKX server time."""
        data = self._public_get("/api/v5/public/time")
        server_ts_ms = int(data["data"][0]["ts"])
        server_ts = server_ts_ms / 1000.0
        local_ts = time.time()
        drift = abs(local_ts - server_ts)
        if drift > _MAX_CLOCK_DRIFT_SECONDS:
            raise OKXApiError(
                f"Clock drift too large: {drift:.1f}s "
                f"(max allowed: {_MAX_CLOCK_DRIFT_SECONDS}s). "
                "Please sync your system clock."
            )
        return f"time_ok (drift={drift:.3f}s)"

    def _check_instrument_tradable(self, inst_id: str) -> str:
        """Confirm the instrument is listed and trading on OKX SPOT."""
        data = self._public_get(
            "/api/v5/public/instruments",
            params={"instType": "SPOT", "instId": inst_id},
        )
        instruments = data.get("data", [])
        if not instruments:
            raise OKXApiError(f"Instrument '{inst_id}' not found on OKX SPOT.")
        state = instruments[0].get("state", "")
        if state != "live":
            raise OKXApiError(
                f"Instrument '{inst_id}' is not live (state='{state}')."
            )
        return f"instrument_ok (inst_id={inst_id}, state={state})"

    def get_instrument_rules(self, inst_id: str) -> Dict[str, Decimal]:
        """
        Fetch tick/lot/min-size rules for *inst_id* from OKX.

        Returns
        -------
        dict with keys ``tick_sz``, ``lot_sz``, ``min_sz`` as ``Decimal``.
        """
        data = self._public_get(
            "/api/v5/public/instruments",
            params={"instType": "SPOT", "instId": inst_id},
        )
        instruments = data.get("data", [])
        if not instruments:
            raise OKXApiError(f"Instrument '{inst_id}' not found.")
        info = instruments[0]
        return {
            "tick_sz": Decimal(info["tickSz"]),
            "lot_sz": Decimal(info["lotSz"]),
            "min_sz": Decimal(info["minSz"]),
        }

    def startup_self_check(self, inst_id: str) -> Dict[str, str]:
        """
        Run all pre-flight checks and return a dict of results.

        Raises on the first failure so the bot does not start with a
        misconfigured or dangerous state.
        """
        results: Dict[str, str] = {}
        results["time_drift"] = self._check_server_time_drift()
        results["instrument_tradable"] = self._check_instrument_tradable(inst_id)
        results["instrument_whitelist"] = self._check_instrument_whitelist(inst_id)
        results["api_credentials"] = self._check_api_credentials()
        logger.info("startup_self_check passed: %s", results)
        return results

    # ------------------------------------------------------------------
    # Normalisation helpers
    # ------------------------------------------------------------------

    def normalize_size(self, size: Decimal, rules: Dict[str, Decimal]) -> Decimal:
        """Round *size* down to the nearest lot_sz, respecting min_sz."""
        lot_sz = rules["lot_sz"]
        min_sz = rules["min_sz"]
        normalised = _quantize(size, lot_sz)
        if normalised < min_sz:
            raise OKXSafetyError(
                f"Order size {size} is below the minimum allowed size {min_sz}."
            )
        return normalised

    def normalize_price(self, price: Decimal, rules: Dict[str, Decimal]) -> Decimal:
        """Round *price* down to the nearest tick_sz."""
        return _quantize(price, rules["tick_sz"])

    # ------------------------------------------------------------------
    # Order methods
    # ------------------------------------------------------------------

    def place_spot_market_buy_by_base_size(
        self,
        inst_id: str,
        base_size: Decimal,
        exp_window_ms: int = 3000,
        cl_ord_id: str = "",
    ) -> Dict[str, Any]:
        """Place a SPOT market-buy order sized in base currency."""
        self._assert_tradable(inst_id)
        rules = self.get_instrument_rules(inst_id)
        sz = self.normalize_size(base_size, rules)
        body: Dict[str, Any] = {
            "instId": inst_id,
            "tdMode": "cash",
            "side": "buy",
            "ordType": "market",
            "sz": str(sz),
            "tgtCcy": "base_ccy",
        }
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
        if exp_window_ms > 0:
            body["expTime"] = str(int(time.time() * 1000) + exp_window_ms)
        data = self._private_request("POST", "/api/v5/trade/order", body)
        logger.info("place_spot_market_buy_by_base_size: %s", data)
        return data

    def place_spot_market_sell_by_base_size(
        self,
        inst_id: str,
        base_size: Decimal,
        exp_window_ms: int = 3000,
        cl_ord_id: str = "",
    ) -> Dict[str, Any]:
        """Place a SPOT market-sell order sized in base currency."""
        self._assert_tradable(inst_id)
        rules = self.get_instrument_rules(inst_id)
        sz = self.normalize_size(base_size, rules)
        body: Dict[str, Any] = {
            "instId": inst_id,
            "tdMode": "cash",
            "side": "sell",
            "ordType": "market",
            "sz": str(sz),
            "tgtCcy": "base_ccy",
        }
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
        if exp_window_ms > 0:
            body["expTime"] = str(int(time.time() * 1000) + exp_window_ms)
        data = self._private_request("POST", "/api/v5/trade/order", body)
        logger.info("place_spot_market_sell_by_base_size: %s", data)
        return data

    def place_spot_limit_buy(
        self,
        inst_id: str,
        base_size: Decimal,
        price: Decimal,
        exp_window_ms: int = 3000,
        cl_ord_id: str = "",
    ) -> Dict[str, Any]:
        """Place a SPOT limit-buy order."""
        self._assert_tradable(inst_id)
        rules = self.get_instrument_rules(inst_id)
        sz = self.normalize_size(base_size, rules)
        px = self.normalize_price(price, rules)
        body: Dict[str, Any] = {
            "instId": inst_id,
            "tdMode": "cash",
            "side": "buy",
            "ordType": "limit",
            "sz": str(sz),
            "px": str(px),
        }
        if cl_ord_id:
            body["clOrdId"] = cl_ord_id
        if exp_window_ms > 0:
            body["expTime"] = str(int(time.time() * 1000) + exp_window_ms)
        data = self._private_request("POST", "/api/v5/trade/order", body)
        logger.info("place_spot_limit_buy: %s", data)
        return data

    def get_order(self, inst_id: str, ord_id: str) -> Dict[str, Any]:
        """Fetch order details by order ID."""
        qs = urllib.parse.urlencode({"instId": inst_id, "ordId": ord_id})
        data = self._private_request("GET", f"/api/v5/trade/order?{qs}")
        return data

    def cancel_order(self, inst_id: str, ord_id: str) -> Dict[str, Any]:
        """Cancel an open order."""
        self._assert_tradable(inst_id)
        body = {"instId": inst_id, "ordId": ord_id}
        data = self._private_request("POST", "/api/v5/trade/cancel-order", body)
        logger.info("cancel_order inst_id=%s ord_id=%s: %s", inst_id, ord_id, data)
        return data

    def safe_place_then_verify_market_buy(
        self,
        inst_id: str,
        base_size: Decimal,
        exp_window_ms: int = 3000,
        cl_ord_id: str = "",
    ) -> Dict[str, Any]:
        """
        Place a market-buy and immediately verify it reached a terminal state.

        The method polls the order status once after placement.  If the order
        is not filled within *exp_window_ms* the method attempts a cancel and
        raises ``OKXSafetyError``.

        Returns the final order-detail dict on success.
        """
        place_resp = self.place_spot_market_buy_by_base_size(
            inst_id, base_size, exp_window_ms, cl_ord_id
        )
        ord_id: str = place_resp["data"][0]["ordId"]

        # Allow exchange a short moment to process the fill
        time.sleep(min(exp_window_ms / 1000.0, 2.0))

        order_data = self.get_order(inst_id, ord_id)
        order_info = order_data["data"][0]
        state = order_info.get("state", "")

        if state == "filled":
            logger.info(
                "safe_place_then_verify_market_buy: order %s filled at avgPx=%s",
                ord_id,
                order_info.get("avgPx"),
            )
            return order_info

        # Order not filled – attempt cancel for safety
        logger.warning(
            "safe_place_then_verify_market_buy: order %s not filled (state=%s), cancelling",
            ord_id,
            state,
        )
        try:
            self.cancel_order(inst_id, ord_id)
        except OKXApiError as exc:
            logger.error("Failed to cancel order %s: %s", ord_id, exc)

        raise OKXSafetyError(
            f"Market buy for {inst_id} was not filled within verification window "
            f"(ord_id={ord_id}, state={state})."
        )
