import math
import torch
import torch.nn as nn

class InputEmbeddings(nn.Module):

    def __init__(self,d_model: int, vocab_size: int):
        super().__init__()
        self.d_model = d_model
        self.vocab_size = vocab_size
        self.embedding = nn.Embedding(vocab_size, d_model)

    def forward(self, x):
        return self.embedding(x) * math.sqrt(self.d_model)   # in original paper they says multiply by sqrt of d_model to embeddings because without it magnitude of embedding would be too small compare to the positional encoding

class PositionalEncoding(nn.Module):

    def __init__(self, d_model: int, seq_len: int, dropout: float):
        super().__init__()
        self.d_model = d_model
        self.seq_len = seq_len
        self.dropout = nn.Dropout(dropout)

        # create a metrics of len (seq_len, d_model)
        pe = torch.zeros(seq_len, d_model)
        # create a vector of shape (seq_len, 1)
        position = torch.arange(0, seq_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * ( -math.log(10000.0) / d_model)) # in here the torch.arange(0, dmodel, 2) is representing the frequencies of sin/cos pair so we use each continuous two dim as one frequency or in other word a sin/cos pair 

        # apply the sin to even dim
        pe[:,0::2] = torch.sin(position * div_term)
        # apply the cos to odd dim
        pe[:,1::2] = torch.cos(position * div_term)

        pe = pe.unsqueeze(0) # to add batch_size dim

        self.register_buffer('pe', pe)

    def forward(self, x):
        x = x + (self.pe[:, :x.shape[1], :]).requires_grad_(False)
        return self.dropout(x)
    
class LayerNormalization(nn.Module):

    def __init__(self, eps: float = 10**-6) -> None:
        super().__init__()
        self.eps = eps
        # we use this two params because sometimes the values between 0 and 1 would be too restrictive so we assign this two params so the network will learn to tune these params whenever its necessory
        self.alpha = nn.Parameter(torch.ones(1)) # multiplicative
        self.bias = nn.Parameter(torch.zeros(1)) # additive

    def forward(self, x):
        mean = x.mean(dim = -1, keepdim= True)
        std = x.std(dim = -1, keepdim= True)
        return self.alpha * (x - mean) / (std + self.eps) + self.bias
    
class FeedForwardBlock(nn.Module):

    def __init__(self, d_model: int, d_ff: int, dropout: float) -> None:
        super().__init__()
        self.linear_1 = nn.Linear(d_model, d_ff) # W1 and B1
        self.dropout = nn.Dropout(dropout)
        self.linear_2 = nn.Linear(d_ff, d_model) # W2 and B2

    # (batch_size, seq_len, d_model) -> (batch_size, seq_len, d_ff) -> (batch_size, seq_len, d_model)
    def forward(self, x):
        return self.linear_2(self.dropout(torch.relu(self.linear_1(x))))
    
