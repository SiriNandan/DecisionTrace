"""Tests for Task B1: data/train_model.py, model_registry row, docs/model_card.md, and backend/scoring.py."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from backend.scoring import (
    FEATURE_ORDER,
    compute_artifact_sha256,
    load_registry_row,
    score,
)
from data.train_model import (
    DEFAULT_MODEL_CARD_PATH,
    train_and_register_model,
)


def test_train_model_and_registry_predict_proba_match() -> None:
    """Verify registry row fields, canonical artifact_sha256, model_card.md, and 1e-9 predict_proba match."""
    bundle = train_and_register_model(sync_db=True)
    clf = bundle["model"]
    scaler = bundle["scaler"]
    X_test = bundle["X_test"]
    test_feature_dicts = bundle["test_feature_dicts"]

    # Load registry row back from PostgreSQL model_registry table
    db_registry_row = load_registry_row(bundle["registry_row"]["model_version"])

    expected_features = [
        "income_annual",
        "requested_amount",
        "dti_ratio",
        "credit_score",
        "delinquencies_24m",
        "account_age_months",
        "employment_months",
    ]
    assert db_registry_row["feature_order"] == expected_features
    assert FEATURE_ORDER == expected_features
    assert db_registry_row["preprocessing_version"] == "standard_scaler_v1"

    # Verify artifact_sha256 is SHA-256 of canonical JSON of all 6 fields
    canonical_payload = {
        "coefficients": db_registry_row["coefficients"],
        "feature_order": db_registry_row["feature_order"],
        "intercept": float(db_registry_row["intercept"]),
        "means": db_registry_row["feature_means"],
        "preprocessing_version": db_registry_row["preprocessing_version"],
        "stds": db_registry_row["feature_stds"],
    }
    expected_sha = hashlib.sha256(
        json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    assert db_registry_row["artifact_sha256"] == expected_sha
    assert (
        compute_artifact_sha256(
            coefficients=db_registry_row["coefficients"],
            means=db_registry_row["means"],
            stds=db_registry_row["stds"],
            intercept=db_registry_row["intercept"],
            feature_order=db_registry_row["feature_order"],
            preprocessing_version=db_registry_row["preprocessing_version"],
        )
        == expected_sha
    )

    # Compare backend.scoring.score(features, db_registry_row) against scikit-learn predict_proba within 1e-9
    X_test_scaled = scaler.transform(X_test)
    sklearn_probs = clf.predict_proba(X_test_scaled)[:, 1]
    sklearn_logits = clf.decision_function(X_test_scaled)

    assert len(test_feature_dicts) == 400
    for idx, feat_dict in enumerate(test_feature_dicts):
        res = score(feat_dict, db_registry_row)
        prob, contribs = res

        assert abs(prob - float(sklearn_probs[idx])) <= 1e-9, (
            f"Row {idx} probability mismatch: {prob} vs {sklearn_probs[idx]}"
        )
        assert abs(res["probability"] - float(sklearn_probs[idx])) <= 1e-9

        # Verify per-feature log-odds contributions sum + intercept == sklearn decision_function
        assert list(contribs.keys()) == expected_features
        reconstructed_logit = float(db_registry_row["intercept"]) + sum(contribs.values())
        assert abs(reconstructed_logit - float(sklearn_logits[idx])) <= 1e-9

    # Verify docs/model_card.md was written and contains required sections & governance wording
    assert DEFAULT_MODEL_CARD_PATH.is_file()
    card_text = DEFAULT_MODEL_CARD_PATH.read_text(encoding="utf-8")
    card_lower = card_text.lower()
    assert "training data" in card_lower
    assert "features" in card_lower
    assert "excluded attributes" in card_lower
    assert "holdout metrics" in card_lower
    assert "limitations" in card_lower
    assert "supports audit and explanation" in card_lower
    assert "tamper-evident against a retained reference" in card_lower
    assert "ensures compliance" not in card_lower
    assert "proves" not in card_lower


def test_scoring_rejects_forbidden_attributes() -> None:
    """Verify score() rejects forbidden demographic or evaluation-only attributes."""
    bundle = train_and_register_model(sync_db=False)
    reg = bundle["registry_row"]
    base_features = dict(bundle["test_feature_dicts"][0])

    for forbidden in ("gender", "religion", "ethnicity", "postcode", "age_band", "test_group"):
        bad_features = dict(base_features)
        bad_features[forbidden] = 1
        with pytest.raises(ValueError, match="Forbidden"):
            score(bad_features, reg)

