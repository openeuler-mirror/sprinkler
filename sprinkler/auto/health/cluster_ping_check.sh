#!/usr/bin/env bash
# cluster_ping_check.sh
# Controller(local/server CPU): start local server on NPU idx sweep -> ssh remote clients to run multi-NPU tests hitting local server.
# No jq. No logs on NPUs. All outputs printed on controller.
# Controller(local/server CPU) collects ALL logs locally.
# No logs saved on NPUs or client CPUs.

# ===================== USER TUNABLE DEFAULTS =====================
CHECK_ID="cluster_ping"
COUNT_OK=0
COUNT_FAIL=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMMON_SH="${COMMON_SH:-}"
if [[ -z "$COMMON_SH" ]]; then
  if [[ -f "$SCRIPT_DIR/common.sh" ]]; then
    COMMON_SH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../sprinkler/utils/common.sh"
  else
    COMMON_SH=""
  fi
fi

if [[ -n "$COMMON_SH" && -f "$COMMON_SH" ]]; then
  # shellcheck disable=SC1090
  source "$COMMON_SH"
fi

PARENT_DIR="$(dirname "$SCRIPT_DIR")"
GRANDPARENT_DIR="$(dirname "$(dirname "$(dirname "$PARENT_DIR")")")"
CONFIG_UTIL_ROOT=${GRANDPARENT_DIR}/sprinkler_config
LOGS_ROOT=${GRANDPARENT_DIR}/sprinkler_logs
mkdir -p "$CONFIG_UTIL_ROOT"
mkdir -p "$LOGS_ROOT"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../sprinkler/utils/config_util.sh"
config_require_keys \
    "CLUSTER_CONF|请输入远端机器的配置文件路径|$CONFIG_UTIL_ROOT/cluster.conf|0|"
log INFO "$CHECK_ID" "远端机器的配置文件路径: $CLUSTER_CONF"

SSH_OPTS="${SSH_OPTS:- -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR -o BatchMode=yes}"

BW_THRESHOLD="${BW_THRESHOLD:-80000}"
SAMPLE_SECONDS="${SAMPLE_SECONDS:-20}"
START_TIMEOUT="${START_TIMEOUT:-60}"
HARD_TIMEOUT="${HARD_TIMEOUT:-70}"
PASS_RATIO="${PASS_RATIO:-0.8}"

# 端口分配（link0/link1 用不同 base，彻底隔离）
BASE0="${BASE0:-21115}"
BASE1="${BASE1:-25115}"
STRIDE="${STRIDE:-256}"     # 每台 client 预留 256 个端口槽位（够 0-32）
# 端口参数名
SERVER_PORT_FLAG="${SERVER_PORT_FLAG:---port}"  # server 监听端口的参数名
CLIENT_PORT_FLAG="${CLIENT_PORT_FLAG:---port}"  # client 指定对端端口的参数名

# remote(client CPU) 侧跑多少张 NPU “并行批次”
# 每张 NPU 会同时起 link0+link1 两条流 => 实际 ssh 会更多
MAX_PARALLEL_NPUS="${MAX_PARALLEL_NPUS:-8}"
# 启动错峰（ms）
START_STAGGER_MS="${START_STAGGER_MS:-50}"

HOST_STAGGER_SEC="${HOST_STAGGER_SEC:-0.2}"
CARD_STAGGER_SEC="${CARD_STAGGER_SEC:-0.15}"
# 可选：显式排除坏卡（比如 npu7）
EXCLUDE_NPUS="${EXCLUDE_NPUS:-}"   # e.g. "7" or "6,7"

PING_COUNT="${PING_COUNT:-2}"
PING_TIMEOUT="${PING_TIMEOUT:-1}"
PING_SSH_TIMEOUT="${PING_SSH_TIMEOUT:-15}"
URMA_SHOW_TIMEOUT="${URMA_SHOW_TIMEOUT:-15}"

STARTUP_CHECK_SEC="${STARTUP_CHECK_SEC:-1}"    # server listener 拉起后检查存活
STARTUP_SLEEP_SEC="${STARTUP_SLEEP_SEC:-0.05}" # server listener 间隔

BASE_DIR=""
[[ -z "$BASE_DIR" ]] && BASE_DIR="$SCRIPT_DIR"
# ================================================================

PYBIN=""
if command -v python3 >/dev/null 2>&1; then PYBIN=python3
elif command -v python >/dev/null 2>&1; then PYBIN=python
else log FAIL "$CHECK_ID" "python3/python not found"; exit 1; fi

trim() { echo "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'; }
q_squote() { echo "$1" | sed "s/'/'\"'\"'/g"; }

# 如果传入了参数，使用传入的路径作为基础目录
# 可选：调试开关，设为1会打印 argv
DEBUG_ARGV="${DEBUG_ARGV:-0}"

print_argv() {
  (( DEBUG_ARGV == 1 )) || return 0
  local i=0
  for a in "$@"; do
    echo "# INFO: argv[$i]=$(printf '%q' "$a")" >&2
    ((i++))
  done
}

print_argv "$@"   # <<< 看看 wrapper 到底怎么传参

usage() {
  cat >&2 <<EOF
Usage:
  $0 --conf cluster.conf --server-idx <0..7|0-3|0,1,3> [--clients clientA,clientB] [--client-spec 0-7]
  
Notes:
  - Logs saved on controller under ./logs/<timestamp>/serverN/<client>/
  - This script assumes controller is also the "server machine".
EOF
  exit 1
}

CLUSTER_CONF="${CLUSTER_CONF:-2}"
SERVER_SPEC=""
CLIENTS_FILTER=""
CLIENT_SPEC_DEFAULT="0-7"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --conf) CLUSTER_CONF="${2:-}"; shift 2 ;;
    --server-idx) SERVER_SPEC="${2:-}"; shift 2 ;;
    --clients) CLIENTS_FILTER="${2:-}"; shift 2 ;;
    --client-spec) CLIENT_SPEC_DEFAULT="${2:-}"; shift 2 ;;
    *) log FAIL "$CHECK_ID" "unknown arg: $1"; usage ;;
  esac
done

[[ -n "$CLUSTER_CONF" && -f "$CLUSTER_CONF" ]] || { log FAIL "$CHECK_ID" "--conf required"; usage; }
[[ -n "$SERVER_SPEC" ]] || { log FAIL "$CHECK_ID" "--server-idx required"; usage; }

# -------------------- INI parser --------------------
declare -A C_cpu_ip C_cpu_user C_cpu_pass C_route_conf C_rootinfo C_npu_user C_npu_pass C_client_spec
declare -a SECTIONS CLIENTS

LOCAL_route_conf=""
LOCAL_rootinfo=""
LOCAL_npu_user="root"
LOCAL_npu_pass=""

parse_ini() {
  local f="$1" cur=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(trim "$line")"
    line="${line//$'\r'/}"
    [[ -z "$line" ]] && continue
    if [[ "$line" =~ ^\[(.+)\]$ ]]; then
      cur="${BASH_REMATCH[1]}"
      SECTIONS+=("$cur")
      continue
    fi
    [[ -n "$cur" ]] || continue
    if [[ "$line" =~ ^([A-Za-z0-9_]+)=(.*)$ ]]; then
      local k="${BASH_REMATCH[1]}"
      local v="$(trim "${BASH_REMATCH[2]}")"
      v="${v//$'\r'/}"
      if [[ "$cur" == "local" ]]; then
        case "$k" in
          route_conf) LOCAL_route_conf="$v" ;;
          rootinfo)  LOCAL_rootinfo="$v" ;;
          npu_user)   LOCAL_npu_user="$v" ;;
          npu_pass)   LOCAL_npu_pass="$v" ;;
        esac
      else
        case "$k" in
          cpu_ip)      C_cpu_ip["$cur"]="$v" ;;
          cpu_user)    C_cpu_user["$cur"]="$v" ;;
          cpu_pass)    C_cpu_pass["$cur"]="$v" ;;
          route_conf)  C_route_conf["$cur"]="$v" ;;
          rootinfo)   C_rootinfo["$cur"]="$v" ;;
          npu_user)    C_npu_user["$cur"]="$v" ;;
          npu_pass)    C_npu_pass["$cur"]="$v" ;;
          client_spec) C_client_spec["$cur"]="$v" ;;
        esac
      fi
    fi
  done < "$f"
}
parse_ini "$CLUSTER_CONF"

[[ -n "$LOCAL_route_conf" ]] || LOCAL_route_conf="/lib/route.conf"
[[ -f "$LOCAL_route_conf" ]] || { log FAIL "$CHECK_ID" "local route_conf not found: $LOCAL_route_conf"; exit 1; }

[[ -n "$LOCAL_rootinfo" ]] || { log FAIL "$CHECK_ID" "[local].rootinfo missing in conf"; exit 1; }
[[ -f "$LOCAL_rootinfo" ]] || { log FAIL "$CHECK_ID" "local rootinfo not found: $LOCAL_rootinfo"; exit 1; }

if [[ -n "$CLIENTS_FILTER" ]]; then
  IFS=',' read -r -a CLIENTS <<< "${CLIENTS_FILTER// /}"
else
  for s in "${SECTIONS[@]}"; do
    [[ "$s" == "local" ]] && continue
    CLIENTS+=("$s")
  done