class MultiHeadAttention(nn.Module):

    def __init__(self, d_model: int, h: int, dropout: float):
        super().__init__()
        self.d_model = d_model
        self.h = h
        assert d_model % h == 0, "d_model is not divisible by h"

        self.d_k = d_model // h # Dimension of vector seen by each head
        self.w_q = nn.Linear(d_model, d_model, bias=False) # Wq
        self.w_k = nn.Linear(d_model, d_model, bias=False) # Wk
        self.w_v = nn.Linear(d_model, d_model, bias=False) # Wv
        self.w_o = nn.Linear(d_model, d_model, bias=False) # Wo
        self.dropout = nn.Dropout(dropout)

    @staticmethod
    def self_attention(query, key, value, mask, dropout: nn.Dropout):
        d_k = query.shape[-1]
        # Just apply the formula from the paper
        # (batch, h, seq_len, d_k) --> (batch, h, seq_len, seq_len)
        attention_scores = (query @ key.transpose(-2, -1)) / math.sqrt(d_k)
        if mask is not None:
            # Write a very low value (indicating -inf) to the positions where mask == 0
            attention_scores.masked_fill_(mask == 0, -1e9)
        attention_scores = attention_scores.softmax(dim=-1) # (batch, h, seq_len, seq_len) # Apply softmax
        if dropout is not None:
            attention_scores = dropout(attention_scores)
        # (batch, h, seq_len, seq_len) --> (batch, h, seq_len, d_k)
        # return attention scores which can be used for visualization
        return (attention_scores @ value), attention_scores


    def forward(self, q, k, v, mask):
        query = self.w_q(q) # (batch_size, seq_len, d_model) -> (batch_size, seq_len, d_model) 
        key = self.w_k(k) # (batch_size, seq_len, d_model) -> (batch_size, seq_len, d_model) 
        value = self.w_v(v) # (batch_size, seq_len, d_model) -> (batch_size, seq_len, d_model) 

        # (batch_size, seq_len, d_model) -> (batch_size, seq_len, h, d_k) -> (batch_size, h, seq_len, d_k)
        query = query.view(query.shape[0], query.shape[1], self.h, self.d_k).transpose(1,2)
        key = key.view(key.shape[0], key.shape[1], self.h, self.d_k).transpose(1,2)
        value = value.view(value.shape[0], value.shape[1], self.h, self.d_k).transpose(1,2)
        
        x, self.attention_scores = MultiHeadAttention.self_attention(query, key, value, mask, self.dropout) 
        #         x              = (batch_size, h, seq_len, d_k)
        # self.Attention_score = (batch_size, h, seq_len, seq_len)

        # (batch_size, h, seq_len, d_k) -> (batch_size, seq_len, h, d_k) -> (batch_size, seq_len, d_model)
        x = x.transpose(1, 2).contiguous().view(x.shape[0], -1, self.h * self.d_k) 
        # use of -1 :> Let PyTorch figure out what the seq_len is — I just care about combining the heads.

        # (batch_size, seq_len, d_model) -> (batch_size, seq_len, d_model)
        return self.w_o(x)
    
class ResidualConnection(nn.Module):
    
    def __init__(self, dropout: float):
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        self.norm = LayerNormalization()

    def forward(self, x, sublayer):
        return x + self.dropout(sublayer(self.norm(x)))
    
class EncoderBlock(nn.Module):

    def __init__(self, self_attention_block: MultiHeadAttention, feed_forward_block: FeedForwardBlock, dropout: float):
        super().__init__()
        self.self_attention_block = self_attention_block
        self.feed_forward_block = feed_forward_block
        self.residual_connection = nn.ModuleList([ResidualConnection(dropout) for _ in range(2)])

    def forward(self, x, src_mask): # src_mask -> we use it becuase we don't want to padding interect with other values

        x = self.residual_connection[0](x, lambda x: self.self_attention_block(x, x, x, src_mask)) # we use lambda because we want to pass normalized x instead of raw x
        x = self.residual_connection[1](x, self.feed_forward_block)
        return x
    
class Encoder(nn.Module):
    def __init__(self, layers: nn.ModuleList):
        super().__init__()
        self.layers = layers
        self.norm = LayerNormalization()

    def forward(self, x, mask):
        for layer in self.layers:
            x = layer(x, mask)
        return self.norm(x)
    
class DecoderBlock(nn.Module):

    def __init__(self, self_attention_block: MultiHeadAttention, cross_attention_block: MultiHeadAttention, feed_forward_block: FeedForwardBlock, dropout: float):
        super().__init__()
        self.self_attention_block = self_attention_block
        self.cross_attention_block = cross_attention_block
        self.feed_forward_block = feed_forward_block
        self.residual_connection = nn.ModuleList(ResidualConnection(dropout) for _ in  range(3))

    def forward(self, x, encoder_output, src_mask, trg_mask):
        x = self.residual_connection[0](x, lambda x: self.self_attention_block(x, x, x, trg_mask))
        x = self.residual_connection[1](x, lambda x: self.cross_attention_block(x, encoder_output, encoder_output, src_mask))
        x = self.residual_connection[2](x, self.feed_forward_block) # (batch_size, seq_len, d_model)            
        return x
    
