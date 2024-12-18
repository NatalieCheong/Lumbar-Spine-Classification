import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
import torch.optim.lr_scheduler
import timm
import numpy as np
from typing import Dict, List, Tuple

class LumbarSpineRegDataset(Dataset):
    """Dataset class for Lumbar Spine Regression"""
    def __init__(self, samples: List[Dict], augment: bool = False):
        # Filter out samples with nan severity
        self.samples = [
            sample for sample in samples
            if isinstance(sample['severity'], str) and not pd.isna(sample['severity'])
        ]
        self.augment = augment

        # Map conditions to indices
        self.condition_map = {
            'Spinal Canal Stenosis': 0,
            'Left Neural Foraminal Narrowing': 1,
            'Right Neural Foraminal Narrowing': 2,
            'Left Subarticular Stenosis': 3,
            'Right Subarticular Stenosis': 4
        }

        # Map levels to indices
        self.level_map = {
            'L1_L2': 0, 'L2_L3': 1, 'L3_L4': 2, 'L4_L5': 3, 'L5_S1': 4
        }

        # Map severity to continuous values
        self.severity_map = {
            'Normal/Mild': 0.0,
            'Moderate': 1.0,
            'Severe': 2.0
        }

        print(f"After filtering nan values: {len(self.samples)} valid samples")

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        sample = self.samples[idx]

        # Convert image to torch tensor
        image = torch.from_numpy(sample['image']).float()
        image = image.permute(2, 0, 1)  # CHW format

        # Create condition one-hot encoding
        condition = torch.zeros(len(self.condition_map))
        condition[self.condition_map[sample['condition']]] = 1

        # Create level one-hot encoding
        level = torch.zeros(len(self.level_map))
        level[self.level_map[sample['level']]] = 1

        # Create severity value
        severity = torch.tensor([self.severity_map[sample['severity']]], dtype=torch.float)

        # Create importance weight based on level and severity
        weight = 1.0
        if sample['level'] in ['L4_L5', 'L5_S1']:
            weight *= 1.5
        if sample['severity'] in ['Moderate', 'Severe']:
            weight *= 2.0

        return {
            'image': image,
            'condition': condition,
            'level': level,
            'severity': severity,
            'weight': torch.tensor([weight], dtype=torch.float),
            'study_id': sample['study_id']
        }

class LumbarRegressor(nn.Module):
    def __init__(self):
        super().__init__()

        # Load pretrained EfficientNetV2 backbone
        self.backbone = timm.create_model(
            'tf_efficientnetv2_s',
            pretrained=True,
            in_chans=1,
            features_only=True
        )

        # Get backbone feature dimensions
        dummy_input = torch.randn(1, 1, 224, 224)
        features = self.backbone(dummy_input)
        feature_dims = [f.shape[1] for f in features]

        # Attention blocks for each feature level
        self.attention_blocks = nn.ModuleList([
            AttentionBlock(dims) for dims in feature_dims
        ])

        # Global Average Pooling
        self.gap = nn.AdaptiveAvgPool2d(1)

        # Calculate total feature dimensions
        total_dims = sum(feature_dims)

        # BiLSTM for sequential feature processing
        self.bilstm = nn.LSTM(
            input_size=total_dims,
            hidden_size=512,
            num_layers=2,
            bidirectional=True,
            batch_first=True
        )

        # Condition and level embedding
        self.condition_embed = nn.Linear(5, 64)  # 5 conditions
        self.level_embed = nn.Linear(5, 64)      # 5 levels

        # Regression head
        self.regressor = nn.Sequential(
            nn.Linear(2*512 + 64 + 64, 512),
            nn.ReLU(),
            nn.Dropout(0.3),
            nn.Linear(512, 256),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(256, 1),
            nn.Sigmoid()  # Output between 0 and 1
        )

        self._initialize_weights()

    def _initialize_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)
            elif isinstance(m, nn.Linear):
                nn.init.kaiming_normal_(m.weight)
                nn.init.constant_(m.bias, 0)

    def forward(self, x, condition, level):
        # Get backbone features
        features = self.backbone(x)

        # Apply attention to each feature level
        attended_features = [
            att(feat) for feat, att in zip(features, self.attention_blocks)
        ]

        # Global average pooling on each feature map
        pooled_features = [self.gap(feat) for feat in attended_features]

        # Concatenate features
        concat_features = torch.cat([
            feat.view(feat.size(0), -1) for feat in pooled_features
        ], dim=1)

        # Reshape for LSTM
        lstm_out, _ = self.bilstm(
            concat_features.unsqueeze(1)
        )
        lstm_out = lstm_out[:, -1, :]  # Take last output

        # Embed condition and level
        condition_embedding = self.condition_embed(condition)
        level_embedding = self.level_embed(level)

        # Concatenate all features
        combined_features = torch.cat([
            lstm_out,
            condition_embedding,
            level_embedding
        ], dim=1)

        # Regression output
        return self.regressor(combined_features) * 2.0  # Scale to 0-2 range

