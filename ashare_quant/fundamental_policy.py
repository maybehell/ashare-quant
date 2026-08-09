from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


FUNDAMENTAL_FACTORS = ("valuation", "quality")


@dataclass(frozen=True)
class FundamentalFactorPolicy:
    source: str
    point_in_time: bool
    explicit: bool
    active_factors: tuple[str, ...]
    max_abs_weight: float

    @property
    def active(self) -> bool:
        return bool(self.active_factors)

    @property
    def usable(self) -> bool:
        return not self.active or self.point_in_time or not self.explicit


def fundamental_factor_policy(config: dict[str, Any]) -> FundamentalFactorPolicy:
    configured = config.get("fundamentals")
    explicit = isinstance(configured, dict)
    section = configured if explicit else {}
    source = str(section.get("source", "unspecified") or "unspecified").strip().lower()
    point_in_time = bool(section.get("point_in_time", False)) if explicit else False
    active_weights = active_fundamental_factor_weights(config.get("strategy", {}))
    active = tuple(name for name in FUNDAMENTAL_FACTORS if active_weights[name] > 1e-12)
    return FundamentalFactorPolicy(
        source=source,
        point_in_time=point_in_time,
        explicit=explicit,
        active_factors=active,
        max_abs_weight=max(active_weights.values(), default=0.0),
    )


def active_fundamental_factor_weights(strategy: object) -> dict[str, float]:
    active = {name: 0.0 for name in FUNDAMENTAL_FACTORS}
    for weights in _weight_mappings(strategy):
        for name in FUNDAMENTAL_FACTORS:
            try:
                value = abs(float(weights.get(name, 0.0)))
            except (TypeError, ValueError):
                continue
            active[name] = max(active[name], value)
    return active


def assert_fundamental_factor_policy(
    config: dict[str, Any],
    weights: dict[str, float],
    *,
    data_available: bool | None = None,
) -> None:
    active = []
    for name in FUNDAMENTAL_FACTORS:
        try:
            if abs(float(weights.get(name, 0.0))) > 1e-12:
                active.append(name)
        except (TypeError, ValueError):
            active.append(name)
    if not active:
        return

    policy = fundamental_factor_policy(config)
    if policy.explicit and not policy.point_in_time:
        joined = ", ".join(active)
        raise ValueError(
            f"fundamental factors ({joined}) require point-in-time data; "
            f"configured source is {policy.source!r}"
        )
    if data_available is False:
        raise ValueError("active fundamental factors require non-empty fundamental data")


def _weight_mappings(value: object, parent_key: str = "") -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if parent_key == "weights" or parent_key.endswith("_weights"):
            yield value
        for key, nested in value.items():
            yield from _weight_mappings(nested, str(key))
    elif isinstance(value, list):
        for nested in value:
            yield from _weight_mappings(nested, parent_key)
