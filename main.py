'''
Parts of this code were incorporated from the following github repositories:
1. parksunwoo/show_attend_and_tell_pytorch
Link: https://github.com/parksunwoo/show_attend_and_tell_pytorch/blob/master/prepro.py

2. sgrvinod/a-PyTorch-Tutorial-to-Image-Captioning
Link: https://github.com/sgrvinod/a-PyTorch-Tutorial-to-Image-Captioning

This script has the Encoder and Decoder models and training/validation scripts. 
Edit the parameters sections of this file to specify which models to load/run
''' 

# coding: utf-8

import pickle
import torch.nn as nn
import torch
from torch.nn.utils.rnn import pack_padded_sequence
from nltk.translate.bleu_score import corpus_bleu
from tqdm import tqdm
import numpy as np
import matplotlib.pyplot as plt
from PIL import Image
from model import Encoder,Decoder
from dataset import Dataset
from build_vocab import Vocabulary
import torchvision.transforms as transforms
###################
# START Parameters
###################
# vocab indices
PAD = 0
START = 1
END = 2
UNK = 3

# hyperparams
grad_clip = 5.
num_epochs = 4
batch_size = 32 
decoder_lr = 0.0004

# if both are false them model = baseline
bert_model = False

from_checkpoint = False
train_model = True
valid_model = False

###################
# END Parameters
###################

# loss
class loss_obj(object):
    def __init__(self):
        self.avg = 0.
        self.sum = 0.
        self.count = 0.

    def update(self, val, n=1):
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

# Device configuration
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# Load vocabulary
with open('dataset/vocab.pkl', 'rb') as f:
    vocab = pickle.load(f)

# load data
transforms_ = transforms.Compose([transforms.ToTensor(),
                transforms.Resize(512), 
                transforms.RandomHorizontalFlip(),
                transforms.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)) ])

train_dataset=Dataset(df_path="dataset/df_train.pkl",vocab=vocab,transform=transforms_)
val_dataset=Dataset(df_path="dataset/df_val.pkl",vocab=vocab,transform=transforms_)

train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=1,shuffle=False)
val_loader = torch.utils.data.DataLoader(train_dataset, batch_size=1,shuffle=False)

#############
# Init model
#############

criterion = nn.CrossEntropyLoss().to(device)

if from_checkpoint:

    encoder = Encoder(ckpt_path="model.pth.tar").to(device)
    decoder = Decoder(vocab, use_bert=bert_model).to(device)

    if bert_model:
        print('Pre-Trained BERT Model')
        encoder_checkpoint = torch.load('./checkpoints/encoder_bert')
        decoder_checkpoint = torch.load('./checkpoints/decoder_bert')
    else:
        print('Pre-Trained Baseline Model')
        encoder_checkpoint = torch.load('./checkpoints/encoder_baseline')
        decoder_checkpoint = torch.load('./checkpoints/decoder_baseline')

    encoder.load_state_dict(encoder_checkpoint['model_state_dict'])
    decoder_optimizer = torch.optim.Adam(params=decoder.parameters(),lr=decoder_lr)
    decoder.load_state_dict(decoder_checkpoint['model_state_dict'])
    decoder_optimizer.load_state_dict(decoder_checkpoint['optimizer_state_dict'])
else:
    encoder = Encoder(ckpt_path="model.pth.tar").to(device)
    decoder = Decoder(vocab, use_bert=bert_model).to(device)
    decoder_optimizer = torch.optim.Adam(params=decoder.parameters(),lr=decoder_lr)

###############
# Train model
###############

def train():
    print("Started training...")
    for epoch in tqdm(range(num_epochs)):
        decoder.train()
        encoder.train()
        losses = loss_obj()
        num_batches = len(train_loader)

        for i, (img1s,img2s, caps,cap_lens) in enumerate(tqdm(train_loader)):

            imgs = encoder(img1s.to(device),img2s.to(device))
            caps = caps.to(device)

            scores, caps_sorted, decode_lengths, alphas = decoder(imgs, caps,cap_lens)
            scores = pack_padded_sequence(scores, decode_lengths, batch_first=True)[0]
            targets = caps_sorted[:, 1:]
            targets = pack_padded_sequence(targets, decode_lengths, batch_first=True)[0]
            loss = criterion(scores, targets).to(device)

            loss += ((1. - alphas.sum(dim=1)) ** 2).mean()

            decoder_optimizer.zero_grad()
            loss.backward()

            # grad_clip decoder
            for group in decoder_optimizer.param_groups:
                for param in group['params']:
                    if param.grad is not None:
                        param.grad.data.clamp_(-grad_clip, grad_clip)

            decoder_optimizer.step()

            losses.update(loss.item(), sum(decode_lengths))

            # save model each 100 batches
            if i%5000==0 and i!=0:
                print('epoch '+str(epoch+1)+'/4 ,Batch '+str(i)+'/'+str(num_batches)+' loss:'+str(losses.avg))
                
                 # adjust learning rate (create condition for this)
                for param_group in decoder_optimizer.param_groups:
                    param_group['lr'] = param_group['lr'] * 0.8

                print('saving model...')

                torch.save({
                    'epoch': epoch,
                    'model_state_dict': decoder.state_dict(),
                    'optimizer_state_dict': decoder_optimizer.state_dict(),
                    'loss': loss,
                    }, './checkpoints/decoder_mid')

                torch.save({
                    'epoch': epoch,
                    'model_state_dict': encoder.state_dict(),
                    'loss': loss,
                    }, './checkpoints/encode_mid')

                print('model saved')

        torch.save({
            'epoch': epoch,
            'model_state_dict': decoder.state_dict(),
            'optimizer_state_dict': decoder_optimizer.state_dict(),
            'loss': loss,
            }, './checkpoints/decoder_epoch'+str(epoch+1))

        torch.save({
            'epoch': epoch,
            'model_state_dict': encoder.state_dict(),
            'loss': loss,
            }, './checkpoints/encoder_epoch'+str(epoch+1))

        print('epoch checkpoint saved')

    print("Completed training...")  

