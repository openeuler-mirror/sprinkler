import os
import time

import torch
import torch_npu
from torch_npu.contrib import transfer_to_npu

import torch.nn as nn
import torch.optim as optim
import torch.distributed as dist
from torch.utils.data.distributed import DistributedSampler

# 定义Transformer解码器层
class TransformerDecoderLayer(nn.Module):
    def __init__(self, d_model, nhead, dim_feedforward=2048, dropout=0.1):
        super(TransformerDecoderLayer, self).__init__()
        self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
        self.feed_forward = nn.Sequential(
            nn.Linear(d_model, dim_feedforward),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(dim_feedforward, d_model)
        )
        self.dropout = nn.Dropout(dropout)
        self.norm1 = nn.LayerNorm(d_model)
        self.norm2 = nn.LayerNorm(d_model)
        
    def forward(self, x, attn_mask=None):
        # 自注意力机制
        attn_output, _ = self.self_attn(x, x, x, attn_mask=attn_mask)
        x = x + self.dropout(attn_output)
        x = self.norm1(x)
        
        # 前馈网络
        ff_output = self.feed_forward(x)
        x = x + self.dropout(ff_output)
        x = self.norm2(x)
        return x

# 定义GPT-3风格的模型
class GPT3StyleModel(nn.Module):
    def __init__(self, vocab_size, d_model=768, nhead=12, num_layers=12, dropout=0.1):
        super(GPT3StyleModel, self).__init__()
        self.embedding = nn.Embedding(vocab_size, d_model)
        self.positional_encoding = nn.Parameter(torch.randn(1, 1024, d_model))  # 位置编码
        self.layers = nn.ModuleList([
            TransformerDecoderLayer(d_model, nhead, dropout=dropout)
            for _ in range(num_layers)
        ])
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(d_model, vocab_size)
        
    def forward(self, x, attn_mask=None):
        seq_len = x.size(1)
        # 嵌入层
        x = self.embedding(x)
        # 位置编码
        x = x + self.positional_encoding[:, :seq_len, :]
        x = self.dropout(x)
        
        # 通过解码器层
        for layer in self.layers:
            x = layer(x, attn_mask=attn_mask)
            
        # 最终线性变换
        x = self.fc(x)
        return x

def train(args):
    # 初始化进程组
    device = int(os.getenv('LOCAL_RANK'))
    world_size = int(os.getenv('WORLD_SIZE'))
    torch.npu.set_device(device)
    torch.distributed.init_process_group(backend='hccl', init_method='env://')
 
    # 创建模型
    model = GPT3StyleModel(
        vocab_size=args.vocab_size,
        d_model=args.d_model,
        nhead=args.nhead,
        num_layers=args.num_layers
    )
    
    # 将模型移动到指定设备
    model.npu()
    
    # 创建优化器
    optimizer = optim.AdamW(model.parameters(), lr=args.lr)
    
    # 创建数据加载器
    train_dataset = torch.randint(low=0, high=args.vocab_size-1,
                                  size=(args.batch_size, args.seq_len))
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        shuffle=False
    )
    
    # 训练循环
    dist.barrier()
    step_start_time = time.time()
    h2d_time = 0
    computing_time = 0
    attn_mask = torch.triu(torch.ones(16, 16), diagonal=1).bool()
    attn_mask = attn_mask.npu()
    for epoch in range(args.epochs):
        for batch in train_loader:
            torch.npu.synchronize()
            h2d_start = time.time()
            batch = batch.npu()
            seq_len = batch.size(1)
            torch.npu.synchronize()
            h2d_time += (time.time() - h2d_start)

            # 前向传播
            torch.npu.synchronize()
            computing_start = time.time()
            outputs = model(batch, attn_mask=attn_mask)
                
            # 生成随机目标（移一位）
            targets = torch.roll(batch, shifts=-1, dims=1)
            targets[:, -1] = 0  # 最后一位无目标
                
            # 计算损失
            loss = nn.CrossEntropyLoss()(outputs.view(-1, args.vocab_size), targets.view(-1))
                
            # 反向传播
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            torch.npu.synchronize()
            computing_time += (time.time() - computing_start)

    step_time = time.time() - step_start_time
    print(f"Device: {device}, StepTime: {step_time:.4f}, H2DTime: {h2d_time:.4f}, ComputingTime: {computing_time:.4f}")
    
    # 清理进程组
    dist.destroy_process_group()

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch_size', type=int, default=16)
    parser.add_argument('--seq_len', type=int, default=1024)
    parser.add_argument('--vocab_size', type=int, default=50257)  # GPT-3的词汇量
    parser.add_argument('--d_model', type=int, default=768)
    parser.add_argument('--nhead', type=int, default=12)
    parser.add_argument('--num_layers', type=int, default=16)
    parser.add_argument('--lr', type=float, default=0.0001)
    parser.add_argument('--epochs', type=int, default=10)
    args = parser.parse_args()
    
    # 启动训练
    train(args)

if __name__ == '__main__':
    main()


