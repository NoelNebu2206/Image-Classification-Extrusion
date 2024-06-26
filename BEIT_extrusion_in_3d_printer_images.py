import torch
import torch.nn as nn
import torchvision 
from tqdm import tqdm
import pandas as pd
from PIL import Image
import torch.optim as optim
from transformers import AdamW
from torch.utils.data import DataLoader
from torchvision import models, transforms
import torchvision.transforms as transforms
from timm.models import vision_transformer 
from sklearn.model_selection import train_test_split
from custom_dataset import CustomDataset, CustomDatasetTest
from timm.models.vision_transformer import vit_small_patch16_224
from sklearn.metrics import f1_score, accuracy_score
from transformers import BeitFeatureExtractor, BeitForMaskedImageModeling
from transformers import BeitModel
from PIL import Image
import requests
from transformers.image_processing_utils import BatchFeature
import torchvision.models as models

#model_resnet152 = models.resnet152(pretrained=True)

# Loading train and test dataframes
df = pd.read_csv('/scratch/ag8766/dlfinalproject/train.csv')  
#test_df = pd.read_csv('/scratch/ag8766/yogya/test.csv')    

df = df.drop(["printer_id", "print_id"], axis = 1)
#test_df = test_df.drop(["printer_id", "print_id"], axis = 1)

image_dir = '/scratch/ag8766/dlfinalproject/dataset'
batch_size = 64
df['img_path'] = image_dir + '/' + df['img_path'] 
#test_df['img_path'] = image_dir + '/' + test_df['img_path'] 

# Defining the data transforms
transform_train = transforms.Compose([
    transforms.RandomHorizontalFlip(),
    transforms.RandomRotation(10),
    transforms.Resize(256),
    transforms.RandomCrop(224),
    transforms.ToTensor(),
    transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
])

transform_val_test = transforms.Compose([
    transforms.Resize(256),
    transforms.CenterCrop(224),
    transforms.ToTensor(),
    transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
])


train_val_df, test_df = train_test_split(df, test_size=0.2, random_state=42)
train_df, val_df = train_test_split(train_val_df, test_size=0.2, random_state=42)

# Creating custom datasets 
train_dataset = CustomDataset(train_df, transform_train)
val_dataset = CustomDataset(val_df, transform_val_test)
test_dataset = CustomDataset(test_df, transform_val_test)

# Creating data loaders for train, validation, and test sets
train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=4)
val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=4)
test_loader = DataLoader(test_dataset, batch_size=64, shuffle=False, num_workers=4)

class CustomClassifierBeIT(nn.Module):
    def __init__(self, num_classes):
        super(CustomClassifierBeIT, self).__init__()
        self.backbone = BeitModel.from_pretrained('microsoft/beit-base-patch16-224-pt22k')
        self.fc1 = nn.Linear(self.backbone.config.hidden_size, 256)
        self.relu = nn.ReLU()
        self.fc2 = nn.Linear(256, 128)
        self.fc3 = nn.Linear(128, num_classes)
        self.sigmoid = nn.Sigmoid()

    def forward(self, input_dict ):
        backbone_outputs = self.backbone(**input_dict)

        # Apply fully connected layers
        x = self.fc1(backbone_outputs.pooler_output)
        x = self.relu(x)
        x = self.fc2(x)
        x = self.relu(x)
        x = self.fc3(x)
        x = self.sigmoid(x)
        
        return x

# Define the number of classes in your classification problem
num_classes = 2  # Replace with the actual number of classes in your dataset

# Create an instance of the model
model = CustomClassifierBeIT(num_classes)

# Freezing all parameters except last transformer layer and fully connected layers
for param in model.parameters():
  param.requires_grad = False
for param in model.backbone.encoder.layer[11].parameters():
  param.requires_grad = True
for param in model.fc1.parameters():
  param.requires_grad = True
for param in model.fc2.parameters():
  param.requires_grad = True

num_trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
print('Number of trainable parameters:', num_trainable)

# Putting the model on a GPU
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
model.to(device)

# loss function and optimizer
criterion = nn.CrossEntropyLoss()
optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

# learning rate scheduler
scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=7, gamma=0.1)

# Train
import torch
import matplotlib.pyplot as plt
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
from tqdm import tqdm

# Lists to store metrics for plotting
train_losses = []
train_accuracies = []
val_losses = []
val_accuracies = []

