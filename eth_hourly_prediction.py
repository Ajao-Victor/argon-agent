import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import lightgbm as lgb
from sklearn.preprocessing import RobustScaler
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import mean_squared_error, mean_absolute_error
import os
import pickle
import warnings
import requests
from datetime import datetime, timedelta

PICKLE_PATH = 'eth_8h_lgbm.pkl'

warnings.filterwarnings('ignore')

# Set TIINGO_API_KEY in the environment. Do not hardcode tokens in this file.
TIINGO_API_KEY = os.environ.get('TIINGO_API_KEY', '')

PREDICTION_HORIZON_HOURS = 8
HORIZON_KEY = f'{PREDICTION_HORIZON_HOURS}h'

try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False
    print("TA-Lib not available. Using manual technical indicators.")


class ZPTAELoss:
    """Z Power-Tanh Absolute Error (Allora-style)."""

    @staticmethod
    def power_tanh(x, alpha=0.25, beta=2):
        return x / (1 + np.abs(x)**beta)**((1 - alpha) / beta)

    @staticmethod
    def loss_zptae(y_true, y_pred, sigma, mean=0, alpha=0.25, beta=2,
                   gamma=4, penalty_norm=0.01):
        z_true = (y_true - mean) / sigma
        z_pred = (y_pred - mean) / sigma
        pt_true = ZPTAELoss.power_tanh(z_true, alpha=alpha, beta=beta)
        pt_pred = ZPTAELoss.power_tanh(z_pred, alpha=alpha, beta=beta)
        return np.abs(pt_pred - pt_true) + (penalty_norm * np.abs(z_pred - z_true))**gamma


class DataIngestion:
    """Tiingo (training bars) + DIA (spot)."""

    def __init__(self, tiingo_api_key=None):
        env_key = (os.environ.get('TIINGO_API_KEY') or '').strip()
        self.tiingo_api_key = (tiingo_api_key or env_key or TIINGO_API_KEY).strip()
        self.tiingo_base_url = 'https://api.tiingo.com/tiingo/crypto/prices'
        self.dia_base_url = (
            'https://api.diadata.org/v1/assetQuotation/Ethereum/'
            '0x0000000000000000000000000000000000000000'
        )
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Token {self.tiingo_api_key}'
        }
        self.data = None
        self.last_data_timestamp = None

    def fetch_current_price_from_dia(self):
        try:
            print("Fetching current ETH price from DIA API...")
            response = requests.get(self.dia_base_url, timeout=10)
            if response.status_code != 200:
                print(f"DIA API request failed: {response.status_code}")
                return None, None
            data = response.json()
            current_price = data['Price']
            timestamp = pd.to_datetime(data['Time'])
            print(f"Current ETH price from DIA: ${current_price:.4f}")
            print(f"Price timestamp: {timestamp}")
            return current_price, timestamp
        except Exception as e:
            print(f"Error fetching DIA data: {e}")
            return None, None

    def fetch_eth_training_data(self, days_back=730, resample_freq='1hour'):
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)
            start_str = start_date.strftime('%Y-%m-%d')
            end_str = end_date.strftime('%Y-%m-%d')
            print(f"Fetching ETH/USDT data from Tiingo ({start_str} to {end_str}, {resample_freq})...")
            params = {
                'tickers': 'ethusd',
                'startDate': start_str,
                'endDate': end_str,
                'resampleFreq': resample_freq,
                'token': self.tiingo_api_key,
            }
            response = requests.get(self.tiingo_base_url, headers=self.headers, params=params)
            if response.status_code != 200:
                print(f"Tiingo API failed: {response.status_code}")
                print(response.text)
                return None
            data_json = response.json()
            if not data_json:
                print("No data returned from Tiingo API")
                return None
            eth_data = (data_json[0]['priceData'] if isinstance(data_json, list)
                        else data_json['priceData'])
            if not eth_data:
                print("No price data found for ETH/USDT")
                return None
            rows = [{
                'Date': pd.to_datetime(p['date']),
                'Open': p['open'],
                'High': p['high'],
                'Low': p['low'],
                'Close': p['close'],
                'Volume': p['volume']
            } for p in eth_data]
            data = pd.DataFrame(rows).set_index('Date').sort_index().dropna()
            if data.empty:
                raise ValueError("No valid data after cleaning")
            self.last_data_timestamp = data.index[-1]
            data['traditional_log_return'] = np.log(data['Close'] / data['Close'].shift(1))
            # True 8h log-return, not the 1h return sitting 8 bars ahead.
            data[f'log_return_{HORIZON_KEY}'] = np.log(
                data['Close'].shift(-PREDICTION_HORIZON_HOURS) / data['Close']
            )
            self.data = data.dropna()
            print(f"Loaded {len(self.data)} hourly bars from Tiingo")
            print(f"Range: {self.data.index.min()} → {self.data.index.max()}")
            print(f"Price: ${self.data['Close'].min():.2f} – ${self.data['Close'].max():.2f}")
            return self.data
        except Exception as e:
            print(f"Error fetching training data: {e}")
            return None


class FeatureEngineering:
    def __init__(self, lookback_windows=None):
        if lookback_windows is None:
            lookback_windows = [PREDICTION_HORIZON_HOURS, 12, 24, 48, 96, 168]
        self.lookback_windows = lookback_windows
        self.scaler = RobustScaler()
        self.feature_columns = []

    def create_features(self, data):
        df = data.copy()
        ret = df['traditional_log_return']

        for lag in sorted({1, 2, 3, 4, PREDICTION_HORIZON_HOURS, 12, 24, 48, 72, 96, 168}):
            df[f'ret_lag_{lag}'] = ret.shift(lag)

        for window in sorted({4, PREDICTION_HORIZON_HOURS, 12, 24, 48}):
            df[f'ret_sum_{window}h'] = ret.rolling(window).sum()
            df[f'ret_sum_sign_{window}h'] = np.sign(df[f'ret_sum_{window}h'])
            # 8h ETH tends to fade, not follow, recent momentum.
            df[f'ret_fade_{window}h'] = -df[f'ret_sum_{window}h']

        for window in self.lookback_windows:
            df[f'vol_std_{window}h'] = ret.rolling(window).std()
            df[f'vol_ewma_{window}h'] = ret.ewm(halflife=window / 2).std()
            df[f'realized_vol_{window}h'] = np.sqrt(ret.pow(2).rolling(window).sum())

        vol24 = df['vol_std_24h'].replace(0, np.nan)
        for window in sorted({4, PREDICTION_HORIZON_HOURS, 12, 24}):
            df[f'ret_volnorm_{window}h'] = df[f'ret_sum_{window}h'] / vol24

        # Autocorr of 1h returns at the forecast horizon (mean-reversion cue).
        df['ret_autocorr_8h'] = ret.rolling(168).corr(ret.shift(PREDICTION_HORIZON_HOURS))

        for window in self.lookback_windows:
            df[f'sma_ret_{window}h'] = ret.rolling(window).mean()
            df[f'ema_ret_{window}h'] = ret.ewm(span=window).mean()

        for window in [24, 48, 168]:
            df[f'price_sma_{window}h'] = df['Close'].rolling(window).mean()
            df[f'price_ratio_{window}h'] = df['Close'] / df[f'price_sma_{window}h']
            df[f'hl_ratio_{window}h'] = (
                df['High'].rolling(window).max() / df['Low'].rolling(window).min()
            )
            z_den = df['Close'].rolling(window).std().replace(0, np.nan)
            df[f'price_z_{window}h'] = (df['Close'] - df[f'price_sma_{window}h']) / z_den

        if TALIB_AVAILABLE:
            df = self._add_talib_features(df)
        else:
            df = self._add_manual_technical_features(df)

        df['bid_ask_spread'] = (df['High'] - df['Low']) / df['Close']
        df['volume_price_trend'] = df['Volume'] * ret
        for window in [24, 48]:
            df[f'volume_sma_{window}h'] = df['Volume'].rolling(window).mean()
            df[f'volume_ratio_{window}h'] = df['Volume'] / df[f'volume_sma_{window}h']

        df['ret_rank_24h'] = ret.rolling(24).rank(pct=True)
        df['vol_rank_24h'] = df['vol_std_24h'].rolling(168).rank(pct=True)
        df['high_vol_regime'] = (
            df['vol_std_24h'] > df['vol_std_24h'].rolling(168).quantile(0.75)
        ).astype(int)
        df['trend_strength'] = np.abs(df['sma_ret_24h'])

        df['hour'] = df.index.hour
        df['day_of_week'] = df.index.dayofweek
        df['is_weekend'] = (df.index.dayofweek >= 5).astype(int)
        df['is_us_cash'] = df.index.hour.isin(range(14, 21)).astype(int)
        df['is_asia'] = df.index.hour.isin(list(range(0, 8))).astype(int)
        df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
        df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
        df['dow_sin'] = np.sin(2 * np.pi * df['day_of_week'] / 7)
        df['dow_cos'] = np.cos(2 * np.pi * df['day_of_week'] / 7)
        return df.dropna()

    def _add_manual_technical_features(self, df):
        for window in [14, 24]:
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
            rs = gain / loss.replace(0, np.nan)
            df[f'rsi_{window}h'] = 100 - (100 / (1 + rs))
        sma = df['Close'].rolling(48).mean()
        std = df['Close'].rolling(48).std()
        df['bb_upper'] = sma + 2 * std
        df['bb_lower'] = sma - 2 * std
        df['bb_position'] = (df['Close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])
        ema12 = df['Close'].ewm(span=12).mean()
        ema26 = df['Close'].ewm(span=26).mean()
        df['macd'] = ema12 - ema26
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']
        return df

    def _add_talib_features(self, df):
        c, h, lo = df['Close'].values, df['High'].values, df['Low'].values
        df['rsi_14h'] = talib.RSI(c, timeperiod=14)
        df['rsi_24h'] = talib.RSI(c, timeperiod=24)
        macd, sig, hist = talib.MACD(c)
        df['macd'], df['macd_signal'], df['macd_histogram'] = macd, sig, hist
        sk, sd = talib.STOCH(h, lo, c)
        df['stoch_k'], df['stoch_d'] = sk, sd
        return df

    def prepare_model_data(self, df, current_price, test_size=0.2):
        df = df.copy()
        target_col = f'target_modified_log_return_{PREDICTION_HORIZON_HOURS}h'
        df[target_col] = np.log(df['Close'].shift(-PREDICTION_HORIZON_HOURS) / df['Close'])
        exclude = {
            'traditional_log_return', f'log_return_{HORIZON_KEY}',
            'Open', 'High', 'Low', 'Close', 'Volume', 'Adj Close',
            target_col,
        }
        feature_cols = sorted([
            c for c in df.columns
            if c not in exclude and df[c].dtype in ('int64', 'float64', 'int32', 'float32')
        ])
        self.feature_columns = feature_cols
        print(f"Selected {len(feature_cols)} features")
        df_clean = df[feature_cols + [target_col]].dropna()
        split_idx = int(len(df_clean) * (1 - test_size))
        train_end = max(split_idx - PREDICTION_HORIZON_HOURS, int(len(df_clean) * 0.5))
        X_train = df_clean[feature_cols].iloc[:train_end]
        X_test = df_clean[feature_cols].iloc[split_idx:]
        y_train = df_clean[target_col].iloc[:train_end]
        y_test = df_clean[target_col].iloc[split_idx:]
        X_train_s = pd.DataFrame(
            self.scaler.fit_transform(X_train), columns=feature_cols, index=X_train.index
        )
        X_test_s = pd.DataFrame(
            self.scaler.transform(X_test), columns=feature_cols, index=X_test.index
        )
        print(f"Training samples: {len(X_train_s)}")
        print(f"Testing samples:  {len(X_test_s)}")
        return X_train_s, X_test_s, y_train, y_test, feature_cols, df_clean


class DirectionalLightGBM:
    """Classifier decides sign; regressor supplies magnitude."""

    def __init__(self, classifier, regressor, feature_cols, threshold=0.5,
                 deadzone=0.0, fade_col=None, calibrator=None, median_abs_return=0.01):
        self.classifier = classifier
        self.regressor = regressor
        self.feature_cols = list(feature_cols)
        self.threshold = float(threshold)
        self.deadzone = float(deadzone)
        self.fade_col = fade_col
        self.calibrator = calibrator
        self.median_abs_return = median_abs_return
        self.feature_importances_ = getattr(classifier, 'feature_importances_', None)

    def _select_features(self, X):
        if hasattr(X, 'loc'):
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
        if self.deadzone <= 0 or self.fade_col is None or self.fade_col not in getattr(X_use, 'columns', []):
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


class LightGBMTrainer:
    def __init__(self):
        self.model = None
        self.feature_importance = {}

    @staticmethod
    def dir_acc_eval(y_true_or_preds, y_pred_or_dataset):
        if hasattr(y_pred_or_dataset, 'get_label'):
            y_pred = np.asarray(y_true_or_preds).ravel()
            y_true = np.asarray(y_pred_or_dataset.get_label()).ravel()
        else:
            y_true = np.asarray(y_true_or_preds).ravel()
            y_pred = np.asarray(y_pred_or_dataset).ravel()
        if y_pred.min() >= 0.0 and y_pred.max() <= 1.0:
            pred_up = y_pred >= 0.5
        else:
            pred_up = y_pred >= 0.0
        labels = np.unique(np.round(y_true, 8))
        true_up = (y_true >= 0.5) if set(labels).issubset({0, 1}) else (y_true > 0)
        return 'dir_acc', float(np.mean(pred_up == true_up)), True

    @staticmethod
    def _stationary_feature_cols(feature_cols):
        drop_prefixes = ('price_sma_', 'volume_sma_')
        drop_exact = {'bb_upper', 'bb_lower', 'macd', 'macd_signal',
                      'macd_histogram', 'hour', 'day_of_week'}
        selected = [
            c for c in feature_cols
            if c not in drop_exact and not c.startswith(drop_prefixes)
        ]
        return selected or list(feature_cols)

    @staticmethod
    def _recency_weights(y, half_life_hours=21 * 24):
        n = len(y)
        ages = np.arange(n - 1, -1, -1, dtype=float)
        recency = np.exp(-np.log(2.0) / max(half_life_hours, 1) * ages)
        abs_y = np.abs(np.asarray(y, dtype=float).ravel())
        typical = max(np.median(abs_y), 1e-8)
        # Tiny 8h moves are coin-flips; large moves are what DA (and the vault) care about.
        move_w = 0.25 + np.clip(abs_y / typical, 0.0, 4.0)
        w = recency * move_w
        return w / np.mean(w)

    @staticmethod
    def _best_iteration(model, default=200):
        for attr in ('best_iteration_', 'best_iteration'):
            val = getattr(model, attr, None)
            if val is not None and int(val) > 0:
                return int(val)
        booster = getattr(model, 'booster_', None)
        if booster is not None:
            val = getattr(booster, 'best_iteration', None)
            if val is not None and int(val) > 0:
                return int(val)
        return default

    @staticmethod
    def _da(y_true, pred_sign):
        y_true = np.asarray(y_true).ravel()
        pred_sign = np.asarray(pred_sign).ravel()
        mask = y_true != 0
        if mask.sum() == 0:
            return 0.0
        return float(np.mean(np.sign(y_true[mask]) == np.sign(pred_sign[mask])))

    def _calibrate_rule(self, proba, y_true, fade_signal):
        y_true = np.asarray(y_true).ravel()
        proba = np.asarray(proba).ravel()
        fade = np.sign(np.asarray(fade_signal).ravel()) if fade_signal is not None else np.zeros_like(proba)
        best = {'acc': -1.0, 'threshold': 0.5, 'deadzone': 0.0}
        for threshold in np.linspace(0.45, 0.55, 11):
            model_dir = np.where(proba >= threshold, 1.0, -1.0)
            edge = np.abs(proba - threshold)
            for deadzone in (0.0, 0.03, 0.05, 0.08, 0.12):
                blended = model_dir.copy()
                if deadzone > 0:
                    use_fade = (edge < deadzone) & (fade != 0)
                    blended = np.where(use_fade, fade, model_dir)
                acc = self._da(y_true, blended)
                if acc > best['acc']:
                    best = {'acc': acc, 'threshold': float(threshold), 'deadzone': float(deadzone)}
        # Prefer a plain 0.5 cutoff unless another rule clearly wins.
        plain = self._da(y_true, np.where(proba >= 0.5, 1.0, -1.0))
        if best['acc'] < plain + 0.005:
            return {'acc': plain, 'threshold': 0.5, 'deadzone': 0.0}
        return best

    def _recent_slice(self, X, y, max_hours=180 * 24):
        if len(X) <= max_hours:
            return X, y
        print(f"Using most recent {max_hours / 24:.0f} days for training")
        return X.iloc[-max_hours:], y.iloc[-max_hours:]

    def _purged_split(self, X, y, val_size=0.18):
        val_split = int(len(X) * (1 - val_size))
        embargo = PREDICTION_HORIZON_HOURS
        train_end = max(val_split - embargo, int(len(X) * 0.55))
        if train_end < 80 or (len(X) - val_split) < 40:
            n = min(80, len(X))
            return X, y, X.iloc[-n:], y.iloc[-n:]
        return X.iloc[:train_end], y.iloc[:train_end], X.iloc[val_split:], y.iloc[val_split:]

    def _fit_with_early_stopping(self, estimator, X_tr, y_tr, X_val, y_val,
                                 sample_weight, eval_metric):
        try:
            estimator.fit(
                X_tr, y_tr, sample_weight=sample_weight,
                eval_set=[(X_val, y_val)], eval_metric=eval_metric,
                callbacks=[lgb.early_stopping(100, first_metric_only=True, verbose=50)]
            )
        except TypeError:
            estimator.fit(
                X_tr, y_tr, sample_weight=sample_weight,
                eval_set=[(X_val, y_val)], eval_metric=eval_metric,
                early_stopping_rounds=100, verbose=50
            )
        return estimator

    def train(self, X_train, y_train, feature_cols=None, val_size=0.18):
        if feature_cols is None:
            feature_cols = list(X_train.columns)
        feature_cols = self._stationary_feature_cols(feature_cols)
        fade_col = f'ret_fade_{PREDICTION_HORIZON_HOURS}h'
        if fade_col not in feature_cols and fade_col in X_train.columns:
            feature_cols = list(feature_cols) + [fade_col]

        X_recent, y_recent = self._recent_slice(X_train[feature_cols], y_train)
        X_tr, y_tr, X_val, y_val = self._purged_split(X_recent, y_recent, val_size)

        clf_params = dict(
            learning_rate=0.03,
            max_depth=3,
            num_leaves=12,
            n_estimators=3000,
            subsample=0.8,
            colsample_bytree=0.45,
            reg_alpha=0.4,
            reg_lambda=4.0,
            min_child_samples=120,
            subsample_freq=1,
            min_split_gain=0.0,
            random_state=42,
            verbose=-1,
        )
        reg_params = dict(
            learning_rate=0.04,
            max_depth=4,
            num_leaves=20,
            n_estimators=2000,
            subsample=0.7,
            colsample_bytree=0.5,
            reg_alpha=0.8,
            reg_lambda=6.0,
            min_child_samples=80,
            subsample_freq=1,
            min_split_gain=0.01,
            random_state=42,
            verbose=-1,
        )

        sample_w = self._recency_weights(y_tr)
        y_tr_dir = (y_tr > 0).astype(int)
        y_val_dir = (y_val > 0).astype(int)
        val_fade = X_val[fade_col].values if fade_col in X_val.columns else None

        print(f"Training LightGBM  (features={len(feature_cols)}, "
              f"train={len(X_tr)}, val={len(X_val)})")

        clf = lgb.LGBMClassifier(
            objective='binary', metric='None', is_unbalance=True, **clf_params
        )
        clf = self._fit_with_early_stopping(
            clf, X_tr, y_tr_dir, X_val, y_val_dir, sample_w, self.dir_acc_eval
        )

        # Calibrate on the holdout BEFORE the full-window retrain (no leakage).
        raw_val_proba = clf.predict_proba(X_val)[:, 1]
        calibrator = IsotonicRegression(out_of_bounds='clip')
        calibrator.fit(raw_val_proba, y_val_dir)
        val_proba = np.clip(calibrator.predict(raw_val_proba), 1e-6, 1 - 1e-6)
        rule = self._calibrate_rule(val_proba, y_val.values, val_fade)
        print(f"Calibrated rule: threshold={rule['threshold']:.3f}, "
              f"deadzone={rule['deadzone']:.3f}, val DA={rule['acc']:.4f}")

        n_clf = max(self._best_iteration(clf, 250), 80)
        print(f"Retraining classifier on full recent window ({n_clf} trees)")
        final_clf = lgb.LGBMClassifier(
            objective='binary', metric='None', is_unbalance=True,
            **{**clf_params, 'n_estimators': n_clf}
        )
        recent_w = self._recency_weights(y_recent)
        final_clf.fit(X_recent, (y_recent > 0).astype(int), sample_weight=recent_w)

        reg = lgb.LGBMRegressor(objective='regression_l1', metric='mae', **reg_params)
        try:
            reg.fit(
                X_tr, y_tr, sample_weight=sample_w,
                eval_set=[(X_val, y_val)],
                callbacks=[lgb.early_stopping(80, verbose=50)]
            )
        except TypeError:
            reg.fit(
                X_tr, y_tr, sample_weight=sample_w,
                eval_set=[(X_val, y_val)], early_stopping_rounds=80, verbose=50
            )
        n_reg = max(self._best_iteration(reg, 200), 40)
        final_reg = lgb.LGBMRegressor(
            objective='regression_l1', metric='mae',
            **{**reg_params, 'n_estimators': n_reg}
        )
        final_reg.fit(X_recent, y_recent, sample_weight=recent_w)

        self.model = DirectionalLightGBM(
            classifier=final_clf,
            regressor=final_reg,
            feature_cols=feature_cols,
            threshold=rule['threshold'],
            deadzone=rule['deadzone'],
            fade_col=fade_col if fade_col in feature_cols else None,
            calibrator=calibrator,
            median_abs_return=float(np.median(np.abs(y_recent))),
        )
        val_preds = self.model.predict(X_val)
        val_da = self._da(y_val.values, val_preds)
        print(f"LightGBM validation directional accuracy: {val_da:.4f}")
        if self.model.feature_importances_ is not None:
            self.feature_importance = dict(zip(feature_cols, self.model.feature_importances_))
        print("LightGBM training completed")
        return self.model

    def predict(self, X):
        if self.model is None:
            raise RuntimeError("Model not trained yet — call train() first")
        return self.model.predict(X)


class ModelEvaluation:
    def __init__(self):
        self.results = {}
        self.zptae = ZPTAELoss()

    @staticmethod
    def directional_accuracy(y_true, y_pred, threshold=0.0):
        y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
        n = min(len(y_true), len(y_pred))
        y_true, y_pred = y_true[-n:], y_pred[-n:]
        mask = y_true != 0
        if threshold > 0:
            mask = mask & (np.abs(y_true) > threshold)
        if mask.sum() == 0:
            return 0.0
        return float(np.mean(np.sign(y_true[mask]) == np.sign(y_pred[mask])))

    def evaluate(self, trainer, X_test, y_test, current_price):
        print("\nEvaluating LightGBM model...")
        y_pred = trainer.predict(X_test)
        n = min(len(y_test), len(y_pred))
        y_true_a = y_test.iloc[-n:] if hasattr(y_test, 'iloc') else y_test[-n:]
        y_pred_a = y_pred[-n:]
        sigma = y_true_a.tail(min(100, len(y_true_a))).std()
        rmse = np.sqrt(mean_squared_error(y_true_a, y_pred_a))
        mae = mean_absolute_error(y_true_a, y_pred_a)
        zptae_val = float(np.mean(self.zptae.loss_zptae(y_true_a, y_pred_a, sigma)))
        da = self.directional_accuracy(y_true_a, y_pred_a)
        da_sig = self.directional_accuracy(y_true_a, y_pred_a, threshold=0.02)
        ic = np.corrcoef(y_true_a, y_pred_a)[0, 1] if n > 1 else 0.0
        pred_prices = current_price * np.exp(y_pred_a)
        actual_prices = current_price * np.exp(y_true_a)
        price_rmse = np.sqrt(np.mean((pred_prices - actual_prices)**2))
        price_mae = np.mean(np.abs(pred_prices - actual_prices))
        metrics = {
            'rmse': rmse, 'mae': mae, 'zptae': zptae_val,
            'directional_accuracy': da,
            'directional_accuracy_significant': da_sig,
            'information_coefficient': float(ic),
            'price_rmse': price_rmse, 'price_mae': price_mae,
            'price_rmse_pct': price_rmse / current_price * 100,
            'price_mae_pct': price_mae / current_price * 100,
            'n_predictions': n,
        }
        self.results = metrics
        print(f"  RMSE={rmse:.6f}  MAE={mae:.6f}  ZPTAE={zptae_val:.6f}")
        print(f"  Directional accuracy={da:.4f}  (|ret|>0.02: {da_sig:.4f})")
        print(f"  IC={ic:.4f}")
        return metrics

    def report_directional_accuracy(self, trainer, X_train, y_train,
                                    X_test, y_test, current_price):
        train_pred = trainer.predict(X_train)
        test_pred = trainer.predict(X_test)
        train_da = self.directional_accuracy(y_train.values, train_pred)
        test_da = self.directional_accuracy(y_test.values, test_pred)
        train_da_sig = self.directional_accuracy(y_train.values, train_pred, 0.02)
        test_da_sig = self.directional_accuracy(y_test.values, test_pred, 0.02)
        print(f"\n{'='*60}")
        print(f"DIRECTIONAL ACCURACY ({HORIZON_KEY})")
        print(f"{'='*60}")
        print(f"  Train: {train_da:.4f}   (|ret|>0.02: {train_da_sig:.4f})")
        print(f"  Test:  {test_da:.4f}   (|ret|>0.02: {test_da_sig:.4f})")
        print(f"{'='*60}")
        return {
            'train': train_da, 'test': test_da,
            'train_significant': train_da_sig, 'test_significant': test_da_sig,
        }

    def plot_predictions(self, trainer, X_test, y_test, current_price, n_points=200):
        y_pred = trainer.predict(X_test.iloc[-n_points:])
        y_actual = y_test.iloc[-n_points:]
        n = min(len(y_actual), len(y_pred))
        y_actual = y_actual.iloc[-n:]
        y_pred = y_pred[-n:]
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(range(n), y_actual.values, label='Actual', alpha=0.7, linewidth=1)
        ax.plot(range(n), y_pred, label='Predicted', alpha=0.7, linewidth=1)
        ax.set_title(f'LightGBM – {HORIZON_KEY} Log-Return Predictions')
        ax.set_xlabel('Time Steps')
        ax.set_ylabel('Log-Return')
        ax.legend()
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()


class ProductionPipeline:
    def __init__(self, tiingo_api_key=None):
        self.data_ingestion = DataIngestion(tiingo_api_key=tiingo_api_key)
        self.feature_engineering = FeatureEngineering()
        self.trainer = LightGBMTrainer()
        self.evaluator = ModelEvaluation()

    def full_pipeline(self, days_back=730, test_size=0.2, resample_freq='1hour'):
        print("=" * 80)
        print("ETH/USDT 8h LOG-RETURN PREDICTION PIPELINE  (LightGBM)")
        print("Training: Tiingo API  |  Current Price: DIA API")
        print(f"Target: ln(new_price / current_price) at {HORIZON_KEY}")
        print()
        print("1. Fetching current price from DIA...")
        current_price, current_ts = self.data_ingestion.fetch_current_price_from_dia()
        if current_price is None:
            raise ValueError("Failed to fetch current price from DIA API")
        print("\n2. Fetching training data from Tiingo...")
        training_data = self.data_ingestion.fetch_eth_training_data(days_back, resample_freq)
        if training_data is None:
            raise ValueError("Failed to fetch training data from Tiingo API")
        last_ts = training_data.index[-1]
        gap_hours = (current_ts - last_ts).total_seconds() / 3600
        print(f"Training data ends: {last_ts}  |  Gap: {gap_hours:.1f}h")
        if gap_hours > 24:
            print("⚠ Large gap between training data and current time")
        print("\n3. Feature engineering...")
        featured = self.feature_engineering.create_features(training_data)
        print("\n4. Preparing train/test split...")
        X_train, X_test, y_train, y_test, feat_cols, df_clean = \
            self.feature_engineering.prepare_model_data(featured, current_price, test_size)
        print("\n5. Training LightGBM...")
        self.trainer.train(X_train, y_train, feature_cols=feat_cols)
        print("\n6. Evaluating...")
        metrics = self.evaluator.evaluate(self.trainer, X_test, y_test, current_price)
        da_report = self.evaluator.report_directional_accuracy(
            self.trainer, X_train, y_train, X_test, y_test, current_price
        )
        if metrics['directional_accuracy'] < 0.50:
            print("⚠ Test directional accuracy is below 50%")
        print("\n7. Generating visualizations...")
        self.evaluator.plot_predictions(self.trainer, X_test, y_test, current_price)
        return {
            'metrics': metrics,
            'directional_accuracy': da_report,
            'current_price': current_price,
            'current_timestamp': current_ts,
            'training_data_end': last_ts,
            'gap_hours': gap_hours,
            'feature_count': len(feat_cols),
            'total_training_points': len(df_clean),
        }

    def generate_prediction(self, use_real_time=True):
        print(f"\n{'='*60}")
        print(f"GENERATING {HORIZON_KEY} ETH/USDT LOG-RETURN PREDICTION")
        print(f"{'='*60}")
        current_price, current_ts = self.data_ingestion.fetch_current_price_from_dia()
        if current_price is None:
            raise ValueError("Failed to fetch current price from DIA API")
        if use_real_time:
            recent_data = self.data_ingestion.fetch_eth_training_data(60, '1hour')
        else:
            if self.data_ingestion.data is None:
                raise ValueError("No data — run full_pipeline() first or set use_real_time=True")
            recent_data = self.data_ingestion.data
        if self.trainer.model is None:
            raise ValueError("No trained model — run full_pipeline() first")
        featured = self.feature_engineering.create_features(recent_data)
        model_cols = self.trainer.model.feature_cols
        latest = featured.iloc[[-1]]
        X_all = pd.DataFrame(index=latest.index)
        for c in self.feature_engineering.feature_columns:
            X_all[c] = latest[c] if c in latest.columns else 0.0
        X_all_scaled = pd.DataFrame(
            self.feature_engineering.scaler.transform(X_all),
            columns=self.feature_engineering.feature_columns,
            index=latest.index,
        )
        X_scaled = X_all_scaled[model_cols]
        log_return = self.trainer.predict(X_scaled)[0]
        pct_change = (np.exp(log_return) - 1) * 100
        recent_vol = recent_data['traditional_log_return'].tail(100).std()
        print(f"\nTimestamp:     {current_ts}")
        print(f"Horizon:      {HORIZON_KEY}")
        print(f"Log return:   {log_return:+.6f}")
        print(f"Change:       {pct_change:+.2f}%")
        print(f"Volatility (100h): {recent_vol:.4f}")
        return {
            'timestamp': current_ts,
            'horizon': HORIZON_KEY,
            'log_return': log_return,
            'percentage_change': pct_change,
            'recent_volatility': recent_vol,
        }

    def monitor_api_status(self):
        print("\nMonitoring API Status...")
        print("-" * 40)
        price, ts = self.data_ingestion.fetch_current_price_from_dia()
        if price:
            print(f"DIA API:    OK  – ETH ${price:.4f} at {ts}")
        else:
            print("DIA API:    Not responding")
        try:
            d = self.data_ingestion.fetch_eth_training_data(7, '1hour')
            if d is not None and len(d):
                print(f"Tiingo API: OK  – {len(d)} bars, {d.index.min()} → {d.index.max()}")
            else:
                print("Tiingo API: No data returned")
        except Exception as e:
            print(f"Tiingo API: Error – {e}")
        print("-" * 40)

    def save_pickle(self, path=PICKLE_PATH):
        payload = {
            'model': self.trainer.model,
            'scaler': self.feature_engineering.scaler,
            'feature_columns': self.feature_engineering.feature_columns,
            'model_feature_cols': list(self.trainer.model.feature_cols),
            'horizon_hours': PREDICTION_HORIZON_HOURS,
        }
        with open(path, 'wb') as f:
            pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
        print(f"Saved pickle → {path}")
        return path


def save_pipeline(pipeline, path=PICKLE_PATH):
    return pipeline.save_pickle(path)


def load_pickle(path=PICKLE_PATH):
    with open(path, 'rb') as f:
        return pickle.load(f)


def hourly_eth_8h_pct(path=PICKLE_PATH, tiingo_api_key=None, pipeline=None):
    """Load the pickle and print only the 8h ETH percent change. Call this once per hour."""
    bundle = load_pickle(path)
    model = bundle['model']
    scaler = bundle['scaler']
    feature_columns = bundle['feature_columns']
    model_cols = bundle.get('model_feature_cols', model.feature_cols)

    if pipeline is not None:
        ingest = pipeline.data_ingestion
        recent = pipeline.data_ingestion.data
        if recent is None or len(recent) < 200:
            recent = ingest.fetch_eth_training_data(days_back=60, resample_freq='1hour')
    else:
        key = tiingo_api_key or TIINGO_API_KEY
        ingest = DataIngestion(tiingo_api_key=key)
        recent = ingest.fetch_eth_training_data(days_back=60, resample_freq='1hour')

    if recent is None:
        raise ValueError('Tiingo fetch failed — pass pipeline=pipeline or a valid tiingo_api_key')

    featured = FeatureEngineering().create_features(recent)
    latest = featured.iloc[[-1]]
    X_all = pd.DataFrame(index=latest.index)
    for c in feature_columns:
        X_all[c] = latest[c] if c in latest.columns else 0.0
    X_scaled = pd.DataFrame(
        scaler.transform(X_all), columns=feature_columns, index=latest.index
    )
    log_return = float(model.predict(X_scaled[model_cols])[0])
    pct_change = (np.exp(log_return) - 1) * 100
    print(f"{pct_change:+.2f}")
    return pct_change


def main(tiingo_api_key=None):
    pipeline = ProductionPipeline(tiingo_api_key=tiingo_api_key)
    pipeline.monitor_api_status()
    print("\nStarting full pipeline...")
    results = pipeline.full_pipeline(days_back=365, test_size=0.15, resample_freq='1hour')
    pipeline.save_pickle(PICKLE_PATH)
    prediction = pipeline.generate_prediction(use_real_time=True)
    print(f"\n{'='*60}")
    print(f"Pipeline complete")
    print(f"  {HORIZON_KEY} log return: {prediction['log_return']:+.6f} "
          f"({prediction['percentage_change']:+.2f}%)")
    da = results['directional_accuracy']
    print(f"  DA  train={da['train']:.4f}  test={da['test']:.4f}")
    print(f"{'='*60}")
    return {'pipeline': pipeline, 'results': results, 'prediction': prediction}


if __name__ == "__main__":
    print("ETH/USDT 8h Log-Return Prediction System (LightGBM)")
    output = main()
