#!/usr/bin/env bash
# 保存原始参数（给 exec 重启用）
ORIG_ARGS=("$@")
CHECK_ID="update_code"

# ---- bootstrap：先把自己复制到 /tmp 执行，避免更新本仓库时路径变化影响运行 ----
if [ "${__BOOTSTRAPPED:-0}" != "1" ]; then
  export __BOOTSTRAPPED=1
  export ORIGINAL_SCRIPT_PATH="$(readlink -f "$0")"
  tmp_directory="/home/tmp"
  _tmp="$tmp_directory/$(basename "$0").$$"
  # 检查目录是否存在
  if [ ! -d "$tmp_directory" ]; then
    echo "目录不存在，正在创建: $tmp_directory"
    mkdir -p "$tmp_directory"
  fi
  cp -f "$ORIGINAL_SCRIPT_PATH" "$_tmp"
  chmod +x "$_tmp"
  exec "$_tmp" "${ORIG_ARGS[@]}"
fi

# -------- time utils --------
_now() { date "+%F %T"; }

# -------- color (optional) --------
_color_ok=""; _color_warn=""; _color_fail=""; _color_info=""; _color_off=""; _color_debug=""
_color_ok=$'\033[32m'    # 绿色
_color_warn=$'\033[33m'  # 黄色
_color_fail=$'\033[31m'  # 红色
_color_info=$'\033[36m'  # 青色
_color_debug=$'\033[34m' # 蓝色
_color_off=$'\033[0m'    # 重置

# -------- logging --------
# usage: log LEVEL CHECK_ID "message"
log() {
  local level="$1"; shift
  local check_id="$1"; shift
  local msg="$*"

  local color="$_color_off"
  case "$level" in
    OK)   color="$_color_ok" ;;
    WARN) color="$_color_warn" ;;
    FAIL) color="$_color_fail" ;;
    INFO) color="$_color_info" ;;
    DEBUG) color="$_color_debug" ;;
  esac

  printf "%s | %b%-5s%b | %s | %s\n" "$(_now)" "$color" "$level" "$_color_off" "$check_id" "$msg"
}


die() { log FAIL "$CHECK_ID" "FATAL $*"; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"
}

is_valid_ipv4() {
    local ip=$1
    local stat=1
    
    # 使用正则表达式检查IPv4格式
    if [[ $ip =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
        # 检查每个八位字节是否在0-255范围内
        IFS='.' read -r -a octets <<< "$ip"
        for octet in "${octets[@]}"; do
            if [[ $octet -lt 0 || $octet -gt 255 ]]; then
                return 1
            fi
        done
        stat=0
    fi
    return $stat
}

# -------- defaults --------
sprinkler_path=$(sprinkler | sed -n 's/.*Sprinkler Path is //p')
WORKDIR="$(dirname "$(dirname "$sprinkler_path")")" #本地工作目录（在此目录下操作：旧目录改名、clone、新目录生成）
REPO_URL="https://szv-open.codehub.huawei.com/innersource/LingquInfra_G/Sprinkler.git" #git 仓库 URL（https）
YAML_FILE="sprinkler/cluster_controller/inventory.yaml" # 集群 yaml（包含 hosts 列表：host/cpu_user/cpu_pass）
PROJECT="Sprinkler" #项目目录名（默认从 repo.git 推断）
CONFIG_NAME="sprinkler/env/config.conf" #配置文件名（可选；默认 config.conf）
CLONE_DEPTH="1" # git clone depth（可选；默认 1）
RETRIES="3" #rsync/scp 重试次数（可选；默认 3）
REMOTE_ENABLED=1 #开启远端同步（会先检查 yaml hosts；没有则跳过远端）
NO_SELF_UPDATE=1 #内部参数：禁止自更新递归（无需手动使用）

HOST_IP="${HOST_IP:-}"
PROXY_IP="${PROXY_IP:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host-ip) HOST_IP="${2:-}"; shift 2 ;;
    --proxy-ip) PROXY_IP="${2:-}"; shift 2 ;;
    *) log FAIL "$CHECK_ID" "unknown arg: $1"; usage ;;
  esac
done

[[ -n "$HOST_IP" ]] || { log FAIL "$CHECK_ID" "-host-ip required"; exit 1; }
[[ -n "$PROXY_IP" ]] || { log FAIL "$CHECK_ID" "-proxy-ip required"; exit 1; }

# 检查IP格式是否正确
if ! is_valid_ipv4 "$HOST_IP"; then
    log FAIL "$CHECK_ID" "Invalid HOST_IP format: $HOST_IP. Please provide a valid IPv4 address."
    exit 1
fi

if ! is_valid_ipv4 "$PROXY_IP"; then
    log FAIL "$CHECK_ID" "Invalid PROXY_IP format: $PROXY_IP. Please provide a valid IPv4 address."
    exit 1
fi

hosts_entries="$HOST_IP szv-open.codehub.huawei.com"
# 引入公共配置库
. "${sprinkler_path}/env/config_util.sh"
# 配置参数
config_require_keys \
    "CONDA_ENV|请输入conda虚拟环境名: ||0|validate_non_empty" \
    "BRANCH|请输入Sprinkler代码分支: |master|0|validate_non_empty" \
    "REMOTE_TARGET|本机或者远端机器的代码父路径: |/home/healthy_check/code|0|validate_non_empty"

HTTP_PROXY=http://p_atlas:proxy%40123@$PROXY_IP:8080
HTTPS_PROXY=http://p_atlas:proxy%40123@$PROXY_IP:8080
NO_PROXY=127.0.0.1,.huawei.com,localhost,local,.local
export http_proxy=$HTTP_PROXY
export https_proxy=$HTTPS_PROXY
export no_proxy=$NO_PROXY
    
# --- archive fallback (NEW) ---
ARCHIVE_FORMAT="tar.gz"        # zip / tar.gz / tar.bz2 / tar
ARCHIVE_URL="https://szv-open.codehub.huawei.com/api/v4/projects/1494627/repository/archive?sha=$BRANCH&archive_format=$ARCHIVE_FORMAT"


[ -n "$WORKDIR" ] || die "缺少 本地工作目录"
[ -n "$REPO_URL" ] || die "缺少 仓库 URL"
[ -n "$YAML_FILE" ] || die "缺少 集群 yaml"
[ -n "$CONDA_ENV" ] || die "缺少 conda env 名称"

need_cmd git
need_cmd ssh
need_cmd scp
need_cmd bash
need_cmd tar


# 项目名默认从 repo url 推断
if [ -z "$PROJECT" ]; then
  base="$(basename "$REPO_URL")"
  PROJECT="${base%.git}"
fi

# 关键目录
LOCAL_PROJECT_DIR="$(dirname "$sprinkler_path")"

