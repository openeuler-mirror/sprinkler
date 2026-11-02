#!/usr/local/sbin/expect -f

set root_pass [lindex $argv 8]
set npu1 [lindex $argv 0]
set npu2 [lindex $argv 1]
set npu3 [lindex $argv 2]
set npu4 [lindex $argv 3]
set npu5 [lindex $argv 4]
set npu6 [lindex $argv 5]
set npu7 [lindex $argv 6]
set npu8 [lindex $argv 7]

set DEVMEM_LIST_str [lindex $argv 9]
# 使用相同的分隔符分割字符串
set delimiter "\u0001"
set DEVMEM_LIST [split $DEVMEM_LIST_str $delimiter]
# 测试输出
puts "数组元素数量: [llength $DEVMEM_LIST]"
for {set i 0} {$i < [llength $DEVMEM_LIST]} {incr i} {
    puts "元素 $i: [lindex $DEVMEM_LIST $i]"
}

set devmem_n [llength $DEVMEM_LIST]

# 检查数量 3/6，否则回落到默认
if { !($devmem_n == 3 || $devmem_n == 6) } {
  puts "传入 DEVMEM_LIST 元素数量=$devmem_n（期望 3 或 6），启用默认初始值回落"

  set DEVMEM_LIST {
    "devmem 0x248040640b4 32 0x60003"
    "devmem 0x248040640cc 32 0x60003"
    "devmem 0x248040640d4 32 0x60003"
    "devmem 0x2c8040640b4 32 0x60003"
    "devmem 0x2c8040640cc 32 0x60003"
    "devmem 0x2c8040640d4 32 0x60003"
  }
  set devmem_n [llength $DEVMEM_LIST]

  if { !($devmem_n == 3 || $devmem_n == 6) } {
    puts "默认初始值元素数量=$devmem_n 仍不为 3/6，为安全起见置空（不执行 devmem）"
    set DEVMEM_LIST {}
    set devmem_n 0
  } else {
    puts "已回落到默认初始值，元素数量=$devmem_n"
  }
} else {
  puts "传入 DEVMEM_LIST 校验通过，元素数量=$devmem_n"
}

for {set i 0} {$i < [llength $DEVMEM_LIST]} {incr i} {
    puts "元素 $i: [lindex $DEVMEM_LIST $i]"
}

set hosts "root@$npu1 root@$npu2 root@$npu3 root@$npu4 root@$npu5 root@$npu6 root@$npu7 root@$npu8"

set timeout 5

# 前4台配置
set original_check_addrs {
    "0x248052c0444" "0x248052c8444" "0x248072c0444" "0x248072c8444"
    "0x248092c0444" "0x248092c8444" "0x2480b2c0444" "0x2480b2c8444" "0x2480d2c0444"
}
set original_config_cmds {
    "devmem 0x24805280004 32 0" "devmem 0x24805280804 32 0" "devmem 0x24807280004 32 0"
    "devmem 0x24807280804 32 0" "devmem 0x24809280004 32 0" "devmem 0x24809280804 32 0"
    "devmem 0x2480B280004 32 0" "devmem 0x2480B280804 32 0" "devmem 0x2480D280004 32 0"
}

# 后4台配置
set new_check_addrs {
    "0x2c8052c0444" "0x2c8052c8444" "0x2c8072c0444" "0x2c8072c8444"
    "0x2c8092c0444" "0x2c8092c8444" "0x2c80b2c0444" "0x2c80b2c8444" "0x2c80d2c0444"
}
set new_config_cmds {
    "devmem 0x2C805280004 32 0" "devmem 0x2C805280804 32 0" "devmem 0x2C807280004 32 0"
    "devmem 0x2C807280804 32 0" "devmem 0x2C809280004 32 0" "devmem 0x2C809280804 32 0"
    "devmem 0x2C80B280004 32 0" "devmem 0x2C80B280804 32 0" "devmem 0x2C80D280004 32 0"
}

foreach host $hosts {
    puts "\n====================================="
    puts "开始处理主机：$host"
    puts "=====================================\n"

    spawn ssh -t $host
    expect {
        "assword:" { puts "ERROR: SSH免密失败！"; exit 1 }
        -re {Last login:.*} { puts "SSH登录成功，准备切换root..." }
        timeout { puts "ERROR: 连接超时！"; exit 1 }
    }
    
    puts "已切换到root权限，开始执行操作..."

    # 1. 查询换回寄存器
    puts "\n----- 执行换回寄存器查询 -----"
    send "devmem 0x248020a041c 32\n"
    expect -re "devmem 0x248020a041c 32\r\n"
    expect -re {0x([0-9A-Fa-f]+)\r\n}
    set reg1 $expect_out(1,string)
    expect "#"
    puts "寄存器0x248020a041c实际值：0x$reg1"

    send "devmem 0x2c8020a041c 32\n"
    expect -re "devmem 0x2c8020a041c 32\r\n"
    expect -re {0x([0-9A-Fa-f]+)\r\n}
    set reg2 $expect_out(1,string)
    expect "#"
    puts "寄存器0x2c8020a041c实际值：0x$reg2"


    # 2. 关掉UDie0/UDie01 qos（david）
    puts "\n----- closed qos config-----"
    send "devmem 0x24804099108 32 0x1\n"
    expect "#"
    send "devmem 0x248040a1084 32 0x0\n"
    expect "#"
    send "devmem 0x2c804099108 32 0x1\n"
    expect "#"
    send "devmem 0x2c8040a1084 32 0x0\n"
    expect "#"	
	
	
    # 4. 检查寄存器后四位（跳过第2、3个）
    puts "\n----- 检查寄存器后四位是否为0x4047（跳过第2、3个） -----"
    if {[string match root@$npu5 $host] || [string match root@$npu6 $host] || [string match root@$npu7 $host] || [string match root@$npu8 $host]} {
        puts "使用新地址配置（后4台主机）"
        set check_addrs $new_check_addrs
        set config_cmds $new_config_cmds
		
		
		puts "\n----- 执行性能规避配置 后4台主机 -----"
        for {set i 0} {$i < [llength $DEVMEM_LIST]} {incr i} {
            set cmd [lindex $DEVMEM_LIST $i]
            
            # 发送命令
            puts "执行元素 $i: $cmd"
            send "$cmd\n"
            expect "#"
        }
    } else {
        puts "使用原始地址配置（前4台主机）"
        set check_addrs $original_check_addrs
        set config_cmds $original_config_cmds
		
		puts "\n----- 执行性能规避配置 前4台主机 -----"
        for {set i 0} {$i < [llength $DEVMEM_LIST]} {incr i} {
            set cmd [lindex $DEVMEM_LIST $i]
            
            # 发送命令
            puts "执行元素 $i: $cmd"
            send "$cmd\n"
            expect "#"
        }
    }

    # 遍历查询并处理（核心：跳过索引1和2，即第2、3个寄存器）
    for {set i 0} {$i < [llength $check_addrs]} {incr i} {
        set addr [lindex $check_addrs $i]
        set cmd [lindex $config_cmds $i]

        # 判断是否为第2个（i=1）或第3个（i=2）寄存器，若是则跳过校验
        if {$i == 1 || $i == 2} {
            puts "\n----- 跳过第 [expr {$i+1}] 个寄存器（$addr）的校验 -----"
            # 执行查询但不校验后四位
            send "devmem $addr\n"
            expect -re "devmem $addr\r\n"
            expect -re {0x([0-9A-Fa-f]+)\r\n}
            set result $expect_out(1,string)
            expect "#"
            puts "第 [expr {$i+1}] 个寄存器（$addr）查询值：0x$result（不校验后四位）"
            # 注释移到上一行，确保continue单独成行
            continue
        }

        # 非跳过的寄存器，正常执行校验
        puts "\n发送查询命令：devmem $addr（第 [expr {$i+1}] 个，正常校验）"
        send "devmem $addr\n"
        expect -re "devmem $addr\r\n"
        set result ""
        expect {
            -re {0x([0-9A-Fa-f]+)\r\n} {
                set result $expect_out(1,string)
                puts "成功捕获实际回显值：0x$result"
            }
            timeout { puts "ERROR: 查询$addr超时"; }
            default { puts "ERROR: 查询$addr格式异常"; }
        }
        expect "#"

        if {$result ne ""} {
            set result_len [string length $result]
            set last4 [string toupper [string range $result [expr {$result_len - 4}] end]]
            puts "寄存器地址：$addr"
            puts "实际回显值：0x$result"
            puts "实际回显后四位：0x$last4"
        } else {
            puts "ERROR: 未获取到$addr有效回显，跳过检查"
            continue
        }

        # 配置与重试逻辑
        if {$last4 ne "4047"} {
            puts "警告：后四位不是0x4047，执行配置命令：$cmd"
            send "$cmd\n"
            expect "#"

            set max_retry 3
            set retry_count 0
            set success 0
            while {$retry_count < $max_retry} {
                incr retry_count
                puts "\n第 $retry_count 次重试查询 $addr ..."
                send "devmem $addr\n"
                expect -re "devmem $addr\r\n"
                set retry_result ""
                expect {
                    -re {0x([0-9A-Fa-f]+)\r\n} { set retry_result $expect_out(1,string) }
                    timeout { puts "WARNING: 第$retry_count次超时"; }
                    default { puts "WARNING: 第$retry_count次格式异常"; }
                }
                expect "#"

                if {$retry_result ne ""} {
                    set r_len [string length $retry_result]
                    set r_last4 [string toupper [string range $retry_result [expr {$r_len - 4}] end]]
                    puts "第$retry_count次回显值：0x$retry_result，后四位：0x$r_last4"
                } else {
                    puts "ERROR: 第$retry_count次未获取有效回显"
                    continue
                }

                if {$r_last4 eq "4047"} {
                    puts "第$retry_count次符合预期，停止重试"
                    set success 1
                    break
                } elseif {$retry_count == $max_retry} {
                    puts "已达最大重试次数，仍未符合预期"
                }
            }
            if {!$success} {
                puts "警告：$addr 配置后仍未达到0x4047，请手动检查"
            }
        } else {
            puts "后四位符合预期（0x4047），无需配置"
        }
    }

    # 修复核心：简化退出逻辑（直接root登录，一次exit即可断开连接）
    puts "\n----- 开始退出主机 $host -----"
    send "exit\n"
    # 匹配连接关闭的状态（logout/Connection closed），避免超时
    expect {
        -re {logout|Connection closed} { puts "成功退出主机 $host"; }
        timeout { puts "退出$host超时，强制关闭连接"; }
    }
	# 等待spawn进程彻底结束
    expect eof

    puts "\n====================================="
    puts "主机$host处理完成"
    puts "=====================================\n"
}

puts "所有主机操作执行完毕！"
exit 0