fi
[[ ${#CLIENTS[@]} -gt 0 ]] || { log FAIL "$CHECK_ID" "no clients selected"; exit 1; }

for name in "${CLIENTS[@]}"; do
  [[ -n "${C_cpu_ip[$name]:-}" && -n "${C_cpu_user[$name]:-}" && -n "${C_route_conf[$name]:-}" && -n "${C_rootinfo[$name]:-}" ]] || {
    log FAIL "$CHECK_ID" "client [$name] missing cpu_ip/cpu_user/route_conf/rootinfo in conf"
    exit 1
  }
  [[ -n "${C_npu_user[$name]:-}" ]] || C_npu_user["$name"]="root"
done

# -------------------- Spec expand (0-7 / 0,2,3-5) --------------------
expand_spec() {
  local spec="${1// /}"
  local -a out=()
  IFS=',' read -r -a parts <<< "$spec"
  for token in "${parts[@]}"; do
    [[ -n "$token" ]] || continue
    if [[ "$token" =~ ^([0-9]+)-([0-9]+)$ ]]; then
      local a="${BASH_REMATCH[1]}" b="${BASH_REMATCH[2]}"
      if ((a<=b)); then for ((i=a;i<=b;i++)); do out+=("$i"); done
      else for ((i=a;i>=b;i--)); do out+=("$i"); done; fi
    else
      [[ "$token" =~ ^[0-9]+$ ]] || { log FAIL "$CHECK_ID" "bad spec token: $token"; exit 1; }
      out+=("$token")
    fi
  done
  local -A seen=()
  local -a uniq=()
  for x in "${out[@]}"; do
    if [[ -z "${seen[$x]:-}" ]]; then seen[$x]=1; uniq+=("$x"); fi
  done
  echo "${uniq[*]}"
}

read -r -a SERVER_IDXS <<<"$(expand_spec "$SERVER_SPEC")"
[[ ${#SERVER_IDXS[@]} -gt 0 ]] || { log FAIL "$CHECK_ID" "bad --server-idx spec: $SERVER_SPEC"; exit 1; }
# -------------------- ssh helpers --------------------
sshpass_prefix() {
  local pass="$1"
  if [[ -n "$pass" ]]; then
    if command -v sshpass >/dev/null 2>&1; then
      printf "sshpass -p %q " "$pass"
    else
      # log WARN "$CHECK_ID" "password provided but sshpass not found on controller; will try key-based ssh." >&2
      printf ""
    fi
  else
    printf ""
  fi
}

ssh_client_cpu() {
  local client="$1" cmd="$2"
  local ip="${C_cpu_ip[$client]}" user="${C_cpu_user[$client]}" pass="${C_cpu_pass[$client]:-}"
  local pfx; pfx="$(sshpass_prefix "$pass")"
  # shellcheck disable=SC2086
  # 执行SSH命令
  if ! eval ${pfx}ssh ${SSH_OPTS} "${user}@${ip}" "$(printf "%q" "$cmd")"; then
      echo "SSH连接失败，请配置免密登录："
      echo "1. 生成SSH密钥："
      echo "   ssh-keygen -t ed25519 -N \"\" -f ~/.ssh/id_ed25519"
      echo "2. 复制公钥到目标服务器："
      echo "   ssh-copy-id ${user}@${ip}"
      exit 1
  fi
}

# -------------------- ssh to local NPU (server side) --------------------
ssh_local_npu() {
  local npu_ip="$1" cmd="$2"
  local pfx=""
  if [[ -n "${LOCAL_npu_pass:-}" ]]; then
    if command -v sshpass >/dev/null 2>&1; then
      pfx="sshpass -p '$(q_squote "$LOCAL_npu_pass")' "
    else
      log WARN "$CHECK_ID" "[local].npu_pass provided but sshpass not found; try key-based."
      pfx=""
    fi
  fi
  # shellcheck disable=SC2086
  eval ${pfx}ssh ${SSH_OPTS} "${LOCAL_npu_user}@${npu_ip}" "$(printf "%q" "$cmd")"
}

# -------------------- route.conf remote_eid last8 -> ipv4 --------------------
hex8_to_ipv4() {
  local h="${1,,}"
  [[ ${#h} -eq 8 ]] || return 1
  printf "%d.%d.%d.%d" \
    "$((16#${h:0:2}))" "$((16#${h:2:2}))" "$((16#${h:4:2}))" "$((16#${h:6:2}))"
}

get_local_npu_ssh_ip() {
  local idx="$1"
  local reid
  reid="$(grep -E "^pair${idx}_chan0_remote_eid=" "$LOCAL_route_conf" | head -n1 | awk '{print $1}' | cut -d= -f2 | tr -d '[:space:]' || true)"
  [[ -n "$reid" ]] || { echo ""; return 0; }
  reid="${reid#0x}"
  reid="$(echo "$reid" | tr -cd '0-9a-fA-F' | tr 'A-F' 'a-f')"
  [[ ${#reid} -ge 8 ]] || { echo ""; return 0; }
  hex8_to_ipv4 "${reid: -8}" || true
}

# -------------------- parse rootinfo net_layer=1 info --------------------
parse_rootinfo_lines() {
  local json="$1"
  "$PYBIN" - "$json" <<'PY'
import json,sys,re
path=sys.argv[1]
with open(path,'r',encoding='utf-8') as f: obj=json.load(f)
ranks=obj.get("rank_list") or []
def clean_hex(s): return re.sub(r"[^0-9a-fA-F]","",s).lower()

def addr_to_groups8(addr_hex:str):
  h=clean_hex(addr_hex)
  if len(h)<32: return []
  h=h[-32:]  # 16 bytes -> 32 hex
  return [h[i:i+4] for i in range(0,32,4)]  # 8 groups

def eid20(addr_hex:str)->str:
  g=addr_to_groups8(addr_hex)
  if len(g)!=8: return ""
  tail=g[-5:]  # last 5 groups
  return "::" + ":".join(tail)

def suf4(addr_hex:str)->str:
  g=addr_to_groups8(addr_hex)
  if len(g)!=8: return ""
  return g[-1][-4:]

for i,r in enumerate(ranks):
  rid=r.get("rank_id", i)
  lvl1=None
  for lv in (r.get("level_list") or []):
    if lv.get("net_layer")==1:
      lvl1=lv; break
  if not lvl1: 
    continue
  addrs=[x for x in (lvl1.get("rank_addr_list") or []) if "addr" in x]
  if len(addrs)<2: 
    continue
  a,b=addrs[0],addrs[1]
  ap=len(a.get("ports") or []); bp=len(b.get("ports") or [])
  print(rid, suf4(a.get("addr","")), eid20(a.get("addr","")), ap, suf4(b.get("addr","")), eid20(b.get("addr","")), bp)
PY
}

# 稳定排序：ports 降序；相同则按 suffix4 字典序（2+2 场景保证一致）
order_links() {
  local a4="$1" a20="$2" ap="$3" b4="$4" b20="$5" bp="$6"
  if (( ap > bp )); then
    echo "$a4 $a20 $ap $b4 $b20 $bp"
  elif (( bp > ap )); then
    echo "$b4 $b20 $bp $a4 $a20 $ap"
  else
    local A="${a4,,}" B="${b4,,}"
    if [[ "$A" < "$B" ]]; then
      echo "$a4 $a20 $ap $b4 $b20 $bp"
    else
      echo "$b4 $b20 $bp $a4 $a20 $ap"
    fi
  fi
}


declare -A L_a4 L_a20 L_ap L_b4 L_b20 L_bp
while read -r rid a4 a20 ap b4 b20 bp; do
  L_a4["$rid"]="$a4"; L_a20["$rid"]="$a20"; L_ap["$rid"]="$ap"
  L_b4["$rid"]="$b4"; L_b20["$rid"]="$b20"; L_bp["$rid"]="$bp"
done < <(parse_rootinfo_lines "$LOCAL_rootinfo")



# for debug: show available suffixes
map_keys_preview() {
  local map_json="$1"
  "$PYBIN" - "$map_json" <<'PY'
import json,sys
m=json.loads(sys.argv[1])
ks=sorted(m.keys())
print("keys_count=%d preview=%s" % (len(ks), " ".join(ks[:40])))
PY
}

# -------------------- server start/kill helpers --------------------
kill_local_npu_pid() {
  local npu_ip="$1" pid="$2"
  [[ -n "$pid" ]] || return 0
  ssh_local_npu "$npu_ip" "bash -lc 'kill -TERM ${pid} >/dev/null 2>&1 || true; sleep 0.2; kill -KILL ${pid} >/dev/null 2>&1 || true; pkill -KILL -P ${pid} >/dev/null 2>&1 || true'" >/dev/null 2>&1 || true
}

alive_local_npu_pid() {
  local npu_ip="$1" pid="$2"
  ssh_local_npu "$npu_ip" "kill -0 ${pid} >/dev/null 2>&1" >/dev/null 2>&1
}

escape_squotes() { printf "%s" "$1" | sed "s/'/'\\\\''/g"; }

# ---------------- controller-side result store ----------------
# key: server_idx|client|npu|link
declare -A R_run R_bw R_mean R_min R_max R_thr R_reason R_samples R_passcnt

record_result() {
  local server_idx="$1" client="$2" npu="$3" link="$4"
  local run="$5" bw="$6" mean="$7" mn="$8" mx="$9" thr="${10}" reason="${11}" samples="${12}" passcnt="${13}"
  local key="${server_idx}|${client}|${npu}|${link}"
  R_run["$key"]="$run"
  R_bw["$key"]="$bw"
  R_mean["$key"]="$mean"
  R_min["$key"]="$mn"
  R_max["$key"]="$mx"
  R_thr["$key"]="$thr"
  R_reason["$key"]="$reason"
  R_samples["$key"]="$samples"
  R_passcnt["$key"]="$passcnt"
}

# -------------------- controller log collector (split per tag to files) --------------------
# Reads stdin, writes to:
#   outdir/_misc.log  (lines w/o [tag])
#   outdir/<tag_sanitized>.log
collector_py() {
  "$PYBIN" -u -c '
import os,sys,re,time
outdir=sys.argv[1]
os.makedirs(outdir, exist_ok=True)
tag_re=re.compile(r"^\[([^\]]+)\]\s?(.*)$")
files={}
def getf(name):
  if name not in files:
    files[name]=open(os.path.join(outdir,name),"a",encoding="utf-8")
  return files[name]
for line in sys.stdin:
  line=line.rstrip("\n")
  m=tag_re.match(line)
  if m:
    tag=m.group(1)
    rest=m.group(2)
    fn=re.sub(r"[^A-Za-z0-9_.-]+","_",tag)+".log"
    getf(fn).write(line+"\n"); getf(fn).flush()
  else:
    getf("_misc.log").write(line+"\n"); getf("_misc.log").flush()
  print(line, flush=True)
for f in files.values():
  try: f.close()
  except: pass
' "$1"
}

# ===================== REMOTE CLIENT PY (runs on client CPU) =====================
remote_client_py() {
cat <<'PY'
import os,sys,re,json,subprocess,time,statistics,selectors,shlex,random

def emit(line:str):
  print(line, flush=True)

def q(s): return shlex.quote(str(s))

CLIENT_NAME=os.environ["CLIENT_NAME"]
CLIENT_SPEC=os.environ["CLIENT_SPEC"]
CLIENT_ORD=int(os.environ["CLIENT_ORD"])

ROUTE_CONF=os.environ.get("ROUTE_CONF","/lib/route.conf")
ROOTINFO=os.environ["ROOTINFO"]
NPU_USER=os.environ.get("NPU_USER","root")
NPU_PASS=os.environ.get("NPU_PASS","")

SERVER0_EID20=os.environ["SERVER0_EID20"]
SERVER1_EID20=os.environ["SERVER1_EID20"]

BASE0=int(os.environ.get("BASE0","21115"))
BASE1=int(os.environ.get("BASE1","25115"))
STRIDE=int(os.environ.get("STRIDE","256"))
CLIENT_PORT_FLAG=os.environ.get("CLIENT_PORT_FLAG","--port")

SAMPLE_SECONDS=float(os.environ.get("SAMPLE_SECONDS","20"))
START_TIMEOUT=float(os.environ.get("START_TIMEOUT","60"))
HARD_TIMEOUT=float(os.environ.get("HARD_TIMEOUT","70"))
PASS_RATIO=float(os.environ.get("PASS_RATIO","0.8"))

# 仍然按“每批 NPU 数量”并发
MAX_PARALLEL_NPUS=int(os.environ.get("MAX_PARALLEL_NPUS","2"))

PING_COUNT=int(os.environ.get("PING_COUNT","2"))
PING_TIMEOUT=int(os.environ.get("PING_TIMEOUT","1"))
PING_SSH_TIMEOUT=int(os.environ.get("PING_SSH_TIMEOUT","15"))
URMA_SHOW_TIMEOUT=int(os.environ.get("URMA_SHOW_TIMEOUT","15"))

START_STAGGER_MS=int(os.environ.get("START_STAGGER_MS","0"))

CLIENT_STAGGER_SEC=float(os.environ.get("CLIENT_STAGGER_SEC","0"))
CLIENT_STAGGER_SCOPE=os.environ.get("CLIENT_STAGGER_SCOPE","global").strip().lower()

HOST_STAGGER_SEC=float(os.environ.get("HOST_STAGGER_SEC","0.2"))
CARD_STAGGER_SEC=float(os.environ.get("CARD_STAGGER_SEC","0.15"))
CARDS_PER_HOST=int(os.environ.get("CARDS_PER_HOST","8"))

EXCLUDE_NPUS=os.environ.get("EXCLUDE_NPUS","").replace(" ","")
EXCLUDE=set()
if EXCLUDE_NPUS:
  for t in EXCLUDE_NPUS.split(","):
    if t.isdigit(): EXCLUDE.add(int(t))

def expand_spec(spec:str):
  spec=spec.replace(" ","")
  out=[]
  for token in spec.split(","):
    if not token: continue
    m=re.match(r"^(\d+)-(\d+)$", token)
    if m:
      a=int(m.group(1)); b=int(m.group(2))
      step=1 if a<=b else -1
      for x in range(a,b+step,step): out.append(x)
    else:
      if not re.match(r"^\d+$", token):
        raise SystemExit(f"__ERROR__|client={CLIENT_NAME}|msg=bad_spec_token:{token}")
      out.append(int(token))
  uniq=[]; seen=set()
  for x in out:
    if x not in seen: seen.add(x); uniq.append(x)
  return uniq

IDX_LIST=[i for i in expand_spec(CLIENT_SPEC) if i not in EXCLUDE]

def ssh_cmd_base():
  # -tt: reduce buffering, make echo __URMA_PID__ more reliable
  return ["ssh","-tt",
          "-o","StrictHostKeyChecking=no",
          "-o","UserKnownHostsFile=/dev/null",
          "-o","ConnectTimeout=8",
          "-o","LogLevel=ERROR",
          "-o","BatchMode=yes"]

def with_sshpass(cmd:list, passwd:str)->list:
  if not passwd: return cmd
  # remote client CPU 上不一定有 sshpass
  if subprocess.call(["bash","-lc","command -v sshpass >/dev/null 2>&1"])!=0:
    return cmd
  return ["sshpass","-p",passwd] + cmd

def run_ssh(host:str, cmd_str:str, timeout:int=None):
  base=ssh_cmd_base()+[host, cmd_str]
  base=with_sshpass(base,NPU_PASS)
  try:
    proc=subprocess.run(base, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, timeout=timeout, errors="replace")
    return proc.returncode, proc.stdout
  except subprocess.TimeoutExpired as ex:
    out=""
    if getattr(ex,"stdout",None): out += ex.stdout
    if getattr(ex,"stderr",None): out += ex.stderr
    if not out: out=f"TIMEOUT after {timeout}s"
    return 124, out

def hex8_to_ipv4(h8:str)->str:
  h8=h8.lower()
  if len(h8)!=8: return ""
  try:
    a=int(h8[0:2],16); b=int(h8[2:4],16); c=int(h8[4:6],16); d=int(h8[6:8],16)
    return f"{a}.{b}.{c}.{d}"
  except: return ""

def npu_ssh_ip_from_route(idx:int)->str:
  # pairX_chan0_remote_eid=0x....last8hex -> ipv4
  try:
    with open(ROUTE_CONF,"r",encoding="utf-8",errors="ignore") as f:
      for line in f:
        if line.startswith(f"pair{idx}_chan0_remote_eid="):
          v=line.split("=",1)[1].split()[0].strip()
          v=v.replace("0x","")
          v=re.sub(r"[^0-9a-fA-F]","",v).lower()
          if len(v)>=8:
            return hex8_to_ipv4(v[-8:])
  except Exception as e:
    return ""
  return ""

def clean_hex(s): return re.sub(r"[^0-9a-fA-F]","",s).lower()

def addr_to_groups8(addr_hex:str):
  h=clean_hex(addr_hex)
  if len(h)<32: return []
  h=h[-32:]
  return [h[i:i+4] for i in range(0,32,4)]

def eid20(addr_hex:str)->str:
  g=addr_to_groups8(addr_hex)
  if len(g)!=8: return ""
  return "::"+":".join(g[-5:])

def suf4(addr_hex:str)->str:
  g=addr_to_groups8(addr_hex)
  if len(g)!=8: return ""
  return g[-1][-4:]

# Load rootinfo net_layer=1, rid may miss => index
def load_rootinfo(path:str):
  with open(path,"r",encoding="utf-8") as f: obj=json.load(f)
  ranks=obj.get("rank_list") or []
  m={}
  for i,r in enumerate(ranks):
    rid=r.get("rank_id", i)
    lvl1=None
    for lv in (r.get("level_list") or []):
      if lv.get("net_layer")==1:
        lvl1=lv; break
    if not lvl1: continue
    addrs=[x for x in (lvl1.get("rank_addr_list") or []) if "addr" in x]
    if len(addrs)<2: continue
    a,b=addrs[0],addrs[1]
    ap=len(a.get("ports") or []); bp=len(b.get("ports") or [])
    a_addr=a.get("addr",""); b_addr=b.get("addr","")
    m[rid]=(suf4(a_addr), eid20(a_addr), ap, suf4(b_addr), eid20(b_addr), bp)
  return m

rank_map=load_rootinfo(ROOTINFO)

def order_links(a4,a20,ap,b4,b20,bp):
  if ap>bp: return (a4,a20,ap,b4,b20,bp)
  if bp>ap: return (b4,b20,bp,a4,a20,ap)
  return (a4,a20,ap,b4,b20,bp) if a4.lower()<b4.lower() else (b4,b20,bp,a4,a20,ap)

ANSI=re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
CTRL=re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
ADDR8=re.compile(r'([0-9a-fA-F]{4}(?::[0-9a-fA-F]{4}){7})')
UDMA_RE=re.compile(r'(udma\d+)', re.IGNORECASE)
EID_RE=re.compile(r'\b(eid\d+)\b', re.IGNORECASE)


def ping6_from_npu(npu_ip:str, dest_eid20:str, src_eid20:str)->bool:
  host=f"{NPU_USER}@{npu_ip}"
  # 兼容：没有 ping6 时用 ping -6
  cmd = (
    f"sh -c '"
    f"(command -v ping6 >/dev/null 2>&1 && ping6 {dest_eid20} -I {src_eid20} -c {PING_COUNT} -W {PING_TIMEOUT})"
    f" || (ping -6 {dest_eid20} -l {src_eid20} -c {PING_COUNT} -W {PING_TIMEOUT})"
    f" >/dev/null 2>&1; echo __RC__=$?'"
  )
  rc,out=run_ssh(host, cmd, timeout=PING_SSH_TIMEOUT)
  if rc==124:
    emit(f"__WARN__|client={CLIENT_NAME}|npu={npu_ip}|msg=ping6_ssh_timeout|dest={dest_eid20}|src={src_eid20}")
    return False
  m=re.search(r"__RC__=(\d+)", out)
  return bool(m) and int(m.group(1))==0

emit(f"__INFO__|client={CLIENT_NAME}|msg=start|ord={CLIENT_ORD}|spec={CLIENT_SPEC}|exclude={sorted(EXCLUDE)}")
emit(f"__INFO__|client={CLIENT_NAME}|msg=server_ips|link0={SERVER0_EID20}|link1={SERVER1_EID20}|base0={BASE0}|base1={BASE1}|stride={STRIDE}")

total_streams=0
skip_npus=[]

batch=[]
batch_npus=0

def flush_batch():
  global batch,batch_npus
  if not batch: return
  emit(f"__INFO__|client={CLIENT_NAME}|msg=batch_run|streams={len(batch)}|npus={batch_npus}")
  batch=[]
  batch_npus=0

host_base = CLIENT_ORD * HOST_STAGGER_SEC


ping_count_ok = 0
ping_count_fail = 0

for idx_list_pos, idx in enumerate(IDX_LIST):
  npu_ip=npu_ssh_ip_from_route(idx)
  if not npu_ip:
    emit(f"__SKIP__|client={CLIENT_NAME}|npu={idx}|msg=no_npu_ip_from_route")
    skip_npus.append(idx); continue
  if idx not in rank_map:
    emit(f"__SKIP__|client={CLIENT_NAME}|npu={idx}|msg=rootinfo_missing_layer1")
    skip_npus.append(idx); continue

  a4,a20,ap,b4,b20,bp=rank_map[idx]
  l0_4,l0_20,l0_p, l1_4,l1_20,l1_p = order_links(a4,a20,ap,b4,b20,bp)

  # ping precheck（从 NPU ping 到 server link0/link1 IP）
  if not ping6_from_npu(npu_ip, SERVER0_EID20, l0_20):
    emit(f"__SKIP__|ping失败 client={CLIENT_NAME}|npu={idx}|msg=ping6_fail|link=L0|dest={SERVER0_EID20}|src={l0_20}")
    ping_count_fail += 1
    skip_npus.append(idx); continue
  if not ping6_from_npu(npu_ip, SERVER1_EID20, l1_20):
    emit(f"__SKIP__|ping失败 client={CLIENT_NAME}|npu={idx}|msg=ping6_fail|link=L1|dest={SERVER1_EID20}|src={l1_20}")
    ping_count_fail += 1
    skip_npus.append(idx); continue
  if ping6_from_npu(npu_ip, SERVER0_EID20, l0_20) and ping6_from_npu(npu_ip, SERVER1_EID20, l1_20):
    emit(f"__INFO__|ping成功 client={CLIENT_NAME}|npu={idx}|{NPU_USER}@{npu_ip} Success ~|link=L1|dest={SERVER1_EID20}|src={l1_20}")
    # emit(f"ping_check: src: {NPU_USER}@{npu_ip} Success ~ ")
    ping_count_ok += 1


flush_batch()

emit(f"__COUNT_OK__={ping_count_ok}")
emit(f"__COUNT_FAIL__={ping_count_fail}")
sys.exit(0)
PY
}

# ---------------- controller: parse remote lines into log + result store ----------------
# results per current server_idx
CURRENT_SERVER_IDX=""

consume_remote_line() {
  local client="$1" line="$2"
  line="${line//$'\r'/}"

  # raw log file
  echo "$line" >> "${CLIENT_RAW_LOG}"

  if [[ "$line" == __RESULT__* ]]; then
    # parse kv pairs
    local npu="" link="" run="" bw="" mean="" mn="" mx="" thr="" reason="" samples="" passcnt=""
    IFS='|' read -r -a parts <<< "$line"
    for part in "${parts[@]}"; do
      case "$part" in
        npu=*) npu="${part#npu=}" ;;
        link=*) link="${part#link=}" ;;
        run=*) run="${part#run=}" ;;
        bw=*) bw="${part#bw=}" ;;
        mean=*) mean="${part#mean=}" ;;
        min=*) mn="${part#min=}" ;;
        max=*) mx="${part#max=}" ;;
        thr=*) thr="${part#thr=}" ;;
        reason=*) reason="${part#reason=}" ;;
        samples=*) samples="${part#samples=}" ;;
        pass=*) passcnt="${part#pass=}" ;;
      esac
    done
    record_result "$CURRENT_SERVER_IDX" "$client" "$npu" "$link" "$run" "$bw" "$mean" "$mn" "$mx" "$thr" "$reason" "$samples" "$passcnt"

    return 0
  fi

  if [[ "$line" == __SKIP__* ]]; then
    log WARN "$CHECK_ID" "skip: ${line#__SKIP__|}"
    return 0
  fi

  if [[ "$line" == __WARN__* ]]; then
    log WARN "$CHECK_ID" "${line#__WARN__|}"
    return 0
  fi

  if [[ "$line" == __ERROR__* ]]; then
    log FAIL "$CHECK_ID" "${line#__ERROR__|}"
    return 0
  fi

  if [[ "$line" == __INFO__* ]]; then
    log INFO "$CHECK_ID" "${line#__INFO__|}"
    return 0
  fi

  # 其它普通输出，统一 INFO
  log INFO "$CHECK_ID" "[${client}] ${line}"
}

# ---------------- summary printer ----------------
print_summary_for_server() {
  local server_idx="$1"
  log INFO "$CHECK_ID" "================ server_idx=${server_idx} 汇总 ================"

  log INFO "$CHECK_ID" "Ping check between clusters: local client-$client "
  # 统计：按 client+npu 汇总两条 link
  local total_run_pass=0 total_run=0 total_bw_pass=0 total_bw=0
  local total_ok=0 total_fail=0 total_nums=0
  total_ok=$COUNT_OK
  total_fail=$COUNT_FAIL
  total_nums=$((total_ok+total_fail))
  # 总结
  log INFO "$CHECK_ID" ">>>AUTO_DETAILS_START<<<"
  local level="OK"
  if [[ $total_ok -eq $total_nums ]]; then level="OK"
  elif [[ $total_ok -eq 0 ]]; then level="FAIL"
  elif [[ $total_ok -lt $total_nums ]]; then level="WARN"
  fi
  case "$level" in
    OK) log OK "$CHECK_ID" "result: Ping check $total_ok / $total_nums" ;;
    WARN) log WARN "$CHECK_ID" "result: Ping check $total_ok / $total_nums" ;;
    FAIL) log FAIL "$CHECK_ID"  "result: Ping check $total_ok / $total_nums" ;;
  esac
  log INFO "$CHECK_ID" ">>>AUTO_DETAILS_END<<<"


}

# ===================== MAIN =====================
ts="$(date +%Y%m%d_%H%M%S)"
log INFO "$CHECK_ID" "controller start: conf=${CLUSTER_CONF}, server_idx_spec=${SERVER_SPEC}, clients=${CLIENTS[*]}"
log INFO "$CHECK_ID" "ports: BASE0=${BASE0}, BASE1=${BASE1}, STRIDE=${STRIDE}, server_flag=${SERVER_PORT_FLAG}, client_flag=${CLIENT_PORT_FLAG}"
log INFO "$CHECK_ID" "stagger: HOST_STAGGER_SEC=${HOST_STAGGER_SEC}, CARD_STAGGER_SEC=${CARD_STAGGER_SEC} (same-card links start together)"

OVERALL_FAIL=0

for CURRENT_SERVER_IDX in "${SERVER_IDXS[@]}"; do
  log INFO "$CHECK_ID" "==================== SERVER_IDX=${CURRENT_SERVER_IDX} ===================="
  # log dir on controller
  ROOT_LOG_DIR="$BASE_DIR/cluster_ping_check_logs/${ts}/server${SERVER_IDX}"
  mkdir -p "$ROOT_LOG_DIR"

  # resolve server NPU ssh ip
  server_npu_ip="$(get_local_npu_ssh_ip "$CURRENT_SERVER_IDX")"
  if [[ -z "$server_npu_ip" ]]; then
    log FAIL "$CHECK_ID" "cannot derive server NPU ssh ip for idx=${CURRENT_SERVER_IDX} from ${LOCAL_route_conf}"
    OVERALL_FAIL=1
    continue
  fi
  # rootinfo 里取该 server_idx 的 link0/link1 suffix + bind_ip
  if [[ -z "${L_a4[$CURRENT_SERVER_IDX]:-}" || -z "${L_b4[$CURRENT_SERVER_IDX]:-}" ]]; then
    log FAIL "$CHECK_ID" "rootinfo missing net_layer==1 addr info for rank_id=${CURRENT_SERVER_IDX}"
    OVERALL_FAIL=1
    continue
  fi

  read -r SV0_LAST4 SV0_EID20 SV0_PORTS SV1_LAST4 SV1_EID20 SV1_PORTS \
    <<<"$(order_links "${L_a4[$CURRENT_SERVER_IDX]}" "${L_a20[$CURRENT_SERVER_IDX]}" "${L_ap[$CURRENT_SERVER_IDX]}" \
                     "${L_b4[$CURRENT_SERVER_IDX]}" "${L_b20[$CURRENT_SERVER_IDX]}" "${L_bp[$CURRENT_SERVER_IDX]}")"

  log INFO "$CHECK_ID" "server npu ssh_ip=${server_npu_ip}"
  log INFO "$CHECK_ID" "link0: suffix=${SV0_LAST4}, ports=${SV0_PORTS}, eid20=${SV0_EID20}"
  log INFO "$CHECK_ID" "link1: suffix=${SV1_LAST4}, ports=${SV1_PORTS}, eid20=${SV1_EID20}"
  log INFO "$CHECK_ID" "port flags: server=${SERVER_PORT_FLAG} client=${CLIENT_PORT_FLAG} base0=${BASE0} base1=${BASE1} stride=${STRIDE}"



  # Run each client sequentially:
  #   1) Start server listeners for that client's npu indices (unique ports)
  #   2) Run remote client python, pipe all logs to controller collector
  #   3) Kill those server listeners
  for ci in "${!CLIENTS[@]}"; do
    c="${CLIENTS[$ci]}"
    c_spec="${C_client_spec[$c]:-$CLIENT_SPEC_DEFAULT}"
    idxs_str="$(expand_spec "$c_spec")"
    read -r -a idxs <<< "$idxs_str"

    client_log_dir="${ROOT_LOG_DIR}/${c}"
    mkdir -p "$client_log_dir"
    CLIENT_RAW_LOG="${client_log_dir}/raw.log"
    : > "$CLIENT_RAW_LOG"

    log INFO "$CHECK_ID" "---- launch client=${c} ord=${ci} spec=${c_spec} ----"

    # 1) 起 server listeners（对该 client 的每张 npu 都起 link0/link1 两个 wait）
    declare -A SV_PIDS=()
    server_fail=0

    # cleanup hook per client
    cleanup_client() {
      log INFO "$CHECK_ID" "[CLEANUP] stop server listeners for client=${c} ..."
      for k in "${!SV_PIDS[@]}"; do
        kill_local_npu_pid "$server_npu_ip" "${SV_PIDS[$k]}"
      done
      log INFO "$CHECK_ID" "[CLEANUP] done."
    }
    trap cleanup_client INT TERM

    if (( server_fail == 1 )); then
      log FAIL "$CHECK_ID" "server listener start failed for client=${c} (server_idx=${CURRENT_SERVER_IDX})"
      cleanup_client
      trap - INT TERM
      OVERALL_FAIL=1
      continue
    fi
    log INFO "$CHECK_ID" "server listeners started: count=${#SV_PIDS[@]} (client=${c})"

    # 2) 远端 client CPU 执行 remote python
    remote_cmd=$(
      cat <<EOF
CLIENT_NAME=$(printf %q "$c") \
CLIENT_SPEC=$(printf %q "$c_spec") \
CLIENT_ORD=$(printf %q "$ci") \
ROUTE_CONF=$(printf %q "${C_route_conf[$c]}") \
ROOTINFO=$(printf %q "${C_rootinfo[$c]}") \
NPU_USER=$(printf %q "${C_npu_user[$c]}") \
NPU_PASS=$(printf %q "${C_npu_pass[$c]:-}") \
SERVER0_EID20=$(printf %q "$SV0_EID20") \
SERVER1_EID20=$(printf %q "$SV1_EID20") \
BASE0=$(printf %q "$BASE0") \
BASE1=$(printf %q "$BASE1") \
STRIDE=$(printf %q "$STRIDE") \
CLIENT_PORT_FLAG=$(printf %q "$CLIENT_PORT_FLAG") \
BW_THRESHOLD=$(printf %q "$BW_THRESHOLD") \
SAMPLE_SECONDS=$(printf %q "$SAMPLE_SECONDS") \
START_TIMEOUT=$(printf %q "$START_TIMEOUT") \
HARD_TIMEOUT=$(printf %q "$HARD_TIMEOUT") \
PASS_RATIO=$(printf %q "$PASS_RATIO") \
MAX_PARALLEL_NPUS=$(printf %q "$MAX_PARALLEL_NPUS") \
START_STAGGER_MS=$(printf %q "$START_STAGGER_MS") \
EXCLUDE_NPUS=$(printf %q "$EXCLUDE_NPUS") \
PING_COUNT=$(printf %q "$PING_COUNT") \
PING_TIMEOUT=$(printf %q "$PING_TIMEOUT") \
PING_SSH_TIMEOUT=$(printf %q "${PING_SSH_TIMEOUT:-15}") \
CLIENT_STAGGER_SEC=$(printf %q "${CLIENT_STAGGER_SEC:-1}") \
CLIENT_STAGGER_SCOPE=$(printf %q "${CLIENT_STAGGER_SCOPE:-global}") \
HOST_STAGGER_SEC=$(printf %q "${HOST_STAGGER_SEC:-0.2}") \
CARD_STAGGER_SEC=$(printf %q "${CARD_STAGGER_SEC:-0.15}") \
$PYBIN -u - <<'PY'
$(remote_client_py)
PY
EOF
    )

    get_count() {
      local client="$1"
      local line="$2"
      
      if [[ "$line" =~ ^__COUNT_OK__=([0-9]+)$ ]]; then
        # 累加到现有值，如果没有值则初始化为0
        COUNT_OK["$client"]=$(( ${COUNT_OK["$client"]:-(0)} + ${BASH_REMATCH[1]} ))
        # log DEBUG "$CHECK_ID" "Client ${client}: new count_ok=${COUNT_OK["$client"]}"
        
      elif [[ "$line" =~ ^__COUNT_FAIL__=([0-9]+)$ ]]; then
        COUNT_FAIL["$client"]=$(( ${COUNT_FAIL["$client"]:-(0)} + ${BASH_REMATCH[1]} ))
        # log DEBUG "$CHECK_ID" "Client ${client}: new count_fail=${COUNT_FAIL["$client"]}"
      fi
    }


    # run and collect logs (controller splits to per-tag files)
    set +e
    while IFS= read -r line; do
      consume_remote_line "$c" "$line"
      get_count "$c" "$line"
    done < <(ssh_client_cpu "$c" "$remote_cmd" 2>&1)
    rc=$?
    set -e

    # 3) cleanup server listeners
    trap - INT TERM
    cleanup_client

    if [[ "$rc" -ne 0 ]]; then
      log WARN "$CHECK_ID" "client ${c} remote returned rc=${rc} (server_idx=${CURRENT_SERVER_IDX}) logs=${client_log_dir}"
      OVERALL_FAIL=1
    else
      log INFO "$CHECK_ID" "client ${c} completed (server_idx=${CURRENT_SERVER_IDX}) logs=${client_log_dir}"
    fi
  done

  print_summary_for_server "$CURRENT_SERVER_IDX"
done

log ""
log INFO "$CHECK_ID" "Logs saved under: $BASE_DIR/cluster_ping_check_logs/${ts}"
if [[ "$OVERALL_FAIL" -eq 0 ]]; then
  log OK "$CHECK_ID" "ALL PASS"
  exit 0
else
  log FAIL "$CHECK_ID" "SOME FAIL (check logs)"
  exit 1
fi