from __future__ import annotations

import re


INDEX_MAPPING_SCHEMA_VERSION = 2

INDEX_EXCHANGES = {
    "000001": "sh",
    "000016": "sh",
    "000300": "sh",
    "000852": "sh",
    "000905": "sh",
    "399001": "sz",
    "399006": "sz",
}


INDEX_SYMBOL_ALIASES = {
    "CSI300": "000300",
    "HS300": "000300",
    "沪深300": "000300",
    "上证50": "000016",
    "中证500": "000905",
    "中证1000": "000852",
    "上证指数": "000001",
    "深证成指": "399001",
    "创业板指": "399006",
}


def canonical_index_symbol(value: object) -> str:
    """Return the provider-neutral six-digit code for a mainland index."""
    text = str(value).strip().upper().replace(" ", "")
    if text in {"", "NONE", "NAN", "<NA>", "NAT"}:
        return ""
    if text in INDEX_SYMBOL_ALIASES:
        return INDEX_SYMBOL_ALIASES[text]
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    if match:
        return match.group(1)
    if text.isdigit() and len(text) <= 6:
        return text.zfill(6)
    return text


def index_exchange(value: object) -> str:
    """Return the authoritative exchange prefix for a mainland index."""
    symbol = canonical_index_symbol(value)
    if symbol in INDEX_EXCHANGES:
        return INDEX_EXCHANGES[symbol]
    return "sz" if symbol.startswith("399") else "sh"


def provider_index_symbol(value: object, provider: str) -> str:
    """Map a canonical index identity to a provider-specific symbol."""
    symbol = canonical_index_symbol(value)
    if not symbol:
        return ""
    exchange = index_exchange(symbol)
    normalized_provider = str(provider).strip().lower()
    if normalized_provider == "baostock":
        return f"{exchange}.{symbol}"
    if normalized_provider == "sina":
        return f"{exchange}{symbol}"
    if normalized_provider in {"akshare", "canonical"}:
        return symbol
    raise ValueError(f"Unsupported index symbol provider: {provider}")


def benchmark_index_identity(value: object) -> dict[str, object]:
    """Return the complete, versioned identity used by benchmark data sources."""
    symbol = canonical_index_symbol(value)
    return {
        "benchmark_index_mapping_schema_version": INDEX_MAPPING_SCHEMA_VERSION,
        "benchmark_symbol_canonical": symbol,
        "benchmark_symbol_akshare": provider_index_symbol(symbol, "akshare"),
        "benchmark_symbol_baostock": provider_index_symbol(symbol, "baostock"),
        "benchmark_symbol_sina": provider_index_symbol(symbol, "sina"),
    }