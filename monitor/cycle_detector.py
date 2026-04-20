# -*- coding: utf-8 -*-
"""
FFT-based Cycle Detection Module
Phase D2: Periodicity detection using Fast Fourier Transform

This module provides cycle detection functionality for capacity prediction.
"""

import numpy as np
from typing import Dict, List, Optional, Any
from collections import defaultdict


class CycleDetector:
    """
    FFT-based periodicity detector.
    
    Detects weekly (7-day) and monthly (30-day) cycles in time series data.
    """

    def __init__(self):
        self.detected_periods: List[int] = []

    def detect_periodicity(self, data: List[float], min_period: int = 2, max_period: int = 365) -> Dict[str, Any]:
        """
        Detect periodicity using FFT.
        
        Args:
            data: List of numeric values (time series)
            min_period: Minimum period to detect
            max_period: Maximum period to detect
            
        Returns:
            Dictionary with:
                - has_weekly: bool
                - has_monthly: bool  
                - dominant_period: int or None
                - all_periods: List[int]
        """
        n = len(data)
        if n < min_period * 2:
            return {'has_weekly': False, 'has_monthly': False, 'dominant_period': None, 'all_periods': []}

        y = np.array(data)
        y = y - np.mean(y)

        # Compute FFT
        fft = np.fft.fft(y)
        freqs = np.fft.fftfreq(n)
        amplitudes = np.abs(fft)

        # Consider only positive frequencies
        positive_freqs_idx = np.where(freqs > 0)[0]
        if len(positive_freqs_idx) == 0:
            return {'has_weekly': False, 'has_monthly': False, 'dominant_period': None, 'all_periods': []}

        # Calculate periods
        periods = n / freqs[positive_freqs_idx]
        period_amplitudes = amplitudes[positive_freqs_idx]

        # Filter to valid period range
        valid_mask = (periods >= min_period) & (periods <= max_period)
        if not np.any(valid_mask):
            return {'has_weekly': False, 'has_monthly': False, 'dominant_period': None, 'all_periods': []}

        valid_periods = periods[valid_mask]
        valid_amplitudes = period_amplitudes[valid_mask]

        # Find dominant period
        dominant_idx = np.argmax(valid_amplitudes)
        dominant_period = int(round(valid_periods[dominant_idx]))

        # Check for weekly cycle (period around 7 days)
        mean_amplitude = np.mean(valid_amplitudes)
        strong_periods = valid_periods[valid_amplitudes > mean_amplitude]
        
        has_weekly = any(abs(p - 7) <= 1 for p in strong_periods)
        has_monthly = any(abs(p - 30) <= 3 for p in strong_periods)

        self.detected_periods = [int(p) for p in valid_periods]

        return {
            'has_weekly': has_weekly,
            'has_monthly': has_monthly,
            'dominant_period': dominant_period,
            'all_periods': self.detected_periods
        }

    def has_weekly_cycle(self, data: List[float]) -> bool:
        """Check if data has a weekly cycle."""
        result = self.detect_periodicity(data)
        return result['has_weekly']

    def has_monthly_cycle(self, data: List[float]) -> bool:
        """Check if data has a monthly cycle."""
        result = self.detect_periodicity(data)
        return result['has_monthly']


class EnhancedModelSelector:
    """
    Enhanced model selector with cycle awareness.
    
    Automatically selects the best prediction model based on:
    - Amount of available data
    - Detected periodicity patterns
    """

    def __init__(self):
        self.cycle_detector = CycleDetector()
        self.model_stats: Dict[str, List[float]] = defaultdict(list)

    # Model selection thresholds
    MIN_DATA_POINTS_LINEAR = 7
    MIN_DATA_POINTS_HOLT_WINTERS = 14

    def select_model(self, data: List[float], n_data_points: int) -> str:
        """
        Automatically select best model based on data characteristics.
        
        Args:
            data: Time series data
            n_data_points: Number of data points available
            
        Returns:
            Model name: 'sma', 'linear', or 'holt_winters'
        """
        if n_data_points < self.MIN_DATA_POINTS_LINEAR:
            return 'sma'

        cycle_info = self.cycle_detector.detect_periodicity(data)

        if n_data_points >= self.MIN_DATA_POINTS_HOLT_WINTERS:
            if cycle_info['has_weekly'] or cycle_info['has_monthly']:
                return 'holt_winters'

        return 'linear'

    def evaluate_model(self, model_name: str, y_true: List[float], y_pred: List[float]) -> None:
        """
        Evaluate model performance using MAPE.
        
        Args:
            model_name: Name of the model
            y_true: Actual values
            y_pred: Predicted values
        """
        if len(y_true) != len(y_pred) or len(y_true) == 0:
            return

        # Mean Absolute Percentage Error
        mape = np.mean(np.abs((np.array(y_true) - np.array(y_pred)) / (np.array(y_true) + 1e-10))) * 100
        self.model_stats[model_name].append(mape)

    def get_best_model(self, recent_window: int = 10) -> str:
        """
        Get best performing model in recent window.
        
        Args:
            recent_window: Number of recent evaluations to consider
            
        Returns:
            Name of best performing model
        """
        best_model = 'linear'
        best_mape = float('inf')

        for model_name, mapes in self.model_stats.items():
            if not mapes:
                continue
            recent_mapes = mapes[-recent_window:]
            avg_mape = sum(recent_mapes) / len(recent_mapes)
            if avg_mape < best_mape:
                best_mape = avg_mape
                best_model = model_name

        return best_model


# Example usage and test
if __name__ == '__main__':
    # Generate sample data with weekly cycle
    np.random.seed(42)
    t = np.linspace(0, 60, 60)
    data = 50 + 10 * np.sin(2 * np.pi * t / 7) + np.random.randn(60) * 2
    
    detector = CycleDetector()
    result = detector.detect_periodicity(data.tolist())
    print(f"Cycle detection result: {result}")
    
    selector = EnhancedModelSelector()
    model = selector.select_model(data.tolist(), len(data))
    print(f"Selected model: {model}")
