#!/bin/bash
# 定义文件路径
ROUTE_FILE="/lib/route.conf"
if [ -z "$ROUTE_FILE" ]; then
    echo "$ROUTE_FILE 不存在！"
    exit 1
fi
cat $ROUTE_FILE | grep remote | awk '{
     len=length($0)
     if(len < 8) next
     s=substr($0, len-7, 8)
     part1=substr(s,1,2)
     part2=substr(s,3,2)
     part3=substr(s,5,2)
     part4=substr(s,7,2)
     
     # 将16进制转为10进制进行比较
     cmd="printf \"%d\" 0x" part2
     cmd | getline part2_dec
     close(cmd)
     
     if (part2_dec == 223) {
         # 223.223.xx.xx格式，输出: 223.223.part3.part4
         cmd="printf \"223.223.%d.%d\\n\" 0x" part3 " 0x" part4
         system(cmd)
     } else {
         # 其他格式，输出: 223.part1.part2.part3
         cmd="printf \"223.%d.%d.%d\\n\" 0x" part1 " 0x" part2 " 0x" part3
         system(cmd)
     }
 }'
