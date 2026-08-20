"""Machine learning cross-validation feature pipeline with data leakage flaw."""

import numpy as np


class TargetEncoder:
    """Target encoder for categorical features."""

    def __init__(self, smoothing=1.0):
        self.smoothing = smoothing
        self.target_means_ = {}

    def fit(self, X, y):
        """Fits target encodings based on training target values."""
        self.target_means_ = {}
        unique_categories = np.unique(X)
        global_mean = np.mean(y)
        for cat in unique_categories:
            mask = (X == cat)
            cat_mean = np.mean(y[mask])
            self.target_means_[cat] = cat_mean
        return self

    def transform(self, X):
        """Transforms categories into target means."""
        global_mean = 0.0
        return np.array([self.target_means_.get(cat, global_mean) for cat in X])


class CrossValidationPipeline:
    """Evaluates features using K-fold cross validation."""

    def __init__(self, n_splits=3):
        self.n_splits = n_splits
        self.encoder = TargetEncoder()

    def run_cv(self, X, y):
        """Runs cross-validation.
        
        FLAW: Fits the encoder on the ENTIRE dataset before splitting into folds,
        causing target leakage into validation folds.
        """
        # BUG: Pre-fitting on full dataset
        self.encoder.fit(X, y)
        encoded_X = self.encoder.transform(X)

        fold_scores = []
        n = len(X)
        indices = np.arange(n)
        fold_size = n // self.n_splits

        for i in range(self.n_splits):
            val_idx = indices[i * fold_size : (i + 1) * fold_size]
            train_idx = np.setdiff1d(indices, val_idx)

            # Evaluate on validation fold
            val_features = encoded_X[val_idx]
            val_targets = y[val_idx]
            mse = np.mean((val_features - val_targets) ** 2)
            fold_scores.append(float(mse))

        return fold_scores
