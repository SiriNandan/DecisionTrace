# Model Card: `loan_lr_v1`

> **SYNTHETIC DATA - FOR DEMONSTRATION.**
> This model card documents the baseline credit risk logistic regression model used in DecisionTrace. The registered model parameters and SHA-256 digest (`artifact_sha256`) are tamper-evident against a retained reference and the resulting record supports audit and explanation.

## 1. Model Overview
- **Model Version**: `loan_lr_v1`
- **Estimator**: `scikit-learn` `LogisticRegression` (L2 penalty, `lbfgs` solver)
- **Preprocessing Version**: `standard_scaler_v1` (`StandardScaler` fit strictly on the 80% training split)
- **Target Variable**: `outcome_default_12m` (12-month synthetic default indicator; `1` = default, `0` = non-default)
- **Intercept (log-odds)**: `-3.77104569`
- **Artifact SHA-256**: `95457108fcb77d33332139d6c23c2272a90a025e2e97639a6902206ce60b2232`

## 2. Training Data
- **Source Dataset**: `data/applicants.csv` (deterministic synthetic retail loan dataset generated under profile `uk_retail`)
- **Total Records**: 2000 applicants (checked for missing or non-finite values prior to splitting)
- **Split Strategy**: 80/20 stratified train/holdout split with fixed random seed `42` (`1600` training rows, `400` holdout test rows)
- **Featurization Ordering**: The dataset is split into training and holdout sets before fitting `StandardScaler`. Means and standard deviations are estimated exclusively on the `1600` training rows and applied without refitting to holdout and inference records.

## 3. Features & Learned Parameters
The model uses the following 7 numerical features in canonical `feature_order`:

| Feature (`feature_order`) | Training Mean (`means`) | Training Std (`stds`) | Standardized Coefficient (`coefficients`) |
| :--- | ---: | ---: | ---: |
| `income_annual` | 49986.1565 | 29422.9064 | -0.328697 |
| `requested_amount` | 16263.0603 | 12277.8188 | +0.341748 |
| `dti_ratio` | 0.2383 | 0.1293 | +1.647922 |
| `credit_score` | 601.5469 | 75.6098 | -2.208673 |
| `delinquencies_24m` | 0.2362 | 0.5054 | +1.268526 |
| `account_age_months` | 123.4562 | 82.4646 | -1.019670 |
| `employment_months` | 97.7450 | 70.9221 | -0.001606 |

## 4. Excluded Attributes
To uphold feature governance rules, the following sensitive, demographic, and evaluation-only attributes are strictly excluded from training and inference:
- **Never collected or stored in applicant datasets**: `gender`, `religion`, `ethnicity`, `postcode`
- **Stored on applicant record for distributional checks only (excluded from model features)**: `age_band`
- **Stored in separate evaluation table `fairness_groups` only (never used as a model feature)**: `test_group`

## 5. Holdout Metrics (20% Test Split, N=400)
- **ROC-AUC**: `0.9789`
- **Accuracy**: `0.9400`
- **Confusion Matrix** (`rows = actual [0, 1]`, `columns = predicted [0, 1]`):

| | Predicted `0` (Non-Default) | Predicted `1` (Default) |
| :--- | ---: | ---: |
| **Actual `0` (Non-Default)** | 314 (TN) | 7 (FP) |
| **Actual `1` (Default)** | 17 (FN) | 62 (TP) |

## 6. Limitations & Governance Considerations
- **Synthetic Data Scope**: Trained exclusively on synthetic data (`SYNTHETIC DATA - FOR DEMONSTRATION.`) with simulated logistic default dynamics; not calibrated for real-world credit underwriting.
- **Linear Log-Odds Assumption**: `LogisticRegression` assumes a linear additive relationship between standardized features and log-odds of default, which may not capture non-linear interactions without feature engineering.
- **Proxy Effects**: Permitted operational features such as `employment_months` or `account_age_months` can correlate with age cohorts or evaluation groups (`fairness_groups`), producing disparate selection rates across subpopulations that require separate fairness evaluation (`eval/fairness.py`).
- **Auditability Scope**: Freezing `coefficients`, `means`, `stds`, `intercept`, `feature_order`, and `preprocessing_version` under `artifact_sha256` makes the model configuration tamper-evident against a retained reference and supports audit and explanation of individual per-feature log-odds contributions.
