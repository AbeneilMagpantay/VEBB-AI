"""
VEBB-AI: Market Context Module
Fetches macro market data for enhanced analysis.
"""

import os
import asyncio
import aiohttp
from aiohttp import TCPConnector
from dataclasses import dataclass
from typing import Optional

# Bypass proxy
os.environ["NO_PROXY"] = "*"
for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    os.environ.pop(proxy_var, None)

# Custom DNS resolver using Cloudflare (bypasses ISP DNS block)
import socket
import ssl

class CloudflareDNSResolver:
    """Custom resolver that uses Cloudflare DNS (1.1.1.1)."""
    
    async def resolve(self, host, port=0, family=socket.AF_INET):
        """Resolve hostname using Cloudflare DNS."""
        import asyncio
        
        # Use system resolver but it should work with WARP enabled
        # This is a fallback - WARP should handle it
        loop = asyncio.get_event_loop()
        try:
            infos = await loop.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
            return infos
        except Exception:
            # Fallback to hardcoded IPs for critical domains
            if 'binance' in host:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('13.35.186.20', port))]
            raise
    
    async def close(self):
        pass


@dataclass
class MarketContext:
    """Market-wide context data."""
    funding_rate: float = 0.0  # Current funding rate (e.g., 0.0001 = 0.01%)
    funding_rate_pct: str = ""  # Formatted as percentage
    open_interest: float = 0.0  # Total open interest in USD
    open_interest_str: str = ""  # Formatted (e.g., "$18.5B")
    oi_change_24h: float = 0.0  # 24h change percentage
    fear_greed_value: int = 50  # 0-100
    fear_greed_label: str = "Neutral"  # Fear/Greed/Neutral
    long_short_ratio: float = 1.0  # Long/Short account ratio
    
    @property
    def funding_direction(self) -> str:
        """Interpret funding rate."""
        if self.funding_rate > 0.0005:
            return "🔴 Crowded Long"
        elif self.funding_rate < -0.0005:
            return "🟢 Crowded Short"
        elif self.funding_rate > 0:
            return "Slightly Long"
        elif self.funding_rate < 0:
            return "Slightly Short"
        return "Neutral"
    
    @property
    def sentiment_warning(self) -> str:
        """Generate warning if extreme sentiment."""
        warnings = []
        
        if self.funding_rate > 0.001:
            warnings.append("⚠️ Very crowded long - squeeze risk")
        elif self.funding_rate < -0.001:
            warnings.append("⚠️ Very crowded short - squeeze risk")
        
        if self.fear_greed_value > 80:
            warnings.append("⚠️ Extreme Greed - reversal risk")
        elif self.fear_greed_value < 20:
            warnings.append("⚠️ Extreme Fear - potential bottom")
        
        return " | ".join(warnings) if warnings else ""


