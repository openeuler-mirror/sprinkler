echo "[Info][Network] Vertify TLS Status..."
npu_num=$(lspci | grep d80 | wc -l)
tlsstatus=$(for i in $(seq 0 $(($npu_num-1))); do hccn_tool -i $i -tls -g ; done | grep switch | awk -F '[()]' '{print $(1)}' | awk '{print $(3)}')
while IFS= read -r line; do
    echo "[Info][Network] TLS status is ${line}"
    if [ "$line" = "switch[0]" ]; then
        status="disable"
    else
        status="enable"
    fi
done <<< "$tlsstatus"

if [ "$status" = "disable" ]; then
    echo -e "[\033[32mPASS\033[0m][Network] TLS status is disable."
else
    echo -e "[\033[31mERROR\033[0m][Network] TLS status is enable!"
    echo "Please use the following command to turn off TLS:"
    echo "for i in {0..$(($npu_num-1))}; do hccn_tool -i $i -tls -s enable 0; done"
fi