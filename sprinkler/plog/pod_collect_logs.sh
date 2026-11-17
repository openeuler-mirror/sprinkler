#!/bin/bash

# 配置参数
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/config_util.sh"

# 配置参数
config_require_keys \
    "POD_USER|请输入CPU OS连接用户名|root|0|validate_non_empty" \
    "POD_PASSWORD|请输入CPU OS登录密码(输入不回显)||1|validate_non_empty" \
    "NPU_BMC_IP|请输入NPU_BMC_IP地址||0|validate_ip" \
    "NPU_BMC_PASS|请输入NPU_BMC登录密码(输入不回显)||1|validate_non_empty" \
    "NPU1213_PASS|请输入NPU 1213登录密码(输入不回显)||1|validate_non_empty" \
    "CPU_BMC_IP|请输入CPU_BMC_IP地址||0|validate_ip" \
    "CPU_BMC_PASS|请输入CPU_BMC登录密码(输入不回显)||1|validate_non_empty" \
    "CPU1213_PASS|请输入CPU 1213登录密码(输入不回显)||1|validate_non_empty" \
    "POD_NPU_USER|请输入连接NPU用户名|root|0|validate_non_empty" \
    "POD_NPU_PASSWORD|请输入登录NPU密码(输入不回显)||1|validate_non_empty"

USER=$POD_USER
PASSWORD=$POD_PASSWORD

# 设置默认路径
now_time=$(date +"%Y%m%d_%H%M")
DEST_DIR="${1:-./debug_log_${now_time}}"

echo "============================================="
ABS_DEST_DIR=$(readlink -f "$DEST_DIR")
echo "All log files stored at $ABS_DEST_DIR"
echo "============================================="


# 创建目录
mkdir -p "$DEST_DIR"


### 收集CPU日志 ###
mkdir -p "$DEST_DIR/CPU/var/log"
dmesg -T > "$DEST_DIR/CPU/var/log/dmesg"
cp /var/log/messages "$DEST_DIR/CPU/var/log/messages"


### 收集NPU日志 ###


# 获取当前NPU IP
SERVERS=($(bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/npuip.sh"))

# 配置免密操作
# [DEPRECATED] 引用文件已不存在，功能由 sprinkler -no-pass 取代
# bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/no_password.sh"  2>&1 | grep -E --line-buffered "ERROR|WARN|INFO"
:  # no-op（原命令已下线）

mkdir -p "$DEST_DIR/NPU"
# 为每个服务器收集日志
for i in "${!SERVERS[@]}";
do
    SERVER="${SERVERS[$i]}"
    # bash "$NO_PASSWORD_SCR $SERVER"
    NPU_NUM=$i
    REMOTE_FILE="/npu${NPU_NUM}_dmesg.txt"
    LOCAL_DMESG_FILE="$DEST_DIR/NPU/npu${NPU_NUM}_dmesg.txt"
    LOCAL_MESSAGES_FILE="$DEST_DIR/NPU/npu${NPU_NUM}_messages"

    echo "正在收集 NPU$NPU_NUM ($SERVER) 的日志..."

    # 在远程服务器执行dmesg并保存到文件
    ssh $POD_NPU_USER@$SERVER syslogd
    ssh $POD_NPU_USER@$SERVER "dmesg" > "$LOCAL_DMESG_FILE"
    scp -O $POD_NPU_USER@$SERVER:/var/log/messages "$LOCAL_MESSAGES_FILE"

    # 检查是否成功
    if [ $? -eq 0 ] && [ -s "$LOCAL_DMESG_FILE" ] && [ -s "$LOCAL_MESSAGES_FILE" ]; then
        echo "✓ NPU$NPU_NUM 日志收集成功"
    else
        echo "✗ NPU$NPU_NUM 日志收集失败"
    fi
done

mkdir -p $DEST_DIR/PLOG
cp -r /root/ascend/log $DEST_DIR/PLOG
cp -r /root/ascend/atrace $DEST_DIR/PLOG


### 收集msnpureport日志 ###
# 先在 /usr/local/Ascend/ 和/home/host/ 路径下查找 msnpureport
echo "查找msnpureport工具位置"
result=$(find /usr/local/Ascend/ -name "msnpureport" -type f 2>/dev/null)
if [ -z "$result" ]; then
    result=$(find /root/host -name "msnpureport" -type f 2>/dev/null)
fi

# 如果没找到，再全局查找
if [ -z "$result" ]; then
    result=$(find / -name "msnpureport" -type f 2>/dev/null)
fi

if [ -z "$result" ]; then
    echo "未找到 msnpureport 文件"
else
    # 取第一个找到的路径
    msnpureport_path=$(echo "$result" | head -n1)
    
    # 如果找到多个，提示使用的是哪个
    if [ $(echo "$result" | wc -l) -gt 1 ]; then
        echo "使用${msnpureport_path}路径的msnpureport工具采集"
    fi
    
    current_dir=$(pwd)
    # 执行收集命令
    "$msnpureport_path" -d 0 -m TS:debug
    cd /root/ascend/log
    "$msnpureport_path" -f

    # 查找最新的日期格式文件夹并复制到指定目录
    latest_dir=$(ls -td /root/ascend/log/*-*-*-*-*-* 2>/dev/null | head -n1)
    cd $current_dir
    if [ -n "$latest_dir" ] && [ -d "$latest_dir" ]; then
        mkdir -p $DEST_DIR/msnpureport
        echo "$latest_dir to  $DEST_DIR/msnpureport"
	    cp -r "$latest_dir" "$DEST_DIR/msnpureport"
        echo "msnpureport日志收集成功"
    else
        echo "未找到msnpureport采集的日志，请检查"
    fi
fi


### 收集UB及mami日志 ###
echo "收集NPU UB日志"
expect -c "
set timeout 10
spawn telnet $NPU_BMC_IP

# 登录BMC
expect \"Password:\" { send \"$NPU_BMC_PASS\\r\" }

# 切1213串口
expect -re \"~.*#\" { send \"ipmcset -t sol -d act -v 19 0\\r\" }

# 处理切串口后的提示
# 等待连接成功提示
expect {
    \"the operation can be viewed on another terminal.\" {
        sleep 10
        send \"\\r\"
        send \"\\r\"
        exp_continue
    }
    # 等待可能出现的密码提示
    \"Password:\" {
        send \"$NPU1213_PASS\\r\"
        exp_continue
    }
    # 等待进入设备提示符
    -re \"<\[^>\]+>\" {
        # 匹配成功，跳出此expect块
    }
}

# 进入系统视图和诊断视图
send \"sys\\r\"
expect -re \"\\[.*\\]\"
send \"diag\\r\"
expect -re \"diagnose\\]\"
send \"l s\\r\"

# 进入后台并传输文件
expect \"TOR / #\" { send \"\\r\" }
expect \"#\"

# 传输cpdt_dmesg_current文件
send \"scp -P 22 /opt/vrpv8/var/log/cpdt_dmesg_current* root@141.61.48.xx:/home/npu1213log/\\r\"
expect {
    # 处理首次连接的主机密钥验证提示
    \"Are you sure you want to continue connecting\" {
        send \"yes\\r\"
        send \"\\r\"
        exp_continue
    }
    # 等待密码提示
    \"password:\" {
        send \"Host密码\\r\"
    }
}
expect \"#\"

# 传输管控面用户态日志
send \"scp -P 22 /opt/vrpv8/var/log/cpdt_journal* root@141.61.48.xx:/home/npu1213log/\\r\"
expect \"password:\" { send \"Host密码\\r\" }
expect \"#\"

# 传输管控面内核态日志
send \"cp /proc/kbox/regions/panic /home/\\r\"
send \"scp -P 22 /home/panic root@141.61.48.xx:/home/npu1213log/\\r\"
expect \"password:\" { send \"Host密码\\r\" }
expect \"#\"

send \"exit\\r\"
expect -re \"diagnose\\]\"
send \"quit\\r\"
expect -re \"\\[.*\\]\"
send \"quit\\r\"
expect -re \"<\[^>\]+>\"
send \"quit\\r\"
send \"\\x1d\"
expect \"telnet>\"
send \"quit\\r\"
expect eof
"


echo "收集CPU UB日志"
expect -c "
set timeout 20
spawn ssh Administrator@$CPU_BMC_IP
expect {
    \"Are you sure you want to continue connecting\" {
        send \"yes\\r\"
        send \"\\r\"
        exp_continue
    }
    \"password:\" {
        send \"$CPU_BMC_PASS\\r\"
    }
}

# 切1213串口
expect -re \"~.*$\" {
    send \"ipmcset -d serialdir -v 4\\r\"
    sleep 15
    send \"ipmcset -t sol -d a -v 4 0\\r\"
}

# 处理切串口后的提示
# 等待连接成功提示
expect {
    \"the operation can be viewed on another terminal.\" {
        sleep 10
        send \"\\r\"
        send \"\\r\"
        exp_continue
    }
    # 等待可能出现的密码提示
    \"password:\" {
        send \"$CPU1213_PASS\\r\"
    }
    \"Password:\" {
        send \"$CPU1213_PASS\\r\"
    }
}

# 等待进入设备提示符
expect -re \"<\[^>\]+>\" {
    send \"sys\\r\"
}
# 进入系统视图和诊断视图
expect -re \"\\[.*\\]\" { send \"diag\\r\" }
expect -re \"diagnose\\]\" { send \"l s\\r\" }

# 进入后台并传输文件
expect \"TOR / #\" { send \"\\r\" }

# 传输cpdt_dmesg_current文件
expect \"#\" {
    send \"scp -P 22 /opt/vrpv8/var/log/cpdt_dmesg_current* root@141.61.48.xx:/home/cpu1213log/\\r\"
}

expect {
    # 处理首次连接的主机密钥验证提示
    \"Are you sure you want to continue connecting\" {
        send \"yes\\r\"
        send \"\\r\"
        exp_continue
    }
    # 等待密码提示
    \"Password:\" {
        send \"Host密码\\r\"
    }
    \"password:\" {
        send \"Host密码\\r\"
    }
}

# 传输管控面用户态日志
expect \"#\" {
    send \"scp -P 22 /opt/vrpv8/var/log/cpdt_journal* root@141.61.48.xx:/home/cpu1213log/\\r\"
}
expect {
    \"Password:\" {
        send \"Host密码\\r\"
    }
    \"password:\" {
        send \"Host密码\\r\"
    }
}

# 传输管控面内核态日志
expect \"#\" {
    send \"cp /proc/kbox/regions/panic /home/\\r\"
    send \"scp -P 22 /home/panic root@141.61.48.xx:/home/cpu1213log/\\r\"
}
expect {
    \"Password:\" {
        send \"Host密码\\r\"
    }
    \"password:\" {
        send \"Host密码\\r\"
    }
}

# 传输vbussw_self.log
expect \"#\" {
    send \"scp -P 22 /opt/vrpv8/home/vbussw_self.log root@141.61.48.xx:/home/cpu1213log/\\r\"
}
expect {
    \"Password:\" {
        send \"Host密码\\r\"
    }
    \"password:\" {
        send \"Host密码\\r\"
    }
}

expect \"#\" { send \"exit\\r\" }
expect -re \"diagnose\\]\" { send \"quit\\r\" }
expect -re \"\\[.*\\]\" { send \"quit\\r\" }
expect -re \"<\[^>\]+>\" { 
    send \"quit\\r\"
    send \"\\x1d\"
}
expect eof
"


echo "所有日志收集完成！"
echo "文件保存在: $DEST_DIR"
tar -czf debug_log.tar.gz $DEST_DIR
ls -la "$DEST_DIR"
