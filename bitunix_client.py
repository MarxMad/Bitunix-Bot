"""
Bitunix Futures REST API Client
Implements the double SHA256 signature required by Bitunix API.
Base URL: https://fapi.bitunix.com

Signature verified working 2026-06-10.
"""

import hashlib
import time
import uuid
import json
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

BASE_URL = "https://fapi.bitunix.com"


def _sha256_hex(data: str) -> str:
    """Single SHA256 hex digest."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _build_sign(api_key: str, secret_key: str, nonce: str, timestamp: str,
                query_params: str = "", body: str = "") -> str:
    """
    Bitunix double-SHA256 signature:
      digest = SHA256(nonce + timestamp + api_key + queryParams + body)
      sign   = SHA256(digest + secretKey)
    """
    digest_input = nonce + timestamp + api_key + query_params + body
    digest = _sha256_hex(digest_input)
    sign = _sha256_hex(digest + secret_key)
    return sign


def _sort_query_params(params: dict) -> str:
    """Sort params by key (ASCII ascending) and concatenate as key=value... (no & or =)."""
    if not params:
        return ""
    sorted_keys = sorted(params.keys())
    return "".join(f"{k}{params[k]}" for k in sorted_keys)


class BitunixClient:
    """
    Async-friendly REST client for Bitunix Futures API.
    Uses requests (synchronous) for simplicity.
    """

    def __init__(self, api_key: str, secret_key: str, timeout: int = 15):
        self.api_key = api_key
        self.secret_key = secret_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        # Auto-retry on connection errors (not on 4xx/5xx — we want to see those)
        retry = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=[502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _make_headers(self, nonce: str, timestamp: str, sign: str) -> dict:
        return {
            "api-key": self.api_key,
            "nonce": nonce,
            "timestamp": timestamp,
            "sign": sign,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[Dict] = None) -> dict:
        """Signed GET request."""
        params = params or {}
        nonce = uuid.uuid4().hex  # 32-char random string
        timestamp = str(int(time.time() * 1000))
        query_str = _sort_query_params(params)
        sign = _build_sign(self.api_key, self.secret_key, nonce, timestamp, query_str, "")

        url = BASE_URL + path
        headers = self._make_headers(nonce, timestamp, sign)

        try:
            resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"GET {path} failed: {e}")
            raise

    def _post(self, path: str, body: Optional[Dict] = None, params: Optional[Dict] = None) -> dict:
        """Signed POST request."""
        body = body or {}
        params = params or {}
        nonce = uuid.uuid4().hex
        timestamp = str(int(time.time() * 1000))
        query_str = _sort_query_params(params)
        body_str = json.dumps(body, separators=(",", ":"))  # compact, no spaces
        sign = _build_sign(self.api_key, self.secret_key, nonce, timestamp, query_str, body_str)

        url = BASE_URL + path
        headers = self._make_headers(nonce, timestamp, sign)

        try:
            resp = self.session.post(url, params=params, data=body_str, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"POST {path} failed: {e}")
            raise

    # ─── Public Market Endpoints ───────────────────────────────────────────────

    def get_tickers(self, symbol: Optional[str] = None) -> dict:
        """Get market ticker(s). No auth required."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        resp = self.session.get(BASE_URL + "/api/v1/futures/market/tickers",
                                params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_depth(self, symbol: str, limit: int = 20) -> dict:
        """Get order book depth. No auth required."""
        params = {"symbol": symbol, "limit": limit}
        resp = self.session.get(BASE_URL + "/api/v1/futures/market/depth",
                                params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_kline(self, symbol: str, granularity: str = "1m", limit: int = 100) -> dict:
        """Get K-line / candlestick data."""
        params = {"symbol": symbol, "granularity": granularity, "limit": limit}
        resp = self.session.get(BASE_URL + "/api/v1/futures/market/kline",
                                params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # ─── Account Endpoints ────────────────────────────────────────────────────

    def get_account(self, margin_coin: str = "USDT") -> dict:
        """Get futures account balance."""
        return self._get("/api/v1/futures/account", {"marginCoin": margin_coin})

    def get_positions(self, symbol: Optional[str] = None) -> dict:
        """Get open positions."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._get("/api/v1/futures/position/get_pending_positions", params)

    def get_leverage(self, symbol: str, margin_coin: str = "USDT") -> dict:
        """Get current leverage and margin mode for a symbol."""
        return self._get("/api/v1/futures/account/get_leverage_margin_mode",
                         {"symbol": symbol, "marginCoin": margin_coin})

    def set_leverage(self, symbol: str, leverage: int, margin_coin: str = "USDT") -> dict:
        """Change leverage for a symbol."""
        return self._post("/api/v1/futures/account/change_leverage",
                          {"symbol": symbol, "leverage": leverage, "marginCoin": margin_coin})

    # ─── Order Endpoints ──────────────────────────────────────────────────────

    def place_order(self, symbol: str, side: str, order_type: str,
                    qty: str, price: Optional[str] = None,
                    reduce_only: bool = False,
                    time_in_force: str = "GTC",
                    client_id: Optional[str] = None) -> dict:
        """
        Place a single order.
        side: BUY | SELL
        order_type: LIMIT | MARKET
        qty: quantity as string
        price: required for LIMIT orders
        """
        body: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(qty),
            "reduceOnly": reduce_only,
            "timeInForce": time_in_force,
        }
        if price:
            body["price"] = str(price)
        if client_id:
            body["clientId"] = client_id
        return self._post("/api/v1/futures/trade/place_order", body)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel a single order."""
        return self._post("/api/v1/futures/trade/cancel_orders",
                          {"symbol": symbol, "orderIds": [order_id]})

    def cancel_all_orders(self, symbol: str) -> dict:
        """Cancel all open orders for a symbol."""
        return self._post("/api/v1/futures/trade/cancel_all_orders",
                          {"symbol": symbol, "marginCoin": "USDT"})

    def get_pending_orders(self, symbol: Optional[str] = None) -> dict:
        """Get open/pending orders."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._get("/api/v1/futures/trade/get_pending_orders", params)

    def flash_close_position(self, symbol: str, position_id: str) -> dict:
        """Flash close (market close) an open position at market price."""
        return self._post("/api/v1/futures/trade/flash_close_position",
                          {"symbol": symbol, "positionId": position_id, "marginCoin": "USDT"})

    def batch_order(self, orders: list) -> dict:
        """Place multiple orders at once."""
        return self._post("/api/v1/futures/trade/batch_order", {"orderList": orders})
