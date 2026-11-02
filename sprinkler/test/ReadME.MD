## 1、test_allreduce 用于检测基础链路功能
### (1)单机：
本机: `torchrun --master_addr=localhost --master_port=20022 --nnodes=1 --node_rank=0 --nproc_per_node=8 test_allreduce.py`
### (2)多机：
Node1: `torchrun --master_addr=Node1_IP --master_port=20022 --nnodes=2 --node_rank=0 --nproc_per_node=8 test_allreduce.py`
Node2: `torchrun --master_addr=Node1_IP --master_port=20022 --nnodes=2 --node_rank=1 --nproc_per_node=8 test_allreduce.py`

## 2、test_all2all 用于检测全链路通路
### (1)单机：
本机: `torchrun --master_addr=localhost --master_port=20022 --nnodes=1 --node_rank=0 --nproc_per_node=8 test_all2all.py`
### (2)多机：
Node1: `torchrun --master_addr=Node1_IP --master_port=20022 --nnodes=2 --node_rank=0 --nproc_per_node=8 test_all2all.py`
Node2: `torchrun --master_addr=Node1_IP --master_port=20022 --nnodes=2 --node_rank=1 --nproc_per_node=8 test_all2all.py`

## 3、test_gpt 用于检测单机/单卡的快慢卡
### (1)单机1P：DV120单卡8.25s
本机: `torchrun --nproc_per_node=1 test_gpt.py`
### (2)单机8P：DV120单卡8.25s
本机: `torchrun --nproc_per_node=8 test_gpt.py`
### (3)多机：
Node1: `torchrun --master_addr=Node1_IP --master_port=20022 --nnodes=2 --node_rank=0 --nproc_per_node=8 test_gpt.py`
Node2: `torchrun --master_addr=Node1_IP --master_port=20022 --nnodes=2 --node_rank=1 --nproc_per_node=8 test_gpt.py`

PS：单卡或多卡指定卡，	`export ASCEND_RT_VISIBLE_DEVICES="0,1,2,3,4,5,6,7"`