import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from typing import Dict, Tuple, List
import pandas as pd

class OptimizedPredictionPipeline:
    def __init__(self,
                 classification_model: nn.Module,
                 device: torch.device):
        """
        Initialize prediction pipeline with optimized rules based on evaluation results
        """
        self.classification_model = classification_model
        self.device = device
        self.classification_model.eval()

        # Define condition-specific weights based on evaluation results
        self.condition_weights = {
            'Spinal Canal Stenosis': 1.0,  # Best performing condition
            'Left Neural Foraminal Narrowing': 0.95,
            'Right Neural Foraminal Narrowing': 0.95,
            'Left Subarticular Stenosis': 0.93,
            'Right Subarticular Stenosis': 0.93
        }

        # Define level-specific weights based on evaluation results
        self.level_weights = {
            'L1_L2': 1.0,  # Best performing level
            'L2_L3': 0.98,
            'L3_L4': 0.95,
            'L4_L5': 0.90,  # Most challenging level
            'L5_S1': 0.92
        }

        # Define log loss thresholds for confidence adjustment
        self.log_loss_thresholds = {
            'Spinal Canal Stenosis': {
                'L1_L2': 0.1067,  # Using actual log loss values from evaluation
                'L2_L3': 0.4514,
                'L3_L4': 0.5757,
                'L4_L5': 0.8928,
                'L5_S1': 0.2164
            },
            'Neural Foraminal Narrowing': {
                'L1_L2': 0.2200,  # Average of left and right
                'L2_L3': 0.4635,
                'L3_L4': 0.9959,
                'L4_L5': 1.1089,
                'L5_S1': 1.0936
            },
            'Subarticular Stenosis': {
                'L1_L2': 0.2285,
                'L2_L3': 0.5337,
                'L3_L4': 0.9766,
                'L4_L5': 1.1551,
                'L5_S1': 0.7856
            }
        }

    def preprocess_input(self, image: torch.Tensor, condition: str, level: str) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Preprocess inputs for model prediction
        """
        # Ensure image is on correct device
        if isinstance(image, np.ndarray):
            image = torch.from_numpy(image).float()
        image = image.to(self.device)

        # Create condition one-hot encoding
        condition_map = {
            'Spinal Canal Stenosis': 0,
            'Left Neural Foraminal Narrowing': 1,
            'Right Neural Foraminal Narrowing': 2,
            'Left Subarticular Stenosis': 3,
            'Right Subarticular Stenosis': 4
        }
        condition_tensor = torch.zeros(5, device=self.device)
        condition_tensor[condition_map[condition]] = 1

        # Create level one-hot encoding
        level_map = {
            'L1_L2': 0, 'L2_L3': 1, 'L3_L4': 2, 'L4_L5': 3, 'L5_S1': 4
        }
        level_tensor = torch.zeros(5, device=self.device)
        level_tensor[level_map[level]] = 1

        return image, condition_tensor, level_tensor

    def apply_condition_level_adjustments(self,
                                        predictions: torch.Tensor,
                                        condition: str,
                                        level: str) -> torch.Tensor:
        """
        Apply condition and level-specific adjustments to predictions
        """
        # Get base weights
        condition_weight = self.condition_weights.get(condition, 0.95)
        level_weight = self.level_weights.get(level, 0.90)

        # Get log loss threshold
        if 'Neural Foraminal Narrowing' in condition:
            condition_key = 'Neural Foraminal Narrowing'
        elif 'Subarticular Stenosis' in condition:
            condition_key = 'Subarticular Stenosis'
        else:
            condition_key = condition

        log_loss_threshold = self.log_loss_thresholds[condition_key][level]

        # Apply weights
        adjusted_predictions = predictions * (condition_weight * level_weight)

        # Adjust based on log loss threshold
        if log_loss_threshold > 0.8:  # High uncertainty
            # More conservative predictions for high uncertainty cases
            adjusted_predictions[:, 2] *= 0.9  # Reduce severe predictions
            adjusted_predictions[:, 1] *= 0.95  # Slightly reduce moderate predictions
            adjusted_predictions[:, 0] += 0.1  # Bias toward normal/mild

        # Normalize predictions
        adjusted_predictions = F.normalize(adjusted_predictions, p=1, dim=1)

        return adjusted_predictions

    def get_prediction_confidence(self, predictions: torch.Tensor) -> torch.Tensor:
        """
        Calculate prediction confidence
        """
        # Get max probability and entropy
        max_prob = predictions.max(dim=1)[0]
        entropy = -(predictions * torch.log(predictions + 1e-7)).sum(dim=1)

        # Combine max probability and entropy for confidence score
        confidence = max_prob * (1 - entropy/np.log(3))  # Normalize entropy by max possible value

        return confidence

    def predict(self,
                image: torch.Tensor,
                condition: str,
                level: str) -> Dict[str, torch.Tensor]:
        """
        Generate predictions with confidence scores
        """
        with torch.no_grad():
            # Preprocess inputs
            image, condition_tensor, level_tensor = self.preprocess_input(image, condition, level)

            # Get base predictions
            base_predictions = self.classification_model(image, condition_tensor, level_tensor)
            base_probabilities = F.softmax(base_predictions, dim=1)

            # Apply adjustments
            adjusted_predictions = self.apply_condition_level_adjustments(
                base_probabilities,
                condition,
                level
            )

            # Calculate confidence
            confidence = self.get_prediction_confidence(adjusted_predictions)

            return {
                'probabilities': adjusted_predictions,
                'confidence': confidence,
                'severity_prediction': torch.argmax(adjusted_predictions, dim=1),
                'original_probabilities': base_probabilities
            }

    def batch_predict(self,
                     dataloader: torch.utils.data.DataLoader) -> List[Dict[str, torch.Tensor]]:
        """
        Generate predictions for a batch of data
        """
        predictions = []

        with torch.no_grad():
            for batch in dataloader:
                images = batch['image'].to(self.device)
                conditions = batch['condition'].to(self.device)
                levels = batch['level'].to(self.device)

                # Get predictions for batch
                outputs = self.classification_model(images, conditions, levels)
                probs = F.softmax(outputs, dim=1)

                # Process each sample in batch
                for i in range(len(images)):
                    condition_idx = torch.argmax(conditions[i]).item()
                    level_idx = torch.argmax(levels[i]).item()

                    condition = list(self.condition_weights.keys())[condition_idx]
                    level = list(self.level_weights.keys())[level_idx]

                    # Apply adjustments
                    adjusted_probs = self.apply_condition_level_adjustments(
                        probs[i].unsqueeze(0),
                        condition,
                        level
                    )

                    confidence = self.get_prediction_confidence(adjusted_probs)

                    predictions.append({
                        'probabilities': adjusted_probs,
                        'confidence': confidence,
                        'severity_prediction': torch.argmax(adjusted_probs, dim=1),
                        'original_probabilities': probs[i].unsqueeze(0)
                    })

        return predictions

def main():
    """Example usage of the prediction pipeline"""
    # Set device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # Load the classification model
    model = LumbarClassifier().to(device)
    model.load_state_dict(torch.load('best_model.pth')['model_state_dict'])

    # Create prediction pipeline
    pipeline = OptimizedPredictionPipeline(model, device)

    # Load validation data
    val_samples = load_processed_data('/kaggle/input/spine-processed-data/val_processed.npy')
    val_dataset = LumbarSpineDataset(val_samples, augment=False)
    val_loader = DataLoader(val_dataset, batch_size=32, shuffle=False)

    # Generate predictions
    print("Generating predictions...")
    predictions = pipeline.batch_predict(val_loader)

    # Analyze results
    confidences = torch.cat([p['confidence'] for p in predictions])
    print(f"\nAverage prediction confidence: {confidences.mean():.4f}")
    print(f"Minimum confidence: {confidences.min():.4f}")
    print(f"Maximum confidence: {confidences.max():.4f}")

if __name__ == "__main__":
    main()
