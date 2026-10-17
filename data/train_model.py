# SYNTHETIC DATA - FOR DEMONSTRATION.
"""Train scikit-learn LogisticRegression with StandardScaler, populate model_registry, and write docs/model_card.md."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np
import psycopg
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.scoring import (
    FEATURE_ORDER,
    FORBIDDEN_FEATURES,
    compute_artifact_sha256,
)
from data.load_policies import get_db_url


DATA_DIR = Path(__file__).resolve().parent
DEFAULT_APPLICANTS_CSV = DATA_DIR / "applicants.csv"
DEFAULT_MODEL_CARD_PATH = ROOT_DIR / "docs" / "model_card.md"
DEFAULT_PREPROCESSING_VERSION = "standard_scaler_v1"
DEFAULT_SEED = 42


def _read_applicants_csv(csv_path: Path) -> list[dict[str, str]]:
    """Read synthetic applicants.csv, skipping the # SYNTHETIC DATA header comment line."""
    with csv_path.open("r", encoding="utf-8") as f:
        lines = [line for line in f if not line.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def load_training_matrix(
    csv_path: Path = DEFAULT_APPLICANTS_CSV,
    feature_order: list[str] = FEATURE_ORDER,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, float]]]:
    """Load feature matrix X and binary target y (outcome_default_12m) from applicants.csv.

    Checks for missing/null values and verifies forbidden attributes are never included in features.
    """
    forbidden_check = FORBIDDEN_FEATURES.intersection({f.lower() for f in feature_order})
    if forbidden_check:
        raise ValueError(f"Forbidden features requested: {sorted(forbidden_check)}")

    rows = _read_applicants_csv(csv_path)
    if not rows:
        raise ValueError(f"No applicant rows found in {csv_path}")

    X_list: list[list[float]] = []
    y_list: list[int] = []
    feature_dicts: list[dict[str, float]] = []

    for row_idx, row in enumerate(rows):
        target_raw = row.get("outcome_default_12m")
        if target_raw is None or str(target_raw).strip() == "":
            raise ValueError(f"Missing target outcome_default_12m at row {row_idx}")

        feat_dict: dict[str, float] = {}
        feat_vec: list[float] = []
        for feat in feature_order:
            if feat == "income_annual":
                raw_val = row.get("income_annual", row.get("annual_income"))
            else:
                raw_val = row.get(feat)

            if raw_val is None or str(raw_val).strip() == "":
                raise ValueError(f"Missing feature '{feat}' at row {row_idx}")

            val = float(raw_val)
            if np.isnan(val) or np.isinf(val):
                raise ValueError(f"Non-finite value for feature '{feat}' at row {row_idx}")

            feat_dict[feat] = val
            feat_vec.append(val)

        X_list.append(feat_vec)
        y_list.append(int(target_raw))
        feature_dicts.append(feat_dict)

    return np.asarray(X_list, dtype=np.float64), np.asarray(y_list, dtype=np.int64), feature_dicts