# yaml 相对路径：强烈建议用户传“仓库内相对路径”
# - 如果用户传的是相对路径：YAML_REL=该相对路径
# - 如果用户传绝对路径且在旧仓库内：推断相对路径
YAML_REL=""
if [[ "$YAML_FILE" != /* ]]; then
  YAML_REL="$YAML_FILE"
else
  # 绝对路径：如果在当前旧仓库目录内，推断相对路径（旧仓库可能存在）
  if [ -d "$LOCAL_PROJECT_DIR" ]; then
    yaml_abs="$(readlink -f "$YAML_FILE" 2>/dev/null || true)"
    if [ -n "${yaml_abs:-}" ] && [[ "$yaml_abs" == "$LOCAL_PROJECT_DIR/"* ]]; then
      YAML_REL="${yaml_abs#"$LOCAL_PROJECT_DIR"/}"
    fi
  fi
  # 如果推断不到，后续仍可用 YAML_FILE 解析，但“回填到新仓库原路径”无法保证
fi

# 时间/日志：支持继承（给 self-update exec 重启用）
DATE_TAG="${DEPLOY_DATE_TAG:-$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="${DEPLOY_LOG_DIR:-${WORKDIR%/}/sprinkler_logs/update_code_logs/${PROJECT}_${DATE_TAG}}"
mkdir -p "$LOG_DIR"
MAIN_LOG="$LOG_DIR/main.log"
: > "$MAIN_LOG"

STATE_DIR="$LOG_DIR/state"
mkdir -p "$STATE_DIR"

LOCAL_BACKUP_DIR="${LOCAL_PROJECT_DIR}_${DATE_TAG}"
# 生成 “latest” 软链（不打屏也能定位日志）
mkdir -p "${WORKDIR%/}/sprinkler_logs/update_code_logs"
ln -sfn "$LOG_DIR" "${WORKDIR%/}/sprinkler_logs/update_code_logs/latest_${PROJECT}" 2>/dev/null || true


log INFO "$CHECK_ID" "INIT 项目名：$PROJECT"
log INFO "$CHECK_ID" "INIT 日期标记：$DATE_TAG"
log INFO "$CHECK_ID" "INIT 本地工作目录：$WORKDIR"
log INFO "$CHECK_ID" "INIT 日志目录：$LOG_DIR"
log INFO "$CHECK_ID" "INIT 远端同步：$REMOTE_ENABLED"
log INFO "$CHECK_ID" "INIT config文件：$CONFIG_NAME"
log INFO "$CHECK_ID" "INIT yaml 输入：$YAML_FILE"
log INFO "$CHECK_ID" "INIT yaml 相对路径（用于回填）：${YAML_REL:-<无法推断>}"
log INFO "$CHECK_ID" "INIT self_updated_flag=${DEPLOY_SELF_UPDATED:-0}, no_self_update=$NO_SELF_UPDATE"

apply_one_hosts() {
  local ip="$1" host="$2"
  [ -n "$ip" ] && [ -n "$host" ] || return 0

  local tmp
  tmp="$(mktemp)"

  # 去掉旧的同名 host 行（保留注释行）
  awk -v H="$host" '
    BEGIN{skip=0}
    /^[[:space:]]*#/ {print; next}
    {
      for(i=2;i<=NF;i++){
        if($i==H){ next }
      }
      print
    }
  ' /etc/hosts > "$tmp"

  printf "%s\t%s\n" "$ip" "$host" >> "$tmp"

  # 写回 /etc/hosts（优先直接写；不行则尝试 sudo；都不行就报错退出）
  if [ -w /etc/hosts ]; then
    cat "$tmp" > /etc/hosts
  elif command -v sudo >/dev/null 2>&1; then
    sh -c "cat '$tmp' > /etc/hosts"
  else
    log FAIL "$CHECK_ID" "ERROR: cannot write /etc/hosts (not writable and no sudo)"
    rm -f "$tmp"
    exit 11
  fi

  rm -f "$tmp"
}

apply_hosts_entries() {
  local s="$1"
  [ -n "$s" ] || return 0

  IFS=';' read -r -a arr <<<"$s"
  local item ip host
  for item in "${arr[@]}"; do
    # item 形如： "1.2.3.4 www.xxx.com"
    ip="$(echo "$item" | awk '{print $1}')"
    host="$(echo "$item" | awk '{print $2}')"
    apply_one_hosts "$ip" "$host"
    log INFO "$CHECK_ID" "/etc/hosts 添加 $ip $host"
  done
}

# ---- 在任何网络访问/安装前先写 hosts ----
apply_hosts_entries "$hosts_entries"

restore_backup() {
    if [ -d "$LOCAL_BACKUP_DIR" ]; then
        rm -rf "$LOCAL_PROJECT_DIR" >/dev/null 2>&1 || true
        mv "$LOCAL_BACKUP_DIR" "$LOCAL_PROJECT_DIR"
        log INFO "$CHECK_ID" "LOCAL 本机旧目录已还原：$LOCAL_PROJECT_DIR"
    else
        if [ ! -d "$LOCAL_PROJECT_DIR" ]; then
            log FAIL "$CHECK_ID" "LOCAL 本机目录未找到：$LOCAL_PROJECT_DIR"
        fi
    fi
}
# -------- local command runner (logs to main.log) --------
run_local_or_die() {
  local check="$1"; shift
  local cmd="$*"
  log INFO "$CHECK_ID" "$check 执行: pip install -e ."
  # 关键：显式检查返回码，失败则 FAIL 并退出（防止误报 OK）
  bash -lc "$cmd" >> "$MAIN_LOG" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    log FAIL "$CHECK_ID" "$check 命令失败：rc=$rc（详见日志：$MAIN_LOG）"
    restore_backup
    exit "$rc"
  fi
}

proxy_curl_check() {
  # $1: check_id
  # 依赖：已在当前 shell 中 export http_proxy/https_proxy（可为空）
  local check="$1"

  # 不要走代理也行，只有当设置了任意代理时才检查
  if [ -z "${http_proxy:-}" ] && [ -z "${https_proxy:-}" ] && [ -z "${HTTP_PROXY:-}" ] && [ -z "${HTTPS_PROXY:-}" ]; then
    return 0
  fi

  # 关键：curl 失败要返回非0，让上层捕获并 FAIL
  # -I：只取响应头；--max-time：避免卡死；--connect-timeout：连接超时
  curl -fsSIL --connect-timeout 5 --max-time 12 https://www.baidu.com >/dev/null 2>&1 \
    || curl -fsSIL --connect-timeout 5 --max-time 12 http://www.baidu.com >/dev/null 2>&1 \
    || { echo "ERROR: proxy curl check failed (baidu unreachable)"; return 10; }
  return 0
}

# -------- parse yaml hosts (simple parser for your format) --------
# 输出：host<TAB>user<TAB>pass
parse_hosts() {
  local f="$1"
  [ -f "$f" ] || return 0

  awk '
  function trim(s){ sub(/^[ \t\r\n]+/, "", s); sub(/[ \t\r\n]+$/, "", s); return s }
  BEGIN{ host=""; user=""; pass=""; in_hosts=0 }
  /^[[:space:]]*hosts[[:space:]]*:/ { in_hosts=1; next }
  in_hosts==0 { next }

  # -host: xxx 或 - host: xxx
  /^[[:space:]]*-[[:space:]]*host[[:space:]]*:/ {
    line=$0
    sub(/^[[:space:]]*-[[:space:]]*host[[:space:]]*:[[:space:]]*/, "", line)
    host=trim(line); gsub(/^["'\''"]|["'\''"]$/, "", host)
    next
  }

  /^[[:space:]]*cpu_user[[:space:]]*:/ {
    line=$0
    sub(/^[[:space:]]*cpu_user[[:space:]]*:[[:space:]]*/, "", line)
    user=trim(line); gsub(/^["'\''"]|["'\''"]$/, "", user)
    next
  }

  /^[[:space:]]*cpu_pass[[:space:]]*:/ {
    line=$0
    sub(/^[[:space:]]*cpu_pass[[:space:]]*:[[:space:]]*/, "", line)
    pass=trim(line); gsub(/^["'\''"]|["'\''"]$/, "", pass)

    if(host!="" && user!=""){
      printf "%s\t%s\t%s\n", host, user, pass
    }
    host=""; user=""; pass=""
    next
  }
  ' "$f"
}

