# /bin/bash
# 全自动开局脚本，cann拷贝的路径根据实际需要的cann版本进行修改，或注释掉cann相关部分
export ip_addr=141.3.244.150
export http_proxy="http://p_atlas:proxy%40123@$ip_addr:8080"
export https_proxy=$http_proxy
export no_proxy=127.0.0.1,localhost,local,.local
git config --global http.proxy http://p_atlas:proxy%40123@$ip_addr:8080
git config --global https.proxy http://p_atlas:proxy%40123@$ip_addr:8080
git config --global http.sslVerify false
export GIT_SSL_NO_VERIFY=1
conda config --set proxy_servers.http http://p_atlas:proxy%40123@$ip_addr:8080
conda config --set proxy_servers.https http://p_atlas:proxy%40123@$ip_addr:8080
conda config --set ssl_verify false
pip3 config set global.index-url http://mirrors.aliyun.com/pypi/simple/
pip3 config set global.trusted-host mirrors.aliyun.com


PASSWORD="128P888#$"
# 免密
timeout 15 expect <<EOF 2>/dev/null
set timeout 10
log_user 0
spawn ssh-copy-id -o StrictHostKeyChecking=no -o ConnectTimeout=5 141.61.50.31
expect {
    "yes/no" { send "yes\r"; exp_continue }
    "password:" { send "$PASSWORD\r"; exp_continue }
    "Password:" { send "$PASSWORD\r"; exp_continue }
    "Permission denied" { exit 1 }
    timeout { exit 2 }
    eof
}
EOF

rm /etc/yum.repos.d/openEuler.repo
scp -r root@141.61.50.31:/etc/yum.repos.d/openEuler.repo /etc/yum.repos.d/
# 查一下还没安装的，根据需要安装
echo "sslverify=false" >> /etc/yum.conf
echo "deltarpm=0" >> /etc/yum.conf
yum install vim git make cmake g++ sshpass rsync --nogpgcheck -y

# 拷贝所有需要的文件
cd /home
mkdir dataset
mkdir x30061065

# 自动重传
SOURCE=(
    "root@141.61.50.31:/home/x30061065/start.zip"
    "root@141.61.50.31:/home/x30061065/pkgs-0420/"
)
DEST=(
    "/home/x30061065/"
    "/home/x30061065/pkgs-0420/"
)

# 检查列表长度是否一致
if [ "${#SOURCE[@]}" -ne "${#DEST[@]}" ]; then
    echo "错误: SOURCE 列表的数量 (${#SOURCE[@]}) 与 DEST 列表的数量 (${#DEST[@]}) 不一致，请检查配置！"
    exit 1
fi
echo "检测到共 ${#SOURCE[@]} 个传输任务。"

# 遍历数组索引
for i in "${!SOURCE[@]}"; do
    # 获取当前对应的源和目标
    CURRENT_SOURCE="${SOURCE[$i]}"
    CURRENT_DEST="${DEST[$i]}"
    echo "================================================"
    echo "开始处理任务 [ $((i+1)) / ${#SOURCE[@]} ]: $CURRENT_SOURCE -> $CURRENT_DEST"
    # 针对当前任务开始重试循环
    while true; do
        echo "[$(date)] 正在传输..."
        # rsync 核心参数说明:
        # -a: 归档模式，保留文件属性
        # -P: 显示进度条，并支持断点续传 (等同于 --partial --progress)
        # -z: 传输时压缩数据
        # -e: 指定使用的ssh协议，可以在这里指定端口，如 -p 22
        sshpass -p "$PASSWORD" rsync -avzP -e "ssh -o StrictHostKeyChecking=no" "$CURRENT_SOURCE" "$CURRENT_DEST"

        # 检查上一条命令的退出状态
        # 0 表示成功
        if [ $? -eq 0 ]; then
            echo "[$(date)] 任务 [ $((i+1)) ] 传输成功完成！"
            break # 跳出重试循环，处理下一个任务
        else
            echo "[$(date)] 任务 [ $((i+1)) ] 传输中断或出错, 3秒后自动重试..."
            sleep 3
        fi
    done
done
echo "================================================"
echo "[$(date)] 所有传输任务已完成！"



cd /home/x30061065/
unzip start.zip
cd start

mv dataset /home/dataset
mv llm_code ../

# 安装CANN
mkdir /home/pckg
cd /home/pckg
mkdir 0420
cd /home/x30061065/pkgs-0420/
chmod -R 755 ./

export CSPATH=/home/pckg/0420
bash Ascend-cann-toolkit* --install --quiet --install-path=$CSPATH
bash Ascend-cann-950-ops* --install --quiet --install-path=$CSPATH

# 装conda
cd /home/x30061065/start/
bash Miniconda3-py310_25.5.1-1-Linux-aarch64.sh -b -p /home/pckg/miniconda3/
/home/pckg/miniconda3/bin/conda init --all
source ~/.bashrc
cd /home/x30061065/start/
unzip xyh_py310.zip
mv xyh_py310 /home/pckg/miniconda3/envs/


conda activate xyh_py310
# rootinfo
# python3 -m pip install unofficial-ascend-tools
pip install unofficial-ascend-tools==0.0.7rc2
mindcluster-tools rootinfo --output hccl_rootinfo.json  
cp hccl_rootinfo.json /etc

# pta
cd /home/x30061065/start/pta
pip install torch_npu*

