# -*- coding: utf-8 -*-
"""
Unit tests for CycleDetector and EnhancedModelSelector
Phase D2: FFT-based cycle detection tests
"""

import unittest
import numpy as np
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.cycle_detector import CycleDetector, EnhancedModelSelector


class TestCycleDetector(unittest.TestCase):
    """Tests for CycleDetector class."""

    def setUp(self):
        self.detector = CycleDetector()

    def test_detect_periodicity_insufficient_data(self):
        """Test with insufficient data points."""
        result = self.detector.detect_periodicity([1, 2, 3])
        self.assertFalse(result['has_weekly'])
        self.assertFalse(result['has_monthly'])
        self.assertIsNone(result['dominant_period'])

    def test_detect_periodicity_weekly_cycle(self):
        """Test detection of weekly cycle."""
        # Generate data with 7-day period
        np.random.seed(42)
        t = np.linspace(0, 56, 56)  # 8 weeks
        data = 50 + 10 * np.sin(2 * np.pi * t / 7) + np.random.randn(56) * 0.5
        
        result = self.detector.detect_periodicity(data.tolist())
        
        # Should detect weekly cycle
        self.assertTrue(result['has_weekly'])
        self.assertIsNotNone(result['dominant_period'])
        self.assertIn(7, result['all_periods'])

    def test_detect_periodicity_monthly_cycle(self):
        """Test detection of monthly cycle."""
        # Generate data with ~30-day period
        np.random.seed(42)
        t = np.linspace(0, 90, 90)  # ~3 months
        data = 50 + 15 * np.sin(2 * np.pi * t / 30) + np.random.randn(90) * 0.5
        
        result = self.detector.detect_periodicity(data.tolist())
        
        # Should detect monthly cycle
        self.assertTrue(result['has_monthly'])

    def test_has_weekly_cycle(self):
        """Test has_weekly_cycle helper method."""
        t = np.linspace(0, 56, 56)
        data = 50 + 10 * np.sin(2 * np.pi * t / 7)
        
        self.assertTrue(self.detector.has_weekly_cycle(data.tolist()))

    def test_has_monthly_cycle(self):
        """Test has_monthly_cycle helper method."""
        t = np.linspace(0, 90, 90)
        data = 50 + 10 * np.sin(2 * np.pi * t / 30)
        
        self.assertTrue(self.detector.has_monthly_cycle(data.tolist()))

    def test_no_cycle_detected(self):
        """Test with random data (no clear cycle)."""
        np.random.seed(42)
        data = np.random.randn(50).tolist()
        
        result = self.detector.detect_periodicity(data)
        
        # Random data may or may not have cycles, but should not crash
        self.assertIn('has_weekly', result)
        self.assertIn('has_monthly', result)
        self.assertIn('dominant_period', result)


class TestEnhancedModelSelector(unittest.TestCase):
    """Tests for EnhancedModelSelector class."""

    def setUp(self):
        self.selector = EnhancedModelSelector()

    def test_select_model_sma_insufficient_data(self):
        """Test SMA selected when data too small for linear."""
        data = [1, 2, 3, 4, 5]
        model = self.selector.select_model(data, len(data))
        self.assertEqual(model, 'sma')

    def test_select_model_linear(self):
        """Test linear model selected for normal data."""
        np.random.seed(42)
        data = (50 + np.random.randn(20) * 5).tolist()
        
        model = self.selector.select_model(data, 20)
        self.assertEqual(model, 'linear')

    def test_select_model_holt_winters_with_cycle(self):
        """Test Holt-Winters selected when cycle detected."""
        np.random.seed(42)
        t = np.linspace(0, 60, 60)
        data = (50 + 10 * np.sin(2 * np.pi * t / 7) + np.random.randn(60) * 0.5).tolist()
        
        model = self.selector.select_model(data, 60)
        self.assertEqual(model, 'holt_winters')

    def test_evaluate_model(self):
        """Test model evaluation with MAPE."""
        y_true = [100, 110, 105, 115]
        y_pred = [102, 108, 103, 112]
        
        self.selector.evaluate_model('linear', y_true, y_pred)
        
        self.assertIn('linear', self.selector.model_stats)
        self.assertGreater(len(self.selector.model_stats['linear']), 0)

    def test_get_best_model(self):
        """Test getting best model based on MAPE."""
        # Add some evaluations
        self.selector.evaluate_model('linear', [100, 110], [105, 115])  # High MAPE
        self.selector.evaluate_model('holt_winters', [100, 110], [101, 109])  # Low MAPE
        
        best = self.selector.get_best_model(recent_window=1)
        self.assertEqual(best, 'holt_winters')


class TestCycleDetectorIntegration(unittest.TestCase):
    """Integration tests for cycle detection with model selection."""

    def test_full_pipeline(self):
        """Test complete pipeline: detect cycle -> select model."""
        # Generate weekly cycling data
        np.random.seed(42)
        t = np.linspace(0, 90, 90)
        data = (50 + 10 * np.sin(2 * np.pi * t / 7) + np.random.randn(90) * 0.5).tolist()
        
        detector = CycleDetector()
        selector = EnhancedModelSelector()
        
        # Detect cycles
        cycle_info = detector.detect_periodicity(data)
        
        # Select model
        model = selector.select_model(data, len(data))
        
        # Verify
        self.assertTrue(cycle_info['has_weekly'])
        self.assertEqual(model, 'holt_winters')


if __name__ == '__main__':
    unittest.main()