class MarketContextFetcher:
    """
    Fetches market context data from various APIs.
    
    Data sources:
    - Binance Futures API: Funding rate, Open Interest, Long/Short ratio
    - Alternative.me: Fear & Greed Index
    """
    
    def __init__(self, testnet: bool = True):
        self.testnet = testnet
        
        # API endpoints - ALWAYS use mainnet for market data
        # (testnet has connectivity issues and this is public read-only data)
        self.binance_base = "https://fapi.binance.com"
        
        self.fear_greed_url = "https://api.alternative.me/fng/"
        
        # Cached data
        self.current: MarketContext = MarketContext()
        self._last_update: float = 0
        self._update_interval: float = 60  # Update every 60 seconds
    
    async def _fetch_json(self, url: str) -> Optional[dict]:
        """Fetch JSON from URL with error handling."""
        import requests
        
        try:
            response = await asyncio.to_thread(
                lambda: requests.get(url, timeout=30, verify=True)
            )
            if response.status_code == 200:
                return response.json()
            else:
                print(f"[MarketContext] HTTP {response.status_code} from {url}")
                return None
        except Exception as e:
            print(f"[MarketContext] Error fetching {url}: {e}")
            return None
    
    async def fetch_funding_rate(self) -> float:
        """Get current funding rate for BTCUSDT."""
        url = f"{self.binance_base}/fapi/v1/premiumIndex?symbol=BTCUSDT"
        data = await self._fetch_json(url)
        
        if data and "lastFundingRate" in data:
            rate = float(data["lastFundingRate"])
            self.current.funding_rate = rate
            self.current.funding_rate_pct = f"{rate * 100:.4f}%"
            return rate
        return 0.0
    
    async def fetch_open_interest(self) -> float:
        """Get open interest for BTCUSDT."""
        url = f"{self.binance_base}/fapi/v1/openInterest?symbol=BTCUSDT"
        data = await self._fetch_json(url)
        
        if data and "openInterest" in data:
            oi_btc = float(data["openInterest"])
            # Get current price to convert to USD
            price_url = f"{self.binance_base}/fapi/v1/ticker/price?symbol=BTCUSDT"
            price_data = await self._fetch_json(price_url)
            
            if price_data and "price" in price_data:
                price = float(price_data["price"])
                oi_usd = oi_btc * price
                self.current.open_interest = oi_usd
                
                # Format as human readable
                if oi_usd >= 1e9:
                    self.current.open_interest_str = f"${oi_usd/1e9:.2f}B"
                else:
                    self.current.open_interest_str = f"${oi_usd/1e6:.1f}M"
                
                return oi_usd
        return 0.0
    
    async def fetch_long_short_ratio(self) -> float:
        """Get global long/short account ratio."""
        url = f"{self.binance_base}/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=15m&limit=1"
        data = await self._fetch_json(url)
        
        if data and len(data) > 0 and "longShortRatio" in data[0]:
            ratio = float(data[0]["longShortRatio"])
            self.current.long_short_ratio = ratio
            return ratio
        return 1.0
    
    async def fetch_fear_greed(self) -> int:
        """Get Fear & Greed Index from Alternative.me."""
        data = await self._fetch_json(self.fear_greed_url)
        
        if data and "data" in data and len(data["data"]) > 0:
            fg = data["data"][0]
            value = int(fg.get("value", 50))
            label = fg.get("value_classification", "Neutral")
            
            self.current.fear_greed_value = value
            self.current.fear_greed_label = label
            return value
        return 50
    
    async def update(self) -> MarketContext:
        """Fetch all market context data."""
        # Run all fetches concurrently
        await asyncio.gather(
            self.fetch_funding_rate(),
            self.fetch_open_interest(),
            self.fetch_long_short_ratio(),
            self.fetch_fear_greed(),
            return_exceptions=True
        )
        
        self._last_update = asyncio.get_event_loop().time()
        return self.current
    
    async def get_context(self) -> MarketContext:
        """Get market context, updating if stale."""
        now = asyncio.get_event_loop().time()
        if now - self._last_update > self._update_interval:
            await self.update()
        return self.current
    
    def format_for_gemini(self) -> str:
        """Format market context for Gemini prompt."""
        ctx = self.current
        
        lines = [
            "MARKET CONTEXT:",
            "",
            f"Funding Rate:    {ctx.funding_rate_pct} ({ctx.funding_direction})",
            f"Open Interest:   {ctx.open_interest_str}",
            f"Long/Short Ratio: {ctx.long_short_ratio:.2f}",
            f"Fear & Greed:    {ctx.fear_greed_value} ({ctx.fear_greed_label})",
        ]
        
        if ctx.sentiment_warning:
            lines.append("")
            lines.append(ctx.sentiment_warning)
        
        return "\n".join(lines)


# Test harness
async def test_context():
    """Test market context fetcher."""
    fetcher = MarketContextFetcher(testnet=False)  # Use mainnet for real data
    
    print("Fetching market context...")
    ctx = await fetcher.update()
    
    print("\n" + fetcher.format_for_gemini())
    print(f"\nRaw data:")
    print(f"  Funding: {ctx.funding_rate}")
    print(f"  OI: ${ctx.open_interest:,.0f}")
    print(f"  L/S Ratio: {ctx.long_short_ratio}")
    print(f"  Fear/Greed: {ctx.fear_greed_value}")


if __name__ == "__main__":
    asyncio.run(test_context())