is_placeholder_pass() {
  # 0=是占位密码；1=不是
  local p="${1:-}"
  p="${p//[[:space:]]/}"
  p="${p#\"}"; p="${p%\"}"
  p="${p#\'}"; p="${p%\'}"

  case "$p" in
    xxxx|xxx|XX|XXX|XXXX|password|Password|PASSWORD|changeme|change_me|ChangeMe|123456|000000)
      return 0
      ;;
  esac
  # xxxxx... 这种也算
  [[ "$p" =~ ^[xX]{3,}$ ]] && return 0
  return 1
}

is_example_entry() {
  # 输入：host user pass
  # 返回 0 = 示例/占位（应跳过）；返回 1 = 真实（应部署）
  local h="${1:-}" u="${2:-}" p="${3:-}"

  # 去空格/引号
  h="${h//[[:space:]]/}"; h="${h#\"}"; h="${h%\"}"; h="${h#\'}"; h="${h%\'}"
  u="${u//[[:space:]]/}"; u="${u#\"}"; u="${u%\"}"; u="${u#\'}"; u="${u%\'}"
  p="${p//[[:space:]]/}"

  # 1) host 明显占位
  [[ -z "$h" ]] && return 0
  [[ "$h" == "xx.xx.xx.xxx" ]] && return 0
  [[ "$h" =~ ^([xX]{1,3}\.){3}[xX]{1,3}$ ]] && return 0

  # 2) 常用的“示例 IP”
  [[ "$h" == "198.11.10.1" ]] && return 0
  [[ "$h" == "141.61.48.182" ]] && return 0

  # 3) 密码是占位符（最关键：cpu_pass: xxxx）
  if is_placeholder_pass "$p"; then
    return 0
  fi

  # 4) 其它明显占位
  [[ "$h" == "0.0.0.0" || "$h" == "127.0.0.1" || "$h" == "localhost" ]] && return 0

  return 1
}

filter_real_hosts() {
  # 输入：多行（host\tuser\tpass）
  # 输出：过滤后的真实 host 行（同格式）
  local lines="${1:-}"
  local out=""
  local skipped=0
  while IFS=$'\t' read -r h u p; do
    [ -n "${h:-}" ] || continue
    if is_example_entry "$h" "$u" "$p"; then
      skipped=$((skipped+1))
      # log WARN "$CHECK_ID" "REMOTE 跳过示例/占位 host：host=$h user=${u:-} pass=<masked>"
      continue
    fi
    out+="${h}"$'\t'"${u}"$'\t'"${p}"$'\n'
  done <<<"$lines"

  export REMOTE_SKIPPED_EXAMPLE_COUNT="$skipped"
  printf "%s" "$out"
}
# =========================
# git clone -> (fail) curl archive download + extract
# =========================
validate_repo_dir_or_die() {
  local check="$1" dir="$2"
  if [ ! -d "$dir" ]; then
    log FAIL "$CHECK_ID" "$check 校验失败：目录不存在：$dir"
    restore_backup
    exit 1
  fi
  if [ -z "$(ls -A "$dir" 2>/dev/null || true)" ]; then
    log FAIL "$CHECK_ID" "$check 校验失败：目录为空：$dir"
    restore_backup
    exit 1
  fi
  # 至少应满足其一：有 .git / setup.py / pyproject.toml
  if [ ! -d "$dir/.git" ] && [ ! -f "$dir/setup.py" ] && [ ! -f "$dir/pyproject.toml" ]; then
    log FAIL "$CHECK_ID" "$check 校验失败：既无 .git 也无 setup.py/pyproject.toml，怀疑下载/解压到的不是仓库：$dir"
    restore_backup
    exit 1
  fi
}

validate_archive_or_die() {
  local check="$1" pkg="$2" fmt="$3"
  if [ ! -s "$pkg" ]; then
    log FAIL "$CHECK_ID" "$check 压缩包为空或不存在：$pkg"
    exit 1
  fi
  case "$fmt" in
    tar.gz)
      if ! tar -tzf "$pkg" >/dev/null 2>&1; then
        log FAIL "$CHECK_ID" "$check 压缩包校验失败（不是合法 tar.gz 或被登录页替代，请删除压缩包并重试）：$pkg"
        restore_backup
        exit 1
      fi
      ;;
    tar.bz2)
      if ! tar -tjf "$pkg" >/dev/null 2>&1; then
        log FAIL "$CHECK_ID" "$check 压缩包校验失败（不是合法 tar.bz2 或被登录页替代，请删除压缩包并重试）：$pkg"
        restore_backup
        exit 1
      fi
      ;;
    tar)
      if ! tar -tf "$pkg" >/dev/null 2>&1; then
        log FAIL "$CHECK_ID" "$check 压缩包校验失败（不是合法 tar 或被登录页替代，请删除压缩包并重试）：$pkg"
        restore_backup
        exit 1
      fi
      ;;
    zip)
      need_cmd unzip
      if ! unzip -tq "$pkg" >/dev/null 2>&1; then
        log FAIL "$CHECK_ID" "$check 压缩包校验失败（不是合法 zip 或被登录页替代，请删除压缩包并重试）：$pkg"
        restore_backup
        exit 1
      fi
      ;;
    *)
      log FAIL "$CHECK_ID" "$check 不支持的 archive_format：$fmt（仅支持 zip/tar.gz/tar.bz2/tar）"
      restore_backup
      exit 1
      ;;
  esac
}

extract_archive_to_dir_or_die() {
  local check="$1" pkg="$2" fmt="$3" outdir="$4"
  local tmp_unpack="$STATE_DIR/unpack.$$"
  rm -rf "$tmp_unpack" >/dev/null 2>&1 || true
  mkdir -p "$tmp_unpack"

  log INFO "$CHECK_ID" "$check 开始解压：$pkg -> $tmp_unpack"
  case "$fmt" in
    tar.gz) tar -xzvf "$pkg" -C "$tmp_unpack" >>"$MAIN_LOG" 2>&1 ;;
    tar.bz2) tar -xjf "$pkg" -C "$tmp_unpack" >>"$MAIN_LOG" 2>&1 ;;
    tar) tar -xf "$pkg" -C "$tmp_unpack" >>"$MAIN_LOG" 2>&1 ;;
    zip) unzip -q "$pkg" -d "$tmp_unpack" >>"$MAIN_LOG" 2>&1 ;;
  esac

  # 通常解压后只有一个顶层目录；如果不是，就把所有内容直接放到 outdir
  local top
  top="$(find "$tmp_unpack" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)"

  rm -rf "$outdir" >/dev/null 2>&1 || true
  if [ -n "$top" ] && [ "$(find "$tmp_unpack" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')" = "1" ]; then
    mv "$top" "$outdir"
  else
    mkdir -p "$outdir"
    # shellcheck disable=SC2086
    mv "$tmp_unpack"/* "$outdir"/ 2>/dev/null || true
  fi

  rm -rf "$tmp_unpack" >/dev/null 2>&1 || true
  log INFO "$CHECK_ID" "$check 解压完成：$outdir"
}

curl_download_archive_try() {
  local check="$1" url="$2" out="$3" fmt="$4"

  log INFO "$CHECK_ID" "$check curl 下载尝试：$url"
  rm -f "$out" >/dev/null 2>&1 || true

  # 代理
  local curl_env=""
  if [ -n "$HTTP_PROXY" ]; then curl_env+="export http_proxy='$HTTP_PROXY'; export HTTP_PROXY='$HTTP_PROXY';"; fi
  if [ -n "$HTTPS_PROXY" ]; then curl_env+="export https_proxy='$HTTPS_PROXY'; export HTTPS_PROXY='$HTTPS_PROXY';"; fi


  # Codehub v4 API：用 X-Auth-Token
  bash -lc "$curl_env curl -k --request GET \
    --header \"PRIVATE-TOKEN: 6KLhVizsmLDvH56aCUo6zEzN\" \
    --output '$out' '$url'" >>"$MAIN_LOG" 2>&1 || return 1

  validate_archive_or_die "$check" "$out" "$fmt"
  return 0
}

download_and_extract_fallback_or_die() {
  local check="$1" dst_dir="$2"

  local fmt="$ARCHIVE_FORMAT"
  local pkg="$STATE_DIR/Sprinkler.${fmt}"

  # 清理目标目录（避免残留）
  rm -rf "$dst_dir" >/dev/null 2>&1 || true

  local ok=0
#   while IFS= read -r u; do
#     [ -n "$u" ] || continue
#     if curl_download_archive_try "$CHECK_ID" "$check $u" "$pkg" "$fmt"; then
#       extract_archive_to_dir_or_die "$CHECK_ID" "$check $pkg" "$fmt" "$dst_dir"
#       ok=1
#       break
#     fi
#   done < <(build_archive_candidates "$fmt" "$REPO_URL" "$BRANCH")
  if curl_download_archive_try "$check" "$ARCHIVE_URL" "$pkg" "$fmt"; then
    extract_archive_to_dir_or_die "$check" "$pkg" "$fmt" "$dst_dir"
    ok=1
  fi

  if [ "$ok" -ne 1 ]; then
    log FAIL "$CHECK_ID" "$check 所有压缩包下载候选都失败（详见日志：$MAIN_LOG）"
    restore_backup
    exit 1
  fi

  validate_repo_dir_or_die "$check" "$dst_dir"
  log OK "$CHECK_ID" "$check 压缩包下载+解压成功：$dst_dir"
}

git_clone_try() {
  local check="$1" dst_dir="$2" askpass="$3"
  log INFO "$CHECK_ID" "$check 开始 git clone：$REPO_URL (branch=$BRANCH) -> $dst_dir"

  rm -rf "$dst_dir" >/dev/null 2>&1 || true
  if ! GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 \
      git clone -b "$BRANCH" --depth "$CLONE_DEPTH" "$REPO_URL" "$dst_dir" >> "$MAIN_LOG" 2>&1; then
    return 1
  fi
  return 0
}

fetch_repo_or_die() {
  # 统一入口：先 git clone，失败则 curl 压缩包下载解压
  local check="$1" dst_dir="$2"

  [ -n "${GIT_USER:-w30064656}" ] || die "请先 export GIT_USER=xxx"
  [ -n "${GIT_PASS:-6KLhVizsmLDvH56aCUo6zEzN}" ] || die "请先 export GIT_PASS=yyy"
  local askpass="$STATE_DIR/git_askpass.sh"
  cat >"$askpass" <<'SH'
#!/usr/bin/env sh
case "$1" in
  *Username*|*username*) echo "$GIT_USER" ;;
  *Password*|*password*) echo "$GIT_PASS" ;;
  *) echo "" ;;
esac
SH
  chmod 700 "$askpass"

  if git_clone_try "$check" "$dst_dir" "$askpass"; then
    rm -f "$askpass" >/dev/null 2>&1 || true
    validate_repo_dir_or_die "$check" "$dst_dir"
    log OK "$CHECK_ID" "$check git clone 成功：$dst_dir"
    return 0
  fi

  log WARN "$CHECK_ID" "$check git clone 失败，开始尝试 curl 下载压缩包 fallback（详见日志：$MAIN_LOG）"
  rm -f "$askpass" >/dev/null 2>&1 || true

  download_and_extract_fallback_or_die "$check" "$dst_dir"
}

# -------- self-update：如果脚本原始路径在待更新仓库内，则先 clone 新版本并 exec 新脚本 --------
maybe_self_update_self_repo() {
  [ "${DEPLOY_SELF_UPDATED:-0}" -eq 0 ] || return 0
  [ "${NO_SELF_UPDATE:-0}" -eq 0 ] || return 0

  local orig_path
  orig_path="${ORIGINAL_SCRIPT_PATH:-$(readlink -f "$0")}"

  # 只有“原始脚本路径”位于待更新仓库目录内才触发
  case "$orig_path" in
    "$LOCAL_PROJECT_DIR"/*|"$LOCAL_PROJECT_DIR") ;;
    *) return 0 ;;
  esac

  log WARN "$CHECK_ID" "SELF_UPDATE 检测到脚本位于待更新仓库内，执行自更新（clone 新版本后 exec 重启）"

  [ -n "${GIT_USER:-w30064656}" ] || die "请先 export GIT_USER=xxx"
  [ -n "${GIT_PASS:-6KLhVizsmLDvH56aCUo6zEzN}" ] || die "请先 export GIT_PASS=yyy"
  
  # state 目录（与日志绑定，稳定）
  local state_dir assets_dir new_repo_dir
  state_dir="$LOG_DIR/state"
  assets_dir="${DEPLOY_ASSET_ROOT:-$state_dir/assets}"
  new_repo_dir="$state_dir/new_repo"
  mkdir -p "$assets_dir"

  # 1) 备份旧仓库中的 config.conf 与 yaml（保持相对路径回填）

  if [ -n "$YAML_REL" ] && [ -f "$LOCAL_PROJECT_DIR/$YAML_REL" ]; then
    mkdir -p "$(dirname "$assets_dir/$YAML_REL")"
    cp -f "$LOCAL_PROJECT_DIR/$YAML_REL" "$assets_dir/$YAML_REL"
    log OK "$CHECK_ID" "SELF_UPDATE 已保存 yaml：$assets_dir/$YAML_REL"
  else
    # 如果 YAML_REL 为空，但 YAML_FILE 是绝对路径且在旧仓库内，尽量保存一份到 assets/cluster.yaml 兜底
    if [ -f "$YAML_FILE" ]; then
      cp -f "$YAML_FILE" "$assets_dir/cluster.yaml"
      export YAML_FILE="$assets_dir/cluster.yaml"
      log WARN "$CHECK_ID" "SELF_UPDATE 无法推断 yaml 相对路径，已兜底保存为：$YAML_FILE（建议传仓库内相对路径）"
    else
      log WARN "$CHECK_ID" "SELF_UPDATE 未找到 yaml 文件可保存：$YAML_FILE"
    fi
  fi

  # 2) clone 新版本到 state/new_repo
  rm -rf "$new_repo_dir" >/dev/null 2>&1 || true
  mkdir -p "$state_dir"

  log INFO "$CHECK_ID" "SELF_UPDATE 获取新版本到：$new_repo_dir（先 clone，失败则压缩包）"
  fetch_repo_or_die "SELF_UPDATE" "$new_repo_dir"

  self_rel="${orig_path#"$repo_root"/}"
  if [ "$self_rel" = "$orig_path" ]; then
    self_rel="$(basename "$orig_path")"
  fi

  local new_script_src new_script_tmp
  new_script_src="$new_repo_dir/$self_rel"
  [ -f "$new_script_src" ] || die "新版本中找不到脚本：$new_script_src（请确认脚本在仓库内路径一致）"

  new_script_tmp="/home/tmp/$(basename "$new_script_src").new.$$"
  cp -f "$new_script_src" "$new_script_tmp"
  chmod +x "$new_script_tmp"

  # 标记已经 self-update 过（防止循环）
  export DEPLOY_SELF_UPDATED=1
  NO_SELF_UPDATE=1
  # 传递上下文给新脚本：同一日志/同一日期/已 clone 新仓/资产目录
  export DEPLOY_LOG_DIR="$LOG_DIR"
  export DEPLOY_DATE_TAG="$DATE_TAG"
  export DEPLOY_PRECLONED_DIR="$new_repo_dir"
  export DEPLOY_ASSET_ROOT="$assets_dir"

  log WARN "$CHECK_ID" "SELF_UPDATE exec 到新版本脚本：$new_script_tmp"
  exec "$new_script_tmp" --no-self-update "${ORIG_ARGS[@]}"
}

# 在真正动本机仓库前先尝试 self-update（如果脚本就在待更新仓库里）
maybe_self_update_self_repo

# -------- 本机：备份旧仓库目录 + 备份 config/yaml 到 assets --------
mkdir -p "$WORKDIR"
cd "$WORKDIR"

ASSET_ROOT="${DEPLOY_ASSET_ROOT:-$LOG_DIR/state/assets}"
mkdir -p "$ASSET_ROOT"

if [ -d "$LOCAL_PROJECT_DIR" ]; then
  log INFO "$CHECK_ID" "LOCAL 发现旧目录：$LOCAL_PROJECT_DIR，准备改名为：$LOCAL_BACKUP_DIR"

  # 备份 yaml（保持路径）
  if [ -n "$YAML_REL" ] && [ -f "$LOCAL_PROJECT_DIR/$YAML_REL" ]; then
    mkdir -p "$(dirname "$ASSET_ROOT/$YAML_REL")"
    cp -f "$LOCAL_PROJECT_DIR/$YAML_REL" "$ASSET_ROOT/$YAML_REL"
    log OK "$CHECK_ID" "LOCAL 已保存旧 yaml：$ASSET_ROOT/$YAML_REL"
  else
    # 如果用户传了绝对路径且文件存在，兜底保存
    if [ -f "$YAML_FILE" ]; then
      cp -f "$YAML_FILE" "$ASSET_ROOT/cluster.yaml"
      log WARN "$CHECK_ID" "LOCAL 无法定位 yaml 相对路径，已兜底保存为：$ASSET_ROOT/cluster.yaml（建议 -y 传仓库内相对路径）"
    else
      log WARN "$CHECK_ID" "LOCAL 未找到 yaml 文件：$YAML_FILE"
    fi
  fi

  mv "$LOCAL_PROJECT_DIR" "$LOCAL_BACKUP_DIR"
  log OK "$CHECK_ID" "LOCAL 本机旧目录已改名：$LOCAL_BACKUP_DIR"
fi

[ -n "${GIT_USER:-w30064656}" ] || die "请先 export GIT_USER=xxx"
[ -n "${GIT_PASS:-6KLhVizsmLDvH56aCUo6zEzN}" ] || die "请先 export GIT_PASS=yyy"
# gitee_user="w30064656"
# gitee_passwd="6KLhVizsmLDvH56aCUo6zEzN"
# git clone https://$gitee_user:$gitee_passwd@szv-open.codehub.huawei.com/innersource/LingquInfra_G/Sprinkler.git Sprinkler_0209

# -------- 本机：获取新代码（clone 失败则压缩包下载解压）--------
if [ -n "${DEPLOY_PRECLONED_DIR:-}" ] && [ -d "$DEPLOY_PRECLONED_DIR" ]; then
  log INFO "$CHECK_ID" "FETCH 使用预先获取的新版本：$DEPLOY_PRECLONED_DIR -> $LOCAL_PROJECT_DIR"
  rm -rf "$LOCAL_PROJECT_DIR" >/dev/null 2>&1 || true
  mv "$DEPLOY_PRECLONED_DIR" "$LOCAL_PROJECT_DIR"
  validate_repo_dir_or_die "FETCH" "$LOCAL_PROJECT_DIR"
  log OK "$CHECK_ID" "FETCH 新版本目录就位"
else
  log INFO "$CHECK_ID" "FETCH 开始获取新代码（先 clone，失败则压缩包）-> $LOCAL_PROJECT_DIR"
  fetch_repo_or_die "FETCH" "$LOCAL_PROJECT_DIR"
fi

# -------- 本机：回填 config.conf 与 yaml 到新仓库 --------

# yaml 回填：优先按 YAML_REL 回填到原路径；否则用兜底 cluster.yaml
if [ -n "$YAML_REL" ] && [ -f "$ASSET_ROOT/$YAML_REL" ]; then
  mkdir -p "$(dirname "$LOCAL_PROJECT_DIR/$YAML_REL")"
  cp -f "$ASSET_ROOT/$YAML_REL" "$LOCAL_PROJECT_DIR/$YAML_REL"
  YAML_FILE="$LOCAL_PROJECT_DIR/$YAML_REL"
  log OK "$CHECK_ID" "LOCAL 已回填 yaml 到新仓：$YAML_FILE"
else
  if [ -f "$ASSET_ROOT/cluster.yaml" ]; then
    # 兜底：回填到用户原 YAML_FILE（若是相对路径则放进新仓；若是绝对路径则直接覆盖该绝对路径）
    if [[ "$YAML_FILE" != /* ]]; then
      mkdir -p "$(dirname "$LOCAL_PROJECT_DIR/$YAML_FILE")"
      cp -f "$ASSET_ROOT/cluster.yaml" "$LOCAL_PROJECT_DIR/$YAML_FILE"
      YAML_FILE="$LOCAL_PROJECT_DIR/$YAML_FILE"
      log WARN "$CHECK_ID" "LOCAL yaml 用兜底回填到：$YAML_FILE（建议 -y 传仓库内相对路径以保持路径一致）"
    else
      cp -f "$ASSET_ROOT/cluster.yaml" "$YAML_FILE"
      log WARN "$CHECK_ID" "LOCAL yaml 用兜底回填覆盖绝对路径：$YAML_FILE（建议 -y 传仓库内相对路径）"
    fi
  else
    log WARN "$CHECK_ID" "LOCAL 未找到可回填的 yaml（assets 中无 $YAML_REL 或 cluster.yaml）"
  fi
fi

# --- 获取 conda 可执行文件绝对路径（优先 which，其次解析 hook） ---
if [ -z "$CONDA_EXE" ] || [ "${CONDA_EXE#/}" = "$CONDA_EXE" ]; then
  CONDA_EXE="$(which conda 2>/dev/null | head -n1 || true)"
  if [ -z "$CONDA_EXE" ] || [ "${CONDA_EXE#/}" = "$CONDA_EXE" ]; then
    # which 没拿到绝对路径 -> 解析 conda shell.bash hook
    CONDA_EXE="$(conda shell.bash hook 2>/dev/null \
      | sed -n "s/^export CONDA_EXE='\(.*\)'.*/\1/p" \
      | head -n1 || true)"
  fi
fi

[ -n "$CONDA_EXE" ] && [ -x "$CONDA_EXE" ] || { die "ERROR: cannot locate conda executable"; }
CONDA_ROOT="$(cd "$(dirname "$CONDA_EXE")/.." && pwd)"
CONDA_SH="$CONDA_ROOT/etc/profile.d/conda.sh"

[ -f "$CONDA_SH" ] || { die "ERROR: conda.sh not found: $CONDA_SH"; }

# -------- 本机：conda + proxy + pip install -e . --------
log INFO "$CHECK_ID" "LOCAL_INSTALL 本机开始 conda + pip install -e .（env=$CONDA_ENV）"
LOCAL_INSTALL_CMD=$(cat <<SH

if [ -f "/home/pckg/miniconda3/etc/profile.d/conda.sh" ]; then
  . "/home/pckg/miniconda3/etc/profile.d/conda.sh"
elif [ -f "/home/miniconda3/etc/profile.d/conda.sh" ]; then
  . "/home/miniconda3/etc/profile.d/conda.sh"
elif [ -f "/home/anaconda3/etc/profile.d/conda.sh" ]; then
  . "/home/anaconda3/etc/profile.d/conda.sh"
else
  . "$CONDA_SH"
  command -v conda >/dev/null 2>&1 || { echo "ERROR: 找不到 conda 初始化脚本且 conda 命令不可用"; exit 2; }
fi
conda activate '$CONDA_ENV'
${HTTP_PROXY:+export http_proxy='$HTTP_PROXY'}
${HTTPS_PROXY:+export https_proxy='$HTTPS_PROXY'}

if [ -n "${http_proxy:-}" ] || [ -n "${https_proxy:-}" ]; then
  curl -fsSIL --connect-timeout 5 --max-time 12 https://www.baidu.com >/dev/null 2>&1 \
    || curl -fsSIL --connect-timeout 5 --max-time 12 http://www.baidu.com >/dev/null 2>&1 \
    || { echo "ERROR: proxy curl check failed (baidu unreachable)"; exit 10; }
fi

pip uninstall sprinkler -y
find $LOCAL_BACKUP_DIR -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find $LOCAL_BACKUP_DIR -type d -name "sprinkler.egg-info" -exec rm -rf {} + 2>/dev/null

cd '$LOCAL_PROJECT_DIR'
python -m pip cache purge
python -m pip install -U pip >/dev/null 2>&1 || true
python -m pip install -e . --trusted-host mirrors.aliyun.com
SH
)
run_local_or_die "$LOCAL_INSTALL" "$LOCAL_INSTALL_CMD"
log OK "$CHECK_ID" "LOCAL_INSTALL 本机安装完成"

# =========================
# 远端部署（可选）
# =========================
if [ "$REMOTE_ENABLED" -ne 1 ]; then
  log OK "$CHECK_ID" "DONE 未开启 remote：只完成本机操作。日志在：$LOG_DIR"
  exit 0
fi

# 开启远端必须有远端目标父目录
[ -n "$REMOTE_TARGET" ] || die "开启 remote 时必须提供 远端目标父目录"

need_cmd ssh
need_cmd scp

have_sshpass=0
if command -v sshpass >/dev/null 2>&1; then have_sshpass=1; fi
[ "$have_sshpass" -eq 1 ] || die "开启 remote 且 yaml 用密码登录：请先安装 sshpass"

ssh_opts_common=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout=10
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=3
)

# 远端脚本执行（输出写入本机 host.log）
ssh_run_script_logged() {
  local host="$1" user="$2" pass="$3" host_log="$4" script="$5"; shift 5
  local -a args=("$@")

  local tmp rc
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  printf "%s" "$pass" >"$tmp"

  log INFO "$CHECK_ID" "SSH [$host] 执行远端脚本（输出写入 $host_log）"
  # 关键：必须放在 if 条件里，避免 ERR trap 触发导致全局退出
  if printf '%s' "$script" | sshpass -f "$tmp" ssh "${ssh_opts_common[@]}" "${user}@${host}" \
      "bash -s -- $(printf '%q ' "${args[@]}")" >> "$host_log" 2>&1; then
    rc=0
  else
    rc=$?
  fi

  rm -f "$tmp"
  return "$rc"
}

rsync_push_logged() {
  local host="$1" user="$2" pass="$3" host_log="$4" src_dir="$5" dst_dir="$6"

  if ! command -v rsync >/dev/null 2>&1; then
    return 2
  fi

  local tmp rc
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  printf "%s" "$pass" >"$tmp"

  local SSH_CMD="ssh ${ssh_opts_common[*]}"
  log INFO "$CHECK_ID" "RSYNC [$host] rsync 推送：$src_dir -> $dst_dir"
  if sshpass -f "$tmp" rsync -az --partial --append-verify \
      --exclude '.git/' --exclude '__pycache__/' --exclude '*.pyc' \
      --exclude "$YAML_REL" \
      -e "$SSH_CMD" \
      "${src_dir%/}/" "${user}@${host}:${dst_dir%/}/" >> "$host_log" 2>&1; then
    rc=0
  else
    rc=$?
  fi

  rm -f "$tmp"
  return "$rc"
}

scp_push_logged() {
  local host="$1" user="$2" pass="$3" host_log="$4" src_dir="$5" dst_parent="$6"

  local tmp rc
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  printf "%s" "$pass" >"$tmp"

  log INFO "$CHECK_ID" "SCP [$host] scp -r 推送（不支持断点续传）：$src_dir -> $dst_parent"
  if sshpass -f "$tmp" scp -r "${ssh_opts_common[@]}" "$src_dir" "${user}@${host}:${dst_parent%/}/" >> "$host_log" 2>&1; then
    rc=0
  else
    rc=$?
  fi

  rm -f "$tmp"
  return "$rc"
}

REMOTE_PROJECT_DIR="${REMOTE_TARGET%/}/$PROJECT"

# 远端准备：旧目录改名 + 创建新目录
remote_prepare_cmd=$(cat <<'SH'
set -euo pipefail
remote_target="$1"
project="$2"
date_tag="$3"

mkdir -p "$remote_target"
old="${remote_target%/}/$project"
bak="${old}_${date_tag}"

# 只在第一次备份：如果 bak 已存在，认为已经备份过，避免重复 mv 导致嵌套/错乱
if [ -d "$old" ] && [ ! -d "$bak" ]; then
  mv "$old" "$bak"
  echo "MOVED_OLD=1"
else
  echo "MOVED_OLD=0"
fi

# 每次准备都清理新目录，保证重试是干净状态
rm -rf "$old"
# 给后续 rsync 一个落点目录（否则 rsync 可能因目录不存在失败）
mkdir -p "$old"
echo "READY_NEW_DIR=$old"
SH
)

# 远端后处理：回填 config + yaml，然后 conda + pip install -e .
remote_post_cmd=$(cat <<'SH'
set -euo pipefail
remote_target="$1"
project="$2"
date_tag="$3"
cfg="$4"
yaml_rel="$5"
conda_env="$6"
http_proxy_local="$7"
https_proxy_local="$8"

new_dir="${remote_target%/}/$project"
bak_dir="${new_dir}_${date_tag}"

# 回填 config
echo "COPIED_CONFIG=1"

# 回填 yaml（仅当传入相对路径且备份里存在）
if [ -n "$yaml_rel" ] && [ -f "$bak_dir/$yaml_rel" ]; then
  mkdir -p "$(dirname "$new_dir/$yaml_rel")"
  cp -f "$bak_dir/$yaml_rel" "$new_dir/$yaml_rel"
  echo "COPIED_YAML=1"
else
  echo "COPIED_YAML=0"
fi

# conda 初始化
if [ -f "/home/pckg/miniconda3/etc/profile.d/conda.sh" ]; then
  . "/home/pckg/miniconda3/etc/profile.d/conda.sh"
elif [ -f "/home/miniconda3/etc/profile.d/conda.sh" ]; then
  . "/home/miniconda3/etc/profile.d/conda.sh"
elif [ -f "/home/anaconda3/etc/profile.d/conda.sh" ]; then
  . "/home/anaconda3/etc/profile.d/conda.sh"
else
  # --- 获取 conda 可执行文件绝对路径（优先 which，其次解析 hook） ---
  if [ -z "$CONDA_EXE" ] || [ "${CONDA_EXE#/}" = "$CONDA_EXE" ]; then
    CONDA_EXE="$(which conda 2>/dev/null | head -n1 || true)"
    if [ -z "$CONDA_EXE" ] || [ "${CONDA_EXE#/}" = "$CONDA_EXE" ]; then
      # which 没拿到绝对路径 -> 解析 conda shell.bash hook
      CONDA_EXE="$(conda shell.bash hook 2>/dev/null \
        | sed -n "s/^export CONDA_EXE='\(.*\)'.*/\1/p" \
        | head -n1 || true)"
    fi
  fi

  [ -n "$CONDA_EXE" ] && [ -x "$CONDA_EXE" ] || { echo "ERROR: cannot locate conda executable"; }
  CONDA_ROOT="$(cd "$(dirname "$CONDA_EXE")/.." && pwd)"
  CONDA_SH="$CONDA_ROOT/etc/profile.d/conda.sh"
  
  [ -f "$CONDA_SH" ] || { echo "ERROR: conda.sh not found: $CONDA_SH"; }
  . "$CONDA_SH"
  command -v conda >/dev/null 2>&1 || { echo "ERROR: conda 不可用"; exit 2; }
fi

set +u
echo "XXXXXXXXXXXXXXXXXXXXXXXXXX0000000XXXXXXXXXXX"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
source /home/healthy_check/cann.sh
set -u

conda activate "$conda_env"

[ -n "$http_proxy_local" ] && export http_proxy="$http_proxy_local"
[ -n "$https_proxy_local" ] && export https_proxy="$https_proxy_local"

# 代理连通性检查：不通就报错退出
if [ -n "${http_proxy_local:-}" ] || [ -n "${https_proxy_local:-}" ]; then
  curl -fsSIL --connect-timeout 5 --max-time 12 https://www.baidu.com >/dev/null 2>&1 \
    || curl -fsSIL --connect-timeout 5 --max-time 12 http://www.baidu.com >/dev/null 2>&1 \
    || { echo "ERROR: proxy curl check failed (baidu unreachable)"; exit 10; }
fi

pip uninstall sprinkler -y
find $bak_dir -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find $bak_dir -type d -name "sprinkler.egg-info" -exec rm -rf {} + 2>/dev/null

cd "$new_dir"
python -m pip cache purge
python -m pip install -U pip >/dev/null 2>&1 || true
python -m pip install -e . --trusted-host mirrors.aliyun.com
echo "PIP_INSTALL_OK=1"
SH
)

