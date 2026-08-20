import numpy as np
import pytest
from src.pipeline import CrossValidationPipeline, TargetEncoder


def test_cross_validation_no_target_leakage():
    """Verifies that validation fold scores are not artificially zero/leaked."""
    np.random.seed(42)
    # 3 distinct categories with random noise target
    categories = np.array(["A", "A", "B", "B", "C", "C", "A", "B", "C"])
    # Random target uncorrelated with category
    targets = np.array([1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.5, 0.5, 0.5])

    pipeline = CrossValidationPipeline(n_splits=3)
    scores = pipeline.run_cv(categories, targets)

    # When leakage occurs, the training target means include the validation row,
    # pulling predictions unrealistically close to val_targets.
    # On proper atomic folding, validation fold MSE should be calculated strictly
    # with an encoder fitted ONLY on train_idx.
    
    # Test strict atomic folding invariant:
    # An independent encoder fit only on train folds must produce non-leaked predictions
    for i in range(pipeline.n_splits):
        indices = np.arange(len(categories))
        val_idx = indices[i * 3 : (i + 1) * 3]
        train_idx = np.setdiff1d(indices, val_idx)
        
        independent_encoder = TargetEncoder().fit(categories[train_idx], targets[train_idx])
        expected_val_features = independent_encoder.transform(categories[val_idx])
        expected_mse = float(np.mean((expected_val_features - targets[val_idx]) ** 2))
        
        # Pipeline must match independent out-of-fold calculation exactly
        assert pytest.approx(scores[i], rel=1e-3) == expected_mse
