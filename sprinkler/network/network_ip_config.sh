echo "[Info][Network] Vertify Network IP Config..."
npu_num=$(lspci | grep d80 | wc -l)
address=$(cat /etc/hccn.conf | grep "address") 
netmask=$(cat /etc/hccn.conf | grep "netmask") 
echo -e "Address:\n${address}"
echo -e "Netmask:\n${netmask}"

link_up_num=$(for i in $(seq 0 $(($npu_num-1))); do hccn_tool -i $i -link -g ; done | grep 'UP' | wc -l)
if [ "$link_up_num" = $npu_num ]; then
    echo -e "[\033[32mPASS\033[0m][Network] All links are UP."
else
    echo -e "[\033[31mERROR\033[0m][Network] Some links are down!"
fi