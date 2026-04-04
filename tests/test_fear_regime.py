#!/usr/bin/env python3
"""
Tests for strategies/fear_regime.py
"""

import unittest
from unittest.mock import patch, MagicMock


class TestFearRegime(unittest.TestCase):
    """Tests for fear regime detection."""

    def setUp(self):
        """Reset module-level caches before each test."""
        import strategies.fear_regime as fr
        fr._fg_cache = None
        fr._vix_cache = None

    def test_fetch_fear_greed_returns_dict(self):
        """fetch_fear_greed returns dict with value, classification, timestamp."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{
                "value": "45",
                "value_classification": "Fear",
                "timestamp": "1712000000",
            }]
        }

        with patch("strategies.fear_regime.requests.get", return_value=mock_response):
            result = __import__("strategies.fear_regime", fromlist=["fetch_fear_greed"]).fetch_fear_greed()

        self.assertIsNotNone(result)
        self.assertIn("value", result)
        self.assertIn("classification", result)
        self.assertIn("timestamp", result)
        self.assertEqual(result["value"], 45)
        self.assertEqual(result["classification"], "Fear")

    def test_fetch_fear_greed_caching(self):
        """Second call within 1h returns cached result (only one HTTP call)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{
                "value": "60",
                "value_classification": "Neutral",
                "timestamp": "1712000000",
            }]
        }

        with patch("strategies.fear_regime.requests.get", return_value=mock_response) as mock_get:
            fr = __import__("strategies.fear_regime", fromlist=["fetch_fear_greed"])
            r1 = fr.fetch_fear_greed()
            r2 = fr.fetch_fear_greed()

        self.assertEqual(r1, r2)
        self.assertEqual(mock_get.call_count, 1)

    def test_get_regime_extreme_fear(self):
        """fg=15 -> regime=extreme_fear, min_edge=0.10."""
        fr = __import__("strategies.fear_regime", fromlist=["get_regime"])

        with patch.object(fr, "fetch_fear_greed", return_value={"value": 15, "classification": "Extreme Fear", "timestamp": "x"}), \
             patch.object(fr, "fetch_vix", return_value=18.0):

            result = fr.get_regime()

        self.assertEqual(result["regime"], "extreme_fear")
        self.assertEqual(result["min_edge_override"], 0.10)
        self.assertEqual(result["fear_greed_value"], 15)
        self.assertEqual(result["vix"], 18.0)

    def test_get_regime_neutral(self):
        """fg=50, vix=18 -> regime=neutral, min_edge=0.15."""
        fr = __import__("strategies.fear_regime", fromlist=["get_regime"])

        with patch.object(fr, "fetch_fear_greed", return_value={"value": 50, "classification": "Neutral", "timestamp": "x"}), \
             patch.object(fr, "fetch_vix", return_value=18.0):

            result = fr.get_regime()

        self.assertEqual(result["regime"], "neutral")
        self.assertEqual(result["min_edge_override"], 0.15)
        self.assertEqual(result["fear_greed_value"], 50)
        self.assertEqual(result["vix"], 18.0)

    def test_get_regime_high_vol(self):
        """fg=50, vix=35 -> regime=high_vol, min_edge=0.12 (VIX dominates)."""
        fr = __import__("strategies.fear_regime", fromlist=["get_regime"])

        with patch.object(fr, "fetch_fear_greed", return_value={"value": 50, "classification": "Neutral", "timestamp": "x"}), \
             patch.object(fr, "fetch_vix", return_value=35.0):

            result = fr.get_regime()

        self.assertEqual(result["regime"], "high_vol")
        self.assertEqual(result["min_edge_override"], 0.12)
        self.assertEqual(result["vix"], 35.0)

    def test_get_regime_api_failure(self):
        """Both APIs fail -> returns safe defaults (neutral, min_edge=0.15)."""
        fr = __import__("strategies.fear_regime", fromlist=["get_regime"])

        with patch.object(fr, "fetch_fear_greed", return_value=None), \
             patch.object(fr, "fetch_vix", return_value=None):

            result = fr.get_regime()

        self.assertEqual(result["regime"], "neutral")
        self.assertEqual(result["min_edge_override"], 0.15)
        self.assertIsNone(result["fear_greed_value"])
        self.assertIsNone(result["vix"])


if __name__ == "__main__":
    unittest.main()
