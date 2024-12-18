import unittest
import torch
import numpy as np
from src.prediction.prediction_pipeline import OptimizedPredictionPipeline
from src.models.classification_model import LumbarClassifier

class TestPrediction(unittest.TestCase):
    def setUp(self):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

        # Create model and pipeline
        self.model = LumbarClassifier().to(self.device)
        self.pipeline = OptimizedPredictionPipeline(self.model, self.device)

        # Create sample data
        self.sample_image = np.random.rand(224, 224, 1)
        self.sample_condition = 'Spinal Canal Stenosis'
        self.sample_level = 'L4_L5'

    def test_prediction_output(self):
        """Test prediction pipeline output"""
        prediction = self.pipeline.predict(
            self.sample_image,
            self.sample_condition,
            self.sample_level
        )

        # Check prediction keys
        required_keys = ['probabilities', 'severity_prediction', 'confidence']
        for key in required_keys:
            self.assertIn(key, prediction)

        # Check probability sum
        probs = prediction['probabilities'].cpu().numpy()
        self.assertAlmostEqual(np.sum(probs), 1.0, places=6)

        # Check confidence
        self.assertTrue(0 <= prediction['confidence'] <= 1)

        # Check prediction class
        self.assertTrue(0 <= prediction['severity_prediction'] <= 2)

    def test_batch_prediction(self):
        """Test batch prediction"""
        batch_size = 4
        sample_batch = [self.sample_image] * batch_size

        predictions = self.pipeline.batch_predict(
            sample_batch,
            [self.sample_condition] * batch_size,
            [self.sample_level] * batch_size
        )

        # Check number of predictions
        self.assertEqual(len(predictions), batch_size)

        # Check consistency
        for pred in predictions:
            self.assertIn('probabilities', pred)
            self.assertIn('severity_prediction', pred)
            self.assertIn('confidence', pred)

def run_tests():
    unittest.main()

if __name__ == '__main__':
    run_tests()