def upsert_model_registry(
    registry_row: dict[str, Any],
    db_url: str | None = None,
) -> None:
    """Insert or update the trained model row in PostgreSQL model_registry."""
    target_url = get_db_url(db_url)
    created_at = datetime.now(timezone.utc)

    with psycopg.connect(target_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO model_registry (
                    model_version,
                    artifact_sha256,
                    preprocessing_version,
                    feature_order,
                    intercept,
                    coefficients,
                    feature_means,
                    feature_stds,
                    created_at
                )
                VALUES (%s, %s, %s, %s::jsonb, %s::numeric, %s::jsonb, %s::jsonb, %s::jsonb, %s)
                ON CONFLICT (model_version) DO UPDATE SET
                    artifact_sha256 = EXCLUDED.artifact_sha256,
                    preprocessing_version = EXCLUDED.preprocessing_version,
                    feature_order = EXCLUDED.feature_order,
                    intercept = EXCLUDED.intercept,
                    coefficients = EXCLUDED.coefficients,
                    feature_means = EXCLUDED.feature_means,
                    feature_stds = EXCLUDED.feature_stds,
                    created_at = EXCLUDED.created_at;
                """,
                (
                    registry_row["model_version"],
                    registry_row["artifact_sha256"],
                    registry_row["preprocessing_version"],
                    json.dumps(registry_row["feature_order"]),
                    repr(float(registry_row["intercept"])),
                    json.dumps(registry_row["coefficients"]),
                    json.dumps(registry_row["feature_means"]),
                    json.dumps(registry_row["feature_stds"]),
                    created_at,
                ),
            )
        conn.commit()


def render_model_card(
    registry_row: dict[str, Any],
    metrics: dict[str, Any],
    seed: int,
    train_size: int,
    test_size: int,
) -> str:
    """Render docs/model_card.md following governance and wording rules."""
    cm = metrics["confusion_matrix"]
    tn, fp = int(cm[0][0]), int(cm[0][1])
    fn, tp = int(cm[1][0]), int(cm[1][1])

    feature_lines = []
    for feat in registry_row["feature_order"]:
        mean_v = registry_row["means"][feat]
        std_v = registry_row["stds"][feat]
        coef_v = registry_row["coefficients"][feat]
        feature_lines.append(
            f"| `{feat}` | {mean_v:.4f} | {std_v:.4f} | {coef_v:+.6f} |"
        )
    feature_table = "\n".join(feature_lines)

    return f"""# Model Card: `{registry_row['model_version']}`

> **SYNTHETIC DATA - FOR DEMONSTRATION.**
> This model card documents the baseline credit risk logistic regression model used in DecisionTrace. The registered model parameters and SHA-256 digest (`artifact_sha256`) are tamper-evident against a retained reference and the resulting record supports audit and explanation.

## 1. Model Overview
- **Model Version**: `{registry_row['model_version']}`
- **Estimator**: `scikit-learn` `LogisticRegression` (L2 penalty, `lbfgs` solver)
- **Preprocessing Version**: `{registry_row['preprocessing_version']}` (`StandardScaler` fit strictly on the 80% training split)
- **Target Variable**: `outcome_default_12m` (12-month synthetic default indicator; `1` = default, `0` = non-default)
- **Intercept (log-odds)**: `{registry_row['intercept']:.8f}`
- **Artifact SHA-256**: `{registry_row['artifact_sha256']}`

## 2. Training Data
- **Source Dataset**: `data/applicants.csv` (deterministic synthetic retail loan dataset generated under profile `uk_retail`)
- **Total Records**: {train_size + test_size} applicants (checked for missing or non-finite values prior to splitting)
- **Split Strategy**: 80/20 stratified train/holdout split with fixed random seed `{seed}` (`{train_size}` training rows, `{test_size}` holdout test rows)
- **Featurization Ordering**: The dataset is split into training and holdout sets before fitting `StandardScaler`. Means and standard deviations are estimated exclusively on the `{train_size}` training rows and applied without refitting to holdout and inference records.

## 3. Features & Learned Parameters
The model uses the following 7 numerical features in canonical `feature_order`:

| Feature (`feature_order`) | Training Mean (`means`) | Training Std (`stds`) | Standardized Coefficient (`coefficients`) |
| :--- | ---: | ---: | ---: |
{feature_table}

## 4. Excluded Attributes
To uphold feature governance rules, the following sensitive, demographic, and evaluation-only attributes are strictly excluded from training and inference:
- **Never collected or stored in applicant datasets**: `gender`, `religion`, `ethnicity`, `postcode`
- **Stored on applicant record for distributional checks only (excluded from model features)**: `age_band`
- **Stored in separate evaluation table `fairness_groups` only (never used as a model feature)**: `test_group`

## 5. Holdout Metrics (20% Test Split, N={test_size})
- **ROC-AUC**: `{metrics['auc']:.4f}`
- **Accuracy**: `{metrics['accuracy']:.4f}`
- **Confusion Matrix** (`rows = actual [0, 1]`, `columns = predicted [0, 1]`):

| | Predicted `0` (Non-Default) | Predicted `1` (Default) |
| :--- | ---: | ---: |
| **Actual `0` (Non-Default)** | {tn} (TN) | {fp} (FP) |
| **Actual `1` (Default)** | {fn} (FN) | {tp} (TP) |

## 6. Limitations & Governance Considerations
- **Synthetic Data Scope**: Trained exclusively on synthetic data (`SYNTHETIC DATA - FOR DEMONSTRATION.`) with simulated logistic default dynamics; not calibrated for real-world credit underwriting.
- **Linear Log-Odds Assumption**: `LogisticRegression` assumes a linear additive relationship between standardized features and log-odds of default, which may not capture non-linear interactions without feature engineering.
- **Proxy Effects**: Permitted operational features such as `employment_months` or `account_age_months` can correlate with age cohorts or evaluation groups (`fairness_groups`), producing disparate selection rates across subpopulations that require separate fairness evaluation (`eval/fairness.py`).
- **Auditability Scope**: Freezing `coefficients`, `means`, `stds`, `intercept`, `feature_order`, and `preprocessing_version` under `artifact_sha256` makes the model configuration tamper-evident against a retained reference and supports audit and explanation of individual per-feature log-odds contributions.
"""


def train_and_register_model(
    csv_path: Path = DEFAULT_APPLICANTS_CSV,
    model_card_path: Path = DEFAULT_MODEL_CARD_PATH,
    model_version: str | None = None,
    preprocessing_version: str = DEFAULT_PREPROCESSING_VERSION,
    seed: int = DEFAULT_SEED,
    sync_db: bool = True,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Train StandardScaler + LogisticRegression on 80/20 split, register model, and write model_card.md."""
    resolved_model_version = model_version or os.environ.get("MODEL_VERSION", "loan_lr_v1")
    feature_order = list(FEATURE_ORDER)

    X, y, feature_dicts = load_training_matrix(csv_path=csv_path, feature_order=feature_order)

    indices = np.arange(len(y))
    X_train, X_test, y_train, y_test, idx_train, idx_test = train_test_split(
        X,
        y,
        indices,
        test_size=0.20,
        random_state=seed,
        stratify=y,
    )

    # Fit StandardScaler strictly on training split
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # Fit LogisticRegression with fixed seed
    clf = LogisticRegression(random_state=seed, max_iter=1000)
    clf.fit(X_train_scaled, y_train)

    y_prob = clf.predict_proba(X_test_scaled)[:, 1]
    y_pred = clf.predict(X_test_scaled)

    auc = float(roc_auc_score(y_test, y_prob))
    acc = float(accuracy_score(y_test, y_pred))
    cm = confusion_matrix(y_test, y_pred).tolist()

    coefficients = {
        feat: float(clf.coef_[0][i]) for i, feat in enumerate(feature_order)
    }
    means = {
        feat: float(scaler.mean_[i]) for i, feat in enumerate(feature_order)
    }
    stds = {
        feat: float(scaler.scale_[i]) for i, feat in enumerate(feature_order)
    }
    intercept = float(clf.intercept_[0])

    artifact_sha256 = compute_artifact_sha256(
        coefficients=coefficients,
        means=means,
        stds=stds,
        intercept=intercept,
        feature_order=feature_order,
        preprocessing_version=preprocessing_version,
    )

    registry_row: dict[str, Any] = {
        "model_version": resolved_model_version,
        "artifact_sha256": artifact_sha256,
        "preprocessing_version": preprocessing_version,
        "feature_order": feature_order,
        "intercept": intercept,
        "coefficients": coefficients,
        "feature_means": means,
        "feature_stds": stds,
        "means": means,
        "stds": stds,
    }

    metrics = {
        "auc": auc,
        "accuracy": acc,
        "confusion_matrix": cm,
    }

    if sync_db:
        upsert_model_registry(registry_row, db_url=db_url)

    model_card_path.parent.mkdir(parents=True, exist_ok=True)
    card_markdown = render_model_card(
        registry_row=registry_row,
        metrics=metrics,
        seed=seed,
        train_size=len(y_train),
        test_size=len(y_test),
    )
    model_card_path.write_text(card_markdown, encoding="utf-8")

    print(f"Model Version: {resolved_model_version}")
    print(f"Artifact SHA-256: {artifact_sha256}")
    print(f"Holdout ROC-AUC: {auc:.4f}")
    print(f"Holdout Accuracy: {acc:.4f}")
    print(f"Holdout Confusion Matrix:\n{np.array(cm)}")

    test_feature_dicts = [feature_dicts[int(i)] for i in idx_test]

    return {
        "model": clf,
        "scaler": scaler,
        "registry_row": registry_row,
        "metrics": metrics,
        "X_train": X_train,
        "X_test": X_test,
        "y_train": y_train,
        "y_test": y_test,
        "test_feature_dicts": test_feature_dicts,
        "model_card_path": model_card_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train LogisticRegression model, write model_registry row, and generate docs/model_card.md."
    )
    parser.add_argument("--applicants-csv", type=Path, default=DEFAULT_APPLICANTS_CSV)
    parser.add_argument("--model-card", type=Path, default=DEFAULT_MODEL_CARD_PATH)
    parser.add_argument("--model-version", type=str, default=None)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    train_and_register_model(
        csv_path=args.applicants_csv,
        model_card_path=args.model_card,
        model_version=args.model_version,
        seed=args.seed,
        sync_db=True,
    )


if __name__ == "__main__":
    main()

