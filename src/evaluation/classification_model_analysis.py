import torch
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix, classification_report
from typing import Dict, List, Tuple

def analyze_classification_model(model_path: str, val_loader: DataLoader, device: torch.device):
    """Analyze classification model performance in detail"""
    # Load the best model
    model = LumbarClassifier().to(device)
    checkpoint = torch.load(model_path)
    model.load_state_dict(checkpoint['model_state_dict'])
    model.eval()

    # Initialize lists to store predictions and true labels
    all_preds = []
    all_labels = []
    all_conditions = []
    all_levels = []
    study_ids = []

    # Get predictions
    with torch.no_grad():
        for batch in val_loader:
            images = batch['image'].to(device)
            conditions = batch['condition'].to(device)
            levels = batch['level'].to(device)
            labels = batch['severity']

            outputs = model(images, conditions, levels)
            _, preds = outputs.max(1)

            all_preds.extend(preds.cpu().numpy())
            all_labels.extend(labels.numpy())

            # Get condition and level names
            condition_indices = torch.argmax(conditions, dim=1).cpu().numpy()
            level_indices = torch.argmax(levels, dim=1).cpu().numpy()

            for c_idx, l_idx in zip(condition_indices, level_indices):
                all_conditions.append(list(val_loader.dataset.condition_map.keys())[c_idx])
                all_levels.append(list(val_loader.dataset.level_map.keys())[l_idx])

            study_ids.extend(batch['study_id'])

    # Create confusion matrix
    severity_names = ['Normal/Mild', 'Moderate', 'Severe']
    cm = confusion_matrix(all_labels, all_preds)

    plt.figure(figsize=(10, 8))
    sns.heatmap(cm, annot=True, fmt='d', xticklabels=severity_names, yticklabels=severity_names)
    plt.title('Confusion Matrix')
    plt.xlabel('Predicted')
    plt.ylabel('True')
    plt.savefig('confusion_matrix.png')
    plt.close()

    # Generate classification report
    report = classification_report(all_labels, all_preds, target_names=severity_names)
    print("\nClassification Report:")
    print(report)

    # Analyze performance by condition and level
    results_df = pd.DataFrame({
        'Study_ID': study_ids,
        'True_Label': [severity_names[l] for l in all_labels],
        'Predicted': [severity_names[p] for p in all_preds],
        'Condition': all_conditions,
        'Level': all_levels,
        'Correct': [1 if p == l else 0 for p, l in zip(all_preds, all_labels)]
    })

    # Performance by condition
    print("\nAccuracy by Condition:")
    condition_acc = results_df.groupby('Condition')['Correct'].mean() * 100
    print(condition_acc)

    # Performance by level
    print("\nAccuracy by Level:")
    level_acc = results_df.groupby('Level')['Correct'].mean() * 100
    print(level_acc)

    # Save detailed results
    results_df.to_csv('classification_results.csv', index=False)

    # Plot accuracy by condition and level
    plt.figure(figsize=(12, 5))
    plt.subplot(1, 2, 1)
    condition_acc.plot(kind='bar')
    plt.title('Accuracy by Condition')
    plt.xticks(rotation=45)
    plt.tight_layout()

    plt.subplot(1, 2, 2)
    level_acc.plot(kind='bar')
    plt.title('Accuracy by Level')
    plt.xticks(rotation=45)
    plt.tight_layout()

    plt.savefig('accuracy_analysis.png')
    plt.close()

    return results_df

def main():
    # Load the same data and model setup as before
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    # Load validation data
    val_samples = load_processed_data('/kaggle/input/spine-processed-data/val_processed.npy')
    val_dataset = LumbarSpineDataset(val_samples, augment=False)
    val_loader = DataLoader(
        val_dataset,
        batch_size=32,
        shuffle=False,
        num_workers=4
    )

    # Analyze model
    results_df = analyze_classification_model('best_model.pth', val_loader, device)
    print("\nAnalysis completed and saved to files.")

if __name__ == "__main__":
    main()
