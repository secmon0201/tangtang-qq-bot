"""Compatibility exports for the application-level local feature registry."""

from bot.application.local_features import (
    FeatureRequest,
    feature_label,
    register_local_feature,
    registered_local_features,
    request_from_decision,
    run_feature_call,
)


__all__ = [
    "FeatureRequest",
    "feature_label",
    "register_local_feature",
    "registered_local_features",
    "request_from_decision",
    "run_feature_call",
]