num_epochs = 20 
best_val_acc = 0.0

for epoch in range(num_epochs):
    print(f'Epoch [{epoch+1}/{num_epochs}] Started')
    model.train()
    train_loss = 0.0
    train_correct = 0
    train_total = 0

    train_progress = tqdm(train_loader, desc=f"Training epoch {epoch}")

    for inputs, labels in train_progress:
        labels = labels.to(device)
        inputs = inputs.to(device)

        optimizer.zero_grad()
        
        outputs = model(BatchFeature({'pixel_values':inputs}) )
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        train_loss += loss.item() * inputs.size(0)

        _, predicted = outputs.max(1)
        train_total += labels.size(0)
        train_correct += predicted.eq(labels).sum().item()

    train_acc = train_correct / train_total
    train_losses.append(train_loss / len(train_dataset))
    train_accuracies.append(train_acc)

    scheduler.step()

    # Validate
    model.eval()
    val_loss = 0.0
    val_correct = 0
    val_total = 0

    val_progress = tqdm(val_loader, desc=f"Validation epoch {epoch}")

    with torch.no_grad():
        for inputs, labels in val_progress:
            inputs = inputs.to(device)
            labels = labels.to(device)

            outputs = model(BatchFeature({'pixel_values':inputs}) )
            loss = criterion(outputs, labels)
            val_loss += loss.item() * inputs.size(0)

            _, predicted = outputs.max(1)
            val_total += labels.size(0)
            val_correct += predicted.eq(labels).sum().item()

    val_acc = val_correct / val_total
    val_losses.append(val_loss / len(val_dataset))
    val_accuracies.append(val_acc)

    val_f1_score = f1_score(labels.cpu().numpy(), predicted.cpu().numpy())

    print(f'Epoch [{epoch+1}/{num_epochs}] Train Loss: {train_loss / len(train_dataset):.4f}')
    print(f'Train Acc: {train_acc:.4f}')
    print(f'Val Loss: {val_loss / len(val_dataset):.4f}')
    print(f'Val Acc: {val_acc:.4f}')
    print(f'Val F1 score: {val_f1_score:.4f}')

    # Saving the best model based on validation accuracy
    if val_acc > best_val_acc:
        best_val_acc = val_acc
        best_model_state_dict = model.state_dict()
        torch.save(best_model_state_dict, 'best_model_ResNet.pth')

# Plotting training accuracy and validation accuracy
plt.figure(figsize=(10, 5))
plt.plot(range(1, num_epochs + 1), train_accuracies, label='Training Accuracy')
plt.plot(range(1, num_epochs + 1), val_accuracies, label='Validation Accuracy')
plt.xlabel('Epoch')
plt.ylabel('Accuracy')
plt.title('BeIT Training Accuracy vs. Validation Accuracy')
plt.legend()
plt.show()

# Plotting training loss and validation loss
plt.figure(figsize=(10, 5))
plt.plot(range(1, num_epochs + 1), train_losses, label='Training Loss')
plt.plot(range(1, num_epochs + 1), val_losses, label='Validation Loss') 
plt.xlabel('Epoch') 
plt.ylabel('Loss') 
plt.title('BeIT Training Loss vs. Validation Loss') 
plt.legend() 
plt.show()

# Loading the best model state dict
model.load_state_dict(best_model_state_dict)

# test
model.eval()
test_preds = []
true_labels = []

with torch.no_grad():
    for inputs, labels in test_loader:
        inputs = inputs.to(device)
        labels = labels.to(device)

        outputs = model(BatchFeature({'pixel_values':inputs}) )
        _, predicted = outputs.max(1)

        test_preds.extend(predicted.tolist())
        true_labels.extend(labels.tolist())

accuracy = accuracy_score(true_labels, test_preds)
precision = precision_score(true_labels, test_preds, average='weighted')
recall = recall_score(true_labels, test_preds, average='weighted')
f1 = f1_score(true_labels, test_preds, average='weighted')

print("BeIT Test Accuracy:", accuracy)
print("BeIT Precision:", precision)
print("BeIT Recall:", recall)
print("BeIT F1 Score:", f1)


print('Train losses:', train_losses)
print('Train accuracies:', train_accuracies)
print('Val losses:',val_losses)
print('Val accuracies:',val_accuracies)

