import os
import joblib
import numpy as np
from loguru import logger

MODEL_PATH = "/app/models/xgb_shadow_model.joblib"

class MLPrefilter:
    def __init__(self):
        self.model = None
        if os.path.exists(MODEL_PATH):
            try:
                self.model = joblib.load(MODEL_PATH)
                logger.info(f"✅ ML Prefilter loaded successfully from {MODEL_PATH}")
            except Exception as e:
                logger.error(f"❌ Failed to load ML Prefilter model: {e}")
        else:
            logger.warning(f"⚠️ ML Prefilter model not found at {MODEL_PATH}. Running without ML filter (Fail-Open).")

    def predict_probability(self, features: dict) -> float:
        """Returns the probability of the trade being profitable (0.0 to 1.0)."""
        if self.model is None:
            return 1.0  # Fail-open if model isn't trained yet
        
        # Convert dict to numpy array in the EXACT order the model expects
        feature_vector = np.array([[
            features.get('cvd_slope', 0.0),
            features.get('spread_bps', 0.0),
            features.get('session_mult', 1.0),
            features.get('regime_encoded', 0),
            features.get('atr_pct', 0.0),
            features.get('vol_factor', 1.0),
            features.get('ai_confidence_before', 0.0),
        ]])
        
        # predict_proba returns [[prob_class_0, prob_class_1]]
        try:
            prob = self.model.predict_proba(feature_vector)[0][1]
            return float(prob)
        except Exception as e:
            logger.error(f"❌ ML prediction failed: {e}. Defaulting to fail-open.")
            return 1.0