# -------- 关键：先检查 yaml 是否有 hosts（没有就跳过远端） --------
HOST_LINES="$(parse_hosts "$YAML_FILE" || true)"
REAL_HOST_LINES="$(filter_real_hosts "$HOST_LINES" || true)"

if [ -z "$REAL_HOST_LINES" ]; then
  if [ -z "$HOST_LINES" ]; then
    log WARN "$CHECK_ID" "REMOTE 已开启 remote，但 yaml 中未解析到任何 hosts（没有服务器信息），将跳过远端操作。yaml：$YAML_FILE"
  else
    log "WARN" "$CHECK_ID" "REMOTE 已开启 remote，但 yaml 中仅包含示例/占位 hosts，跳过远端：$YAML_FILE"
  fi
  log OK "$CHECK_ID" "DONE 流程结束（仅本机）。日志在：$LOG_DIR"
  exit 0
fi

log INFO "$CHECK_ID" "HOSTS yaml 解析到的主机如下："
echo "$REAL_HOST_LINES" | awk -F'\t' '{printf "  - %s (%s)\n", $1, $2}' 1>&2 | tee -a "$MAIN_LOG" >/dev/null

deploy_one() {
  local host="$1" user="$2" pass="$3"
  local host_safe="${host//[:\/]/_}"
  local host_log="$LOG_DIR/${host_safe}.log"
  : > "$host_log"

  log INFO "$CHECK_ID" "REMOTE [$host] 主机日志：$host_log"

  # 1) 远端准备
  local attempt rc
  attempt=1
  while [ "$attempt" -le "$RETRIES" ]; do
    log INFO "$CHECK_ID" "REMOTE [$host] 远端准备 attempt=${attempt}/${RETRIES}"
    if ssh_run_script_logged "$host" "$user" "$pass" "$host_log" "$remote_prepare_cmd" \
        "$REMOTE_TARGET" "$PROJECT" "$DATE_TAG"; then
      rc=0
      break
    else
      rc=$?
      log WARN "$CHECK_ID" "REMOTE [$host] 远端准备失败 rc=$rc（见 $host_log），${attempt}/${RETRIES}"
      sleep $((2 * attempt))
    fi
    attempt=$((attempt + 1))
  done
  if [ "${rc:-1}" -ne 0 ]; then
    log FAIL "$CHECK_ID" "REMOTE [$host] 远端准备失败（重试${RETRIES}次仍失败，见 $host_log）"
    return 1
  fi

  # 2) 传输：优先 rsync（断点续传），失败重试/回退 scp
  local ok=0 rc=0
  for i in $(seq 1 "$RETRIES"); do
    log INFO "$CHECK_ID" "SYNC [$host] 传输第 $i/$RETRIES 次：尝试 rsync"
    if rsync_push_logged "$host" "$user" "$pass" "$host_log" "$LOCAL_PROJECT_DIR" "$REMOTE_PROJECT_DIR"; then
      ok=1; log OK "$CHECK_ID" "SYNC [$host] rsync 成功"; break
    else
      rc=$?
      if [ "$rc" -eq 2 ]; then
        log WARN "$CHECK_ID" "SYNC [$host] 本机无 rsync，回退 scp"
        break
      fi
      log WARN "$CHECK_ID" "SYNC [$host] rsync 失败 rc=$rc，attempt=${attempt}/${RETRIES}（见 $host_log）"
      sleep $((2 * $i))
    fi
  done

  if [ "$ok" -ne 1 ]; then
    for i in $(seq 1 "$RETRIES"); do
      log INFO "$CHECK_ID" "SYNC [$host] 传输第 $i/$RETRIES 次：scp -r（不支持续传）"
      if scp_push_logged "$host" "$user" "$pass" "$host_log" "$LOCAL_PROJECT_DIR" "$REMOTE_TARGET"; then
        ok=1; log OK "$CHECK_ID" "SYNC [$host] scp 成功"; break
      else
        rc=$?
        log WARN "$CHECK_ID" "SYNC [$host] scp 失败 rc=$rc，attempt=${attempt}/${RETRIES}（见 $host_log）"
        sleep $((2 * $i))
      fi
    done
  fi

  if [ "$ok" -ne 1 ]; then
    log FAIL "$CHECK_ID" "REMOTE [$host] 代码同步失败（重试${RETRIES}次仍失败，见 $host_log）"
    return 1
  fi

  # 3) 远端回填 config + yaml + 安装
  attempt=1
  rc=1
  while [ "$attempt" -le "$RETRIES" ]; do
    log INFO "$CHECK_ID" "REMOTE [$host] 远端安装 attempt=${attempt}/${RETRIES}"
    if ssh_run_script_logged "$host" "$user" "$pass" "$host_log" "$remote_post_cmd" \
        "$REMOTE_TARGET" "$PROJECT" "$DATE_TAG" "$CONFIG_NAME" "$YAML_REL" "$CONDA_ENV" "$HTTP_PROXY" "$HTTPS_PROXY"; then
      rc=0
      break
    else
      rc=$?
      log WARN "$CHECK_ID" "REMOTE [$host] 远端安装失败 rc=$rc（见 $host_log），${attempt}/${RETRIES}"
      sleep $((3 * attempt))
    fi
    attempt=$((attempt + 1))
  done
  if [ "$rc" -ne 0 ]; then
    log FAIL "$CHECK_ID" "REMOTE [$host] 远端安装失败（重试${RETRIES}次仍失败，见 $host_log）"
    return 1
  fi

  log OK "$CHECK_ID" "REMOTE [$host] 完成"
  return 0
}

REMOTE_TOTAL=0
REMOTE_OK=0
REMOTE_FAIL=0
FAILED_HOSTS=()

while IFS=$'\t' read -r h u p; do
  [ -n "$h" ] || continue
  REMOTE_TOTAL=$((REMOTE_TOTAL+1))

  if deploy_one "$h" "$u" "$p"; then
    REMOTE_OK=$((REMOTE_OK+1))
  else
    REMOTE_FAIL=$((REMOTE_FAIL+1))
    FAILED_HOSTS+=("$h")
    log WARN "$CHECK_ID" "REMOTE [$h] 失败，继续下一台（该机日志：$LOG_DIR/${h//[:\/]/_}.log）"
  fi
done <<<"$REAL_HOST_LINES"

if [ "$REMOTE_FAIL" -gt 0 ]; then
  log FAIL "$CHECK_ID" "DONE 远端完成：成功 ${REMOTE_OK}/${REMOTE_TOTAL}，失败 ${REMOTE_FAIL}（失败主机：${FAILED_HOSTS[*]}）"
  exit 1
else
  log OK "$CHECK_ID" "DONE 全部完成（本机+远端）：成功 ${REMOTE_OK}/${REMOTE_TOTAL}"
  exit 0
fi
