"""Deterministic logistic regression scoring and per-feature log-odds contributions from model_registry."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import psycopg


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_EXAMPLE_PATH = ROOT_DIR / ".env.example"

FEATURE_ORDER: list[str] = [
    "income_annual",
    "requested_amount",
    "dti_ratio",
    "credit_score",
    "delinquencies_24m",
    "account_age_months",
    "employment_months",
]

FORBIDDEN_FEATURES: set[str] = {
    "gender",
    "religion",
    "ethnicity",
    "postcode",
    "age_band",
    "test_group",
}


class ScoreResult(dict):
    """Dictionary result of score() that also supports attribute access and (probability, contributions) unpacking."""

    @property
    def probability(self) -> float:
        return float(self["probability"])

    @property
    def contributions(self) -> dict[str, float]:
        return dict(self["contributions"])

    @property
    def per_feature_log_odds(self) -> dict[str, float]:
        return dict(self["per_feature_log_odds"])

    @property
    def log_odds(self) -> float:
        return float(self["log_odds"])

    @property
    def intercept(self) -> float:
        return float(self["intercept"])

    def __iter__(self):
        yield self["probability"]
        yield self["contributions"]


def _get_default_db_url() -> str:
    if "DB_URL" in os.environ and os.environ["DB_URL"].strip():
        return os.environ["DB_URL"].strip()
    if ENV_EXAMPLE_PATH.is_file():
        for raw_line in ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "DB_URL":
                return v.strip()
    raise RuntimeError("DB_URL not found in environment or .env.example")


def canonical_json(obj: Any) -> str:
    """Serialize object to deterministic canonical JSON with sorted keys."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_artifact_sha256(
    *,
    coefficients: dict[str, float] | list[float],
    means: dict[str, float] | list[float],
    stds: dict[str, float] | list[float],
    intercept: float,
    feature_order: list[str],
    preprocessing_version: str,
) -> str:
    """Compute SHA-256 of canonical JSON of coefficients, means, stds, intercept, feature_order, preprocessing_version."""
    payload = {
        "coefficients": coefficients,
        "feature_order": list(feature_order),
        "intercept": float(intercept),
        "means": means,
        "preprocessing_version": str(preprocessing_version),
        "stds": stds,
    }
    encoded = canonical_json(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_feature_param(
    mapping_or_seq: dict[str, Any] | list[Any],
    feature_name: str,
    idx: int,
) -> float:
    if isinstance(mapping_or_seq, dict):
        if feature_name not in mapping_or_seq:
            raise KeyError(f"Feature '{feature_name}' missing from registry parameter mapping")
        return float(mapping_or_seq[feature_name])
    if isinstance(mapping_or_seq, (list, tuple)):
        return float(mapping_or_seq[idx])
    raise TypeError(f"Unsupported registry parameter container type: {type(mapping_or_seq)}")


def _extract_feature_value(features: dict[str, Any] | list[Any], feature_name: str, idx: int) -> float:
    if isinstance(features, dict):
        if feature_name in features:
            return float(features[feature_name])
        if feature_name == "income_annual" and "annual_income" in features:
            return float(features["annual_income"])
        if feature_name == "annual_income" and "income_annual" in features:
            return float(features["income_annual"])
        raise KeyError(f"Required feature '{feature_name}' missing from input features")
    if isinstance(features, (list, tuple)):
        return float(features[idx])
    raise TypeError(f"Unsupported features input type: {type(features)}")


def score(
    features: dict[str, Any] | list[Any],
    registry_row: dict[str, Any],
) -> ScoreResult:
    """Compute predicted probability and per-feature log-odds contributions from a model_registry row.

    Returns a ScoreResult (dict subclass) containing:
      - 'probability': predicted probability in (0, 1) matching scikit-learn predict_proba[:, 1]
      - 'contributions': dict mapping each feature in feature_order to its log-odds contribution
      - 'per_feature_log_odds': alias for 'contributions'
      - 'intercept': float model intercept
      - 'log_odds': total logit (intercept + sum of per-feature contributions)
    Also supports tuple unpacking: `prob, contributions = score(features, registry_row)`.
    """
    if isinstance(features, dict):
        forbidden_input = FORBIDDEN_FEATURES.intersection({k.lower() for k in features.keys()})
        if forbidden_input:
            raise ValueError(f"Forbidden attributes passed to score(): {sorted(forbidden_input)}")

    feature_order: list[str] = list(registry_row.get("feature_order", FEATURE_ORDER))
    forbidden_in_order = FORBIDDEN_FEATURES.intersection({f.lower() for f in feature_order})
    if forbidden_in_order:
        raise ValueError(f"Forbidden features in registry feature_order: {sorted(forbidden_in_order)}")

    coefficients = registry_row["coefficients"]
    means = registry_row.get("means") if "means" in registry_row else registry_row["feature_means"]
    stds = registry_row.get("stds") if "stds" in registry_row else registry_row["feature_stds"]
    intercept = float(registry_row["intercept"])

    contributions: dict[str, float] = {}
    total_log_odds = intercept

    for idx, feat_name in enumerate(feature_order):
        val = _extract_feature_value(features, feat_name, idx)
        mean_val = _resolve_feature_param(means, feat_name, idx)
        std_val = _resolve_feature_param(stds, feat_name, idx)
        coef_val = _resolve_feature_param(coefficients, feat_name, idx)

        if std_val == 0.0:
            raise ValueError(f"Standard deviation for feature '{feat_name}' cannot be zero")

        z_val = (val - mean_val) / std_val
        contrib = coef_val * z_val
        contributions[feat_name] = contrib
        total_log_odds += contrib

    # Numerically stable logistic sigmoid matching scipy.special.expit
    if total_log_odds >= 0.0:
        probability = 1.0 / (1.0 + math.exp(-total_log_odds))
    else:
        exp_x = math.exp(total_log_odds)
        probability = exp_x / (1.0 + exp_x)

    return ScoreResult(
        probability=probability,
        contributions=contributions,
        per_feature_log_odds=contributions,
        intercept=intercept,
        log_odds=total_log_odds,
    )


def load_registry_row(
    model_version: str | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Load a model_registry row from PostgreSQL by model_version."""
    target_version = model_version or os.environ.get("MODEL_VERSION", "loan_lr_v1")
    target_db_url = db_url or _get_default_db_url()

    with psycopg.connect(target_db_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    model_version,
                    artifact_sha256,
                    preprocessing_version,
                    feature_order,
                    intercept,
                    coefficients,
                    feature_means,
                    feature_stds,
                    created_at
                FROM model_registry
                WHERE model_version = %s;
                """,
                (target_version,),
            )
            row = cur.fetchone()

    if row is None:
        raise KeyError(f"Model version '{target_version}' not found in model_registry")

    (
        ver,
        artifact_sha256,
        preprocessing_version,
        feature_order,
        intercept,
        coefficients,
        feature_means,
        feature_stds,
        created_at,
    ) = row

    return {
        "model_version": str(ver),
        "artifact_sha256": str(artifact_sha256),
        "preprocessing_version": str(preprocessing_version),
        "feature_order": list(feature_order),
        "intercept": float(intercept),
        "coefficients": coefficients,
        "feature_means": feature_means,
        "feature_stds": feature_stds,
        "means": feature_means,
        "stds": feature_stds,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
    }