class Decoder(nn.Module):

    def __init__(self, layers: nn.ModuleList):
        super().__init__()
        self.layers = layers
        self.norm = LayerNormalization()

    def forward(self, x, encoder_output, src_mask, trg_mask):
        for layer in self.layers:
            x = layer(x, encoder_output, src_mask, trg_mask)
        return self.norm(x)

class ProjectionLayer(nn.Module):

    def __init__(self, d_model: int, vocab_size: int):
        super().__init__()
        self.proj = nn.Linear(d_model, vocab_size)

    def forward(self, x):
        # (batch_size, seq_len, d_model) -> (batch_size, seq_len, vocab_size)
        return torch.log_softmax(self.proj(x), dim = -1)
    
class Transformer(nn.Module):

    def __init__(self, encoder: Encoder, decoder: Decoder, src_embed: InputEmbeddings, trg_embed: InputEmbeddings, src_pos: PositionalEncoding, trg_pos: PositionalEncoding, projection_layer: ProjectionLayer):
        super().__init__()
        self.encoder = encoder
        self.decoder = decoder
        self.src_embed = src_embed
        self.trg_embed = trg_embed
        self.src_pos = src_pos
        self.trg_pos = trg_pos
        self.projection_layer = projection_layer

    def encode(self, src, src_mask):
        src = self.src_embed(src)
        src = self.src_pos(src)
        return self.encoder(src, src_mask)
    
    def decode(self, encoder_output, src_mask, trg, trg_mask):
        trg = self.trg_embed(trg)
        trg = self.trg_pos(trg)
        return self.decoder(trg, encoder_output, src_mask, trg_mask)
    
    def project(self, x):
        return self.projection_layer(x)
    
def build_transformer(src_vocab_size: int, trg_vocab_size: int, src_seq_len: int, trg_seq_len: int, d_model: int = 512, N: int = 6, h: int = 8, dropout: float = 0.1, d_ff: int = 2048) -> Transformer:
    
    # crreate embedding layers
    src_embed = InputEmbeddings(d_model, src_vocab_size)
    trg_embed = InputEmbeddings(d_model, trg_vocab_size)

    # create positional encoding
    src_pos = PositionalEncoding(d_model, src_seq_len, dropout)
    trg_pos = PositionalEncoding(d_model, src_seq_len, dropout)

    # create the encoder block
    encoder_blocks = []
    for _ in range(N):
        encoder_self_attention_block = MultiHeadAttention(d_model, h, dropout)
        feed_forward_block = FeedForwardBlock(d_model, d_ff, dropout)
        encoder_block = EncoderBlock(encoder_self_attention_block, feed_forward_block, dropout)
        encoder_blocks.append(encoder_block)

    # create the decoder block
    decoder_blocks = []
    for _ in range(N):
        decoder_self_attention_block = MultiHeadAttention(d_model, h, dropout)
        decoder_cross_attention_block = MultiHeadAttention(d_model, h, dropout)
        feed_forward_block = FeedForwardBlock(d_model, d_ff, dropout)
        decoder_block = DecoderBlock(decoder_self_attention_block, decoder_cross_attention_block, feed_forward_block, dropout)
        decoder_blocks.append(decoder_block)

    # create the encoder and decoder
    encoder = Encoder(nn.ModuleList(encoder_blocks))
    decoder = Decoder(nn.ModuleList(decoder_blocks))

    # create projection layer
    projection_layer = ProjectionLayer(d_model, trg_vocab_size)

    # create the transformer
    transformer = Transformer(encoder, decoder, src_embed, trg_embed, src_pos, trg_pos, projection_layer)

    # initialize the parameter
    for p in transformer.parameters():
        if p.dim() > 1:
            nn.init.xavier_uniform_(p)

    return transformer