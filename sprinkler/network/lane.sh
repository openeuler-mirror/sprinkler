# /bin/bash
# 配置参数
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "${PARENT_DIR}/config_util.sh"

# 配置参数
config_require_keys \
    "POD_NPU_USER|请输入NPU连接用户名|root|0|validate_non_empty" \
    "POD_NPU_PASSWORD|请输入NPU登录密码(输入不回显)||1|validate_non_empty" \
    "POD_NPU_HW_USER|请输入NPU连接Hw用户名|HwHiAiUser|0|validate_non_empty" \
    "POD_NPU_HW_PASSWORD|请输入NPU Hw登录密码(输入不回显)||1|validate_non_empty"

USER=$POD_NPU_USER
PASSWORD=$POD_NPU_PASSWORD
HW_USER=$POD_NPU_HW_USER
HW_PASSWORD=$POD_NPU_HW_PASSWORD
ip_index=($(bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/npuip.sh"))
chmod +x $(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/autoroot.sh
for ((i=0; i<=7; i++)); do
    "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/autoroot.sh" ${ip_index[$i]} $POD_NPU_PASSWORD $HW_USER $HW_PASSWORD
done
bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/no_password.sh" 2>&1 | grep -E --line-buffered "ERROR"
echo "若登录失败，检查是否已存在免密key，根据提示删除后再次执行"
ip_str=""
for item in "${ip_index[@]}"; do
    if [ -z "$ip_str" ]; then
        ip_str="$item"
    else
        ip_str="$ip_str $item" # 这里使用逗号作为分隔符，可以替换为任意字符
    fi
done

cmds_out="$(bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/gen_udma_fix_cmds.sh" 2>&1)"  # 只要0号卡就加 --first-only

# 取第一条 ssh 行（可能是：ssh 1.2.3.4 或 ssh user@1.2.3.4 或 ssh -p 22 user@1.2.3.4）
SSH_LINE="$(printf '%s\n' "$cmds_out" | awk '/^ssh[[:space:]]/ {print; exit}')"
SSH_IP="$(printf '%s\n' "$SSH_LINE" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -n1 || true)"

# 从第一条 ssh 行之后开始，提取所有 devmem 行
DEVMEM_LIST=()
mapfile -t DEVMEM_LIST < <(
  printf '%s\n' "$cmds_out" | awk '
    BEGIN{started=0}
    /^ssh[[:space:]]/ {started=1; next}
    started && /^devmem[[:space:]]+0x[0-9a-fA-F]+/ {print}
  ' | tail -n 6
)

echo "ssh $SSH_IP"
printf '%s\n' "${DEVMEM_LIST[@]}"
# 将数组转换为分隔符分隔的字符串（使用不会出现在元素中的分隔符）
IFS=$'\001'  # 使用ASCII码001作为分隔符（几乎不会出现在普通文本中）
DEVMEM_LIST_str="${DEVMEM_LIST[*]}"
unset IFS

chmod +x $(dirname "$(realpath "${BASH_SOURCE[0]}")")/full_lane.sh
for ((i=1; i<=8; i++)); do
    $(dirname "$(realpath "${BASH_SOURCE[0]}")")/full_lane.sh ${ip_index[0]} ${ip_index[1]} ${ip_index[2]} ${ip_index[3]} ${ip_index[4]} ${ip_index[5]} ${ip_index[6]} ${ip_index[7]} $PASSWORD "$DEVMEM_LIST_str"
done
