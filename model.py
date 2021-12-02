import torch
import torch.nn as nn
from chexnet import DenseNet121
from collections import OrderedDict

# vocab indices
PAD = 0
START = 1
END = 2
UNK = 3
#####################
# Encoder ChexNet
#####################
class Encoder(nn.Module):
    def __init__(self,ckpt_path):
        super(Encoder, self).__init__()
        self.chexnet = DenseNet121()
        checkpoint = torch.load(ckpt_path)
        fixed_state_dict = OrderedDict()
        for k, v in checkpoint["state_dict"].items():
            k = k.replace("module.", "") # removing ‘module.’ from key
            k = k.replace( '.norm.1', '.norm1')
            k = k.replace( '.conv.1', '.conv1')
            k = k.replace( '.norm.2', '.norm2')
            k = k.replace( '.conv.2', '.conv2')    
            fixed_state_dict[k] = v  
        self.chexnet.load_state_dict(fixed_state_dict)
        self.conv_layers= self.chexnet.densenet121.features
        self.adaptive_pool = nn.AdaptiveAvgPool2d((14, 14))

    def forward(self, image1,image2):
        out1 = self.adaptive_pool(self.conv_layers(image1))
        out2 = self.adaptive_pool(self.conv_layers(image2))
        # concat 2 images in the channel dimension
        out = torch.cat((out1,out2),dim=1) 
        # batch_size, img size, imgs size, 2048
        out = out.permute(0, 2, 3, 1)
        return out

####################
# Attention Decoder
####################
class Decoder(nn.Module):

    def __init__(self, vocab, use_bert=False):
        super(Decoder, self).__init__()
        self.encoder_dim = 2048
        self.attention_dim = 512
        self.use_bert = use_bert
        self.vocab=vocab
        if use_bert:
            self.embed_dim = 768
        else:
            self.embed_dim = 512

        self.decoder_dim = 512
        self.vocab_size = len(vocab)
        self.dropout = 0.5
        
        # soft attention
        self.enc_att = nn.Linear(2048, 512)
        self.dec_att = nn.Linear(512, 512)
        self.att = nn.Linear(512, 1)
        self.relu = nn.ReLU()
        self.softmax = nn.Softmax(dim=1)

        # decoder layers
        self.dropout = nn.Dropout(p=self.dropout)
        self.decode_step = nn.LSTMCell(self.embed_dim + self.encoder_dim, self.decoder_dim, bias=True)
        self.h_lin = nn.Linear(self.encoder_dim, self.decoder_dim)
        self.c_lin = nn.Linear(self.encoder_dim, self.decoder_dim)
        self.f_beta = nn.Linear(self.decoder_dim, self.encoder_dim)
        self.sigmoid = nn.Sigmoid()
        self.fc = nn.Linear(self.decoder_dim, self.vocab_size)

        # init variables
        self.fc.bias.data.fill_(0)
        self.fc.weight.data.uniform_(-0.1, 0.1)

        if not use_bert:
            self.embedding = nn.Embedding(self.vocab_size, self.embed_dim)
            self.embedding.weight.data.uniform_(-0.1, 0.1)
            for p in self.embedding.parameters():
                p.requires_grad = True

        ### Using bio bert here
        # else:
        #     # Load pre-trained model tokenizer (vocabulary)
        #     tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
        #     # Load pre-trained model (weights)
        #     BertModel = BertModel.from_pretrained('bert-base-uncased').to(device)
        #     BertModel.eval()

    def forward(self, encoder_out, encoded_captions, caption_lengths):    
        batch_size = encoder_out.size(0)
        encoder_dim = encoder_out.size(-1)
        vocab_size = self.vocab_size
        dec_len = [x-1 for x in caption_lengths]
        max_dec_len = max(dec_len)

        encoder_out = encoder_out.view(batch_size, -1, encoder_dim)
        num_pixels = encoder_out.size(1)

        # load bert or regular embeddings
        if not self.use_bert:
            embeddings = self.embedding(encoded_captions)

        ### using bio bert here
        # elif self.use_bert:
        #     embeddings = []
        #     for cap_idx in encoded_captions:
                
        #         # padd caption to correct size
        #         while len(cap_idx) < max_dec_len:
        #             cap_idx.append(PAD)
                    
        #         cap = ' '.join([self.vocab.idx2word[word_idx.item()] for word_idx in cap_idx])
        #         cap = u'[CLS] '+cap
                
        #         tokenized_cap = tokenizer.tokenize(cap)                
        #         indexed_tokens = tokenizer.convert_tokens_to_ids(tokenized_cap)
        #         tokens_tensor = torch.tensor([indexed_tokens]).to(device)

        #         with torch.no_grad():
        #             encoded_layers, _ = BertModel(tokens_tensor)

        #         bert_embedding = encoded_layers[11].squeeze(0)
                
        #         split_cap = cap.split()
        #         tokens_embedding = []
        #         j = 0

        #         for full_token in split_cap:
        #             curr_token = ''
        #             x = 0
        #             for i,_ in enumerate(tokenized_cap[1:]): # disregard CLS
        #                 token = tokenized_cap[i+j]
        #                 piece_embedding = bert_embedding[i+j]
                        
        #                 # full token
        #                 if token == full_token and curr_token == '' :
        #                     tokens_embedding.append(piece_embedding)
        #                     j += 1
        #                     break
        #                 else: # partial token
        #                     x += 1
                            
        #                     if curr_token == '':
        #                         tokens_embedding.append(piece_embedding)
        #                         curr_token += token.replace('#', '')
        #                     else:
        #                         tokens_embedding[-1] = torch.add(tokens_embedding[-1], piece_embedding)
        #                         curr_token += token.replace('#', '')
                                
        #                         if curr_token == full_token: # end of partial
        #                             j += x
        #                             break                            

        #         cap_embedding = torch.stack(tokens_embedding)
        #         embeddings.append(cap_embedding)
  
        #     embeddings = torch.stack(embeddings)

        # init hidden state
        avg_enc_out = encoder_out.mean(dim=1)
        h = self.h_lin(avg_enc_out)
        c = self.c_lin(avg_enc_out)

        predictions = torch.zeros(batch_size, max_dec_len, vocab_size,device=encoder_out.device)
        alphas = torch.zeros(batch_size, max_dec_len, num_pixels,device=encoder_out.device)

        for t in range(max(dec_len)):
            batch_size_t = sum([l > t for l in dec_len ])
            
            # soft-attention
            enc_att = self.enc_att(encoder_out[:batch_size_t])
            dec_att = self.dec_att(h[:batch_size_t])
            att = self.att(self.relu(enc_att + dec_att.unsqueeze(1))).squeeze(2)
            alpha = self.softmax(att)
            attention_weighted_encoding = (encoder_out[:batch_size_t] * alpha.unsqueeze(2)).sum(dim=1)
        
            gate = self.sigmoid(self.f_beta(h[:batch_size_t]))
            attention_weighted_encoding = gate * attention_weighted_encoding
            
            batch_embeds = embeddings[:batch_size_t, t, :]            
            cat_val = torch.cat([batch_embeds.double(), attention_weighted_encoding.double()], dim=1)
            
            h, c = self.decode_step(cat_val.float(),(h[:batch_size_t].float(), c[:batch_size_t].float()))
            preds = self.fc(self.dropout(h))
            predictions[:batch_size_t, t, :] = preds
            alphas[:batch_size_t, t, :] = alpha
            
        # preds, sorted capts, dec lens, attention weights
        return predictions, encoded_captions, dec_len, alphas