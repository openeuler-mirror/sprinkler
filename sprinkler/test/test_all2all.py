import os
import torch
import torch_npu
import torch.distributed as dist

local_rank = int(os.environ['LOCAL_RANK'])
global_rank = int(os.environ['RANK'])
torch.npu.set_device(local_rank) 
device = f"npu:{local_rank}"
print(f"==============={device}==============set device done")

dist.init_process_group(backend='hccl', init_method='env://')
world_size = dist.get_world_size()
   
input_tensor = torch.tensor([global_rank for _ in range(world_size)]).npu().bfloat16()
output_tensor_list = torch.empty([world_size]).npu().bfloat16()
dist.all_to_all_single(output_tensor_list, input_tensor)
print(output_tensor_list)

dist.destroy_process_group()
