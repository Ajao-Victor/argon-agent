"""Classes the Colab pickle may reference. Maps __main__ names on load."""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np


class DirectionalLightGBM:
    """Classifier decides sign; regressor supplies magnitude."""

    def __init__(
        self,
        classifier,
        regressor,
        feature_cols,
        threshold=0.5,
        deadzone=0.0,
        fade_col=None,
        calibrator=None,
        median_abs_return=0.01,
    ):
        self.classifier = classifier
        self.regressor = regressor
        self.feature_cols = list(feature_cols)
        self.threshold = float(threshold)
        self.deadzone = float(deadzone)
        self.fade_col = fade_col
        self.calibrator = calibrator
        self.median_abs_return = median_abs_return
        self.feature_importances_ = getattr(classifier, "feature_importances_", None)

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.threshold = float(getattr(self, "threshold", 0.5))
        self.deadzone = float(getattr(self, "deadzone", 0.0))
        self.feature_cols = list(getattr(self, "feature_cols", []))

    def _select_features(self, X):
        if hasattr(X, "loc"):
            missing = [c for c in self.feature_cols if c not in X.columns]
            if missing:
                raise ValueError(f"Missing features: {missing[:8]}")
            return X[self.feature_cols]
        return X

    def _calibrate_proba(self, proba):
        if self.calibrator is None:
            return proba
        return np.clip(self.calibrator.predict(proba), 1e-6, 1 - 1e-6)

    def predict_direction(self, X):
        X_use = self._select_features(X)
        proba_up = self._calibrate_proba(self.classifier.predict_proba(X_use)[:, 1])
        model_dir = np.where(proba_up >= self.threshold, 1.0, -1.0)
        if self.deadzone <= 0 or self.fade_col is None or self.fade_col not in getattr(X_use, "columns", []):
            return model_dir, proba_up
        fade = np.sign(np.asarray(X_use[self.fade_col]).ravel())
        fade = np.where(fade == 0, model_dir, fade)
        unsure = np.abs(proba_up - self.threshold) < self.deadzone
        return np.where(unsure, fade, model_dir), proba_up

    def predict(self, X):
        X_use = self._select_features(X)
        direction, _ = self.predict_direction(X_use)
        magnitude = np.maximum(np.abs(self.regressor.predict(X_use)), 1e-8)
        return direction * magnitude


class _ColabUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        aliases = {
            "DirectionalLightGBM": DirectionalLightGBM,
        }
        if name in aliases and module in ("__main__", "eth_hourly_prediction"):
            return aliases[name]
        try:
            return super().find_class(module, name)
        except (AttributeError, ModuleNotFoundError):
            if name in aliases:
                return aliases[name]
            raise


def load_bundle(path: Path) -> dict:
    with open(path, "rb") as f:
        return _ColabUnpickler(f).load()