class WeightedL1Loss(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, pred, target, weight):
        return (weight * torch.abs(pred - target)).mean()

def train_epoch(model, train_loader, criterion, optimizer, device):
    model.train()
    total_loss = 0

    for batch in train_loader:
        images = batch['image'].to(device)
        conditions = batch['condition'].to(device)
        levels = batch['level'].to(device)
        targets = batch['severity'].to(device)
        weights = batch['weight'].to(device)

        optimizer.zero_grad()
        outputs = model(images, conditions, levels)
        loss = criterion(outputs, targets, weights)

        loss.backward()
        optimizer.step()

        total_loss += loss.item()

    return total_loss / len(train_loader)

def validate(model, val_loader, criterion, device):
    model.eval()
    total_loss = 0
    all_preds = []
    all_targets = []

    with torch.no_grad():
        for batch in val_loader:
            images = batch['image'].to(device)
            conditions = batch['condition'].to(device)
            levels = batch['level'].to(device)
            targets = batch['severity'].to(device)
            weights = batch['weight'].to(device)

            outputs = model(images, conditions, levels)
            loss = criterion(outputs, targets, weights)

            total_loss += loss.item()
            all_preds.extend(outputs.cpu().numpy())
            all_targets.extend(targets.cpu().numpy())

    mae = np.mean(np.abs(np.array(all_preds) - np.array(all_targets)))
    return total_loss / len(val_loader), mae

def main():
    # Set device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")

    # Load data
    print("Loading processed data...")
    train_samples = load_processed_data('/kaggle/input/spine-processed-data/train_processed.npy')
    val_samples = load_processed_data('/kaggle/input/spine-processed-data/val_processed.npy')

    # Create datasets and dataloaders
    train_dataset = LumbarSpineRegDataset(train_samples, augment=True)
    val_dataset = LumbarSpineRegDataset(val_samples, augment=False)

    train_loader = DataLoader(
        train_dataset,
        batch_size=32,
        shuffle=True,
        num_workers=4
    )

    val_loader = DataLoader(
        val_dataset,
        batch_size=32,
        shuffle=False,
        num_workers=4
    )

    # Create model
    model = LumbarRegressor().to(device)
    criterion = WeightedL1Loss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4, weight_decay=0.01)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=30, eta_min=1e-6)

    # Training parameters
    num_epochs = 30
    best_val_loss = float('inf')
    patience = 5
    patience_counter = 0

    print("Starting training...")
    for epoch in range(num_epochs):
        print(f"\nEpoch {epoch+1}/{num_epochs}")
        print("-" * 20)

        train_loss = train_epoch(model, train_loader, criterion, optimizer, device)
        val_loss, val_mae = validate(model, val_loader, criterion, device)
        scheduler.step()

        print(f"Train Loss: {train_loss:.4f}")
        print(f"Val Loss: {val_loss:.4f}, Val MAE: {val_mae:.4f}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            print("Saving best model...")
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_loss': val_loss,
                'val_mae': val_mae
            }, 'best_regression_model.pth')
            patience_counter = 0
        else:
            patience_counter += 1

        if patience_counter >= patience:
            print(f"\nEarly stopping triggered after {epoch+1} epochs")
            break

    print("\nTraining completed!")
    print(f"Best Validation Loss: {best_val_loss:.4f}")

if __name__ == "__main__":
    main()
