import torch
import torch_npu
import os

device = int(os.getenv('LOCAL_RANK'))
#print(device)
torch.npu.set_device(device)
print(device)
# Call the hccl init process
torch.distributed.init_process_group(backend='hccl', init_method='env://')

a = torch.ones(1).npu()
print(device)

torch.distributed.all_reduce(a)
print('Hccl: ', a.cpu(), device)