#################
# Validate model
#################

def print_sample(hypotheses, references, test_references,imgs, alphas, k, show_att, losses):
    bleu_1 = corpus_bleu(references, hypotheses, weights=(1, 0, 0, 0))
    bleu_2 = corpus_bleu(references, hypotheses, weights=(0, 1, 0, 0))
    bleu_3 = corpus_bleu(references, hypotheses, weights=(0, 0, 1, 0))
    bleu_4 = corpus_bleu(references, hypotheses, weights=(0, 0, 0, 1))

    print("Validation loss: "+str(losses.avg))
    print("BLEU-1: "+str(bleu_1))
    print("BLEU-2: "+str(bleu_2))
    print("BLEU-3: "+str(bleu_3))
    print("BLEU-4: "+str(bleu_4))

    img_dim = 336 # 14*24
    
    hyp_sentence = []
    for word_idx in hypotheses[k]:
        hyp_sentence.append(vocab.idx2word[word_idx])
    
    ref_sentence = []
    for word_idx in test_references[k]:
        ref_sentence.append(vocab.idx2word[word_idx])

    print('Hypotheses: '+" ".join(hyp_sentence))
    print('References: '+" ".join(ref_sentence))
        
    # img = imgs[0][k] 
    # imageio.imwrite('img.jpg', img)
  
    # if show_att:
    #     image = Image.open('img.jpg')
    #     image = image.resize([img_dim, img_dim], Image.LANCZOS)
    #     for t in range(len(hyp_sentence)):

    #         plt.subplot(np.ceil(len(hyp_sentence) / 5.), 5, t + 1)

    #         plt.text(0, 1, '%s' % (hyp_sentence[t]), color='black', backgroundcolor='white', fontsize=12)
    #         plt.imshow(image)
    #         current_alpha = alphas[0][t, :].detach().numpy()
    #         alpha = skimage.transform.resize(current_alpha, [img_dim, img_dim])
    #         if t == 0:
    #             plt.imshow(alpha, alpha=0)
    #         else:
    #             plt.imshow(alpha, alpha=0.7)
    #         plt.axis('off')
    # else:
    #     img = imageio.imread('img.jpg')
    #     plt.imshow(img)
    #     plt.axis('off')
    #     plt.show()


def validate():

    references = [] 
    test_references = []
    hypotheses = [] 
    all_imgs = []
    all_alphas = []

    print("Started validation...")
    decoder.eval()
    encoder.eval()

    losses = loss_obj()

    num_batches = len(val_loader)
    # Batches
    for i, (img1s,img2s, caps,cap_lens) in enumerate(tqdm(val_loader)):

        imgs_jpg = img1s.numpy() 
        imgs_jpg = np.swapaxes(np.swapaxes(imgs_jpg, 1, 3), 1, 2)
        
        # Forward prop.
        imgs = encoder(img1s.to(device),img2s.to(device))
        caps = caps.to(device)

        scores, caps_sorted, decode_lengths, alphas = decoder(imgs, caps,cap_lens)
        targets = caps_sorted[:, 1:]

        # Remove timesteps that we didn't decode at, or are pads
        scores_packed = pack_padded_sequence(scores, decode_lengths, batch_first=True)[0]
        targets_packed = pack_padded_sequence(targets, decode_lengths, batch_first=True)[0]

        # Calculate loss
        loss = criterion(scores_packed, targets_packed)
        loss += ((1. - alphas.sum(dim=1)) ** 2).mean()
        losses.update(loss.item(), sum(decode_lengths))

         # References
        for j in range(targets.shape[0]):
            img_caps = targets[j].tolist() # validation dataset only has 1 unique caption per img
            clean_cap = [w for w in img_caps if w not in [PAD, START, END]]  # remove pad, start, and end
            img_captions = list(map(lambda c: clean_cap,img_caps))
            test_references.append(clean_cap)
            references.append(img_captions)

        # Hypotheses
        _, preds = torch.max(scores, dim=2)
        preds = preds.tolist()
        temp_preds = list()
        for j, p in enumerate(preds):
            pred = p[:decode_lengths[j]]
            pred = [w for w in pred if w not in [PAD, START, END]]
            temp_preds.append(pred)  # remove pads, start, and end
        preds = temp_preds
        hypotheses.extend(preds)
        
        if i == 0:
            all_alphas.append(alphas)
            all_imgs.append(imgs_jpg)

    print("Completed validation...")
    print_sample(hypotheses, references, test_references, all_imgs, all_alphas,1,False, losses)

######################
# Run training/validation
######################

if train_model:
    train()

if valid_model:
    validate()
