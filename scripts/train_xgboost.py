import pandas as pd
import xgboost as xgb
import joblib
import psycopg2
from loguru import logger
import os
import argparse

DB_URI = os.getenv("DATABASE_URL", "postgresql://karsa:karsa@karsa-postgres:5432/karsa")
MODEL_PATH = "models/xgb_shadow_model.joblib"

def fetch_shadow_data():
    """Pulls the last 30 days of shadow trade features and outcomes."""
    conn = psycopg2.connect(DB_URI)
    query = """
        SELECT cvd_slope, spread_bps, session_mult, regime_encoded, 
               atr_pct, vol_factor, ai_confidence_before, 
               CASE WHEN pnl > 0 THEN 1 ELSE 0 END as is_profitable
        FROM shadow_trades
        WHERE created_at > NOW() - INTERVAL '30 days'
        AND cvd_slope IS NOT NULL; -- Ensure features were logged
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df

def train_and_save():
    logger.info("🧠 Fetching shadow data for XGBoost training...")
    df = fetch_shadow_data()
    
    # Handle legacy missing data if any
    df = df.dropna(subset=['cvd_slope', 'spread_bps', 'regime_encoded'])
    
    if len(df) < 100:
        logger.warning(f"⚠️ Not enough data to train ({len(df)} rows). Need at least 100.")
        return

    X = df.drop(columns=['is_profitable'])
    y = df['is_profitable']

    logger.info(f"📊 Training XGBoost on {len(X)} samples. Win rate: {y.mean():.2%}")

    # Institutional-grade hyperparameters for tabular financial data
    model = xgb.XGBClassifier(
        n_estimators=150,
        max_depth=4,             # Prevent overfitting
        learning_rate=0.05,      # Slow learning
        subsample=0.8,           # Row sampling
        colsample_bytree=0.8,    # Feature sampling
        eval_metric='logloss'
    )
    
    model.fit(X, y)
    
    # Ensure models directory exists
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    logger.info(f"✅ Model successfully trained and saved to {MODEL_PATH}")

if __name__ == "__main__":
    train_and_save()
