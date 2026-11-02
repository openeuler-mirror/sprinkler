"""PoDManager纳管条件检查 - 检查BMC/MeSwitch/UBM的纳管状态"""

import json
import re
import subprocess
import platform
import paramiko
import socket
import getpass
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from datetime import datetime
from sprinkler.logging import log

REAL_BMC_URI_PATH = "/usr/local/podmanager/rackd-service/comm/conf/real_bmc_uri/real_bmc.json"
UBM_CONFIGURATION_PATH = "/usr/local/podmanager/ubm-service/sysm-service/conf/configuration.json"
ACCOUNT_CONFIG_PATH = "/usr/local/podmanager/rackd-service/comm/conf/device_manager/account.json"
DECRYPT_TOOL_PATH = "/DecryptTool_V3_ARM"
KEYSTORE_KA_PATH = "/data/trust/keystore/ksfa"
KEYSTORE_KB_PATH = "/data/trust/keystore/ksfb"
LOCAL_API_BASE = "http://127.0.0.1:34256"
POD_MANAGER_API_BASE = "http://127.0.0.1:9090"

def input_password(prompt: str = "  PASSWORD: ") -> str:
    """安全输入密码（不显示明文）"""
    return getpass.getpass(prompt)

@dataclass
class DeviceCredential:
    """设备凭据：用户名和加密密码"""
    username: str
    encrypted_password: str
    decrypted_password: Optional[str] = None

@dataclass
class PodManagerConfig:
    """PoDManager配置：BMC/UBM/MeSwitch的IP映射和凭据"""
    bmc_ip_map: dict[str, str] = field(default_factory=dict)
    ubm_ip_map: dict[str, tuple[Optional[str], Optional[str]]] = field(default_factory=dict)
    bmc_credential: Optional[DeviceCredential] = None
    ubm_credential: Optional[DeviceCredential] = None
    meswitch_credential: Optional[DeviceCredential] = None
    account_data: dict = field(default_factory=dict)  # 新增：保存原始account数据
    pod_manager_ip: str = "127.0.0.1"

class ConfigLoader:
    """配置加载器：读取BMC/UBM/MeSwitch的IP和凭据"""

    def __init__(self, check_id: str = "POD_MANAGER_CONFIG"):
        self.check_id = check_id

    def load_real_bmc_uri(self) -> dict[str, str]:
        """从real_bmc.json加载BMC IP映射"""
        bmc_ip_map = {}
        path = Path(REAL_BMC_URI_PATH)

        if not path.exists():
            log("WARN", self.check_id, f"配置文件不存在: {REAL_BMC_URI_PATH}")
            return bmc_ip_map

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            data = json.loads(content)
            blade_map = data.get("bladeIdAndBmcMap", {})

            for blade_id, uri in blade_map.items():
                if not uri:
                    log("WARN", self.check_id, f"Blade {blade_id} 的BMC IP为空")
                    continue
                ip = self._extract_ip_from_uri(uri)
                if ip:
                    bmc_ip_map[blade_id] = ip

            log("INFO", self.check_id, f"成功加载 {len(bmc_ip_map)} 个BMC IP映射")
            return bmc_ip_map

        except json.JSONDecodeError as e:
            log("FAIL", self.check_id, f"JSON解析失败: {e}")
            return bmc_ip_map
        except Exception as e:
            log("FAIL", self.check_id, f"读取配置文件异常: {e}")
            return bmc_ip_map

    def load_ubm_configuration(self) -> dict[str, tuple[Optional[str], Optional[str]]]:
        """从configuration.json加载UBM/MeSwitch IP配置"""
        ubm_ip_map = {}
        path = Path(UBM_CONFIGURATION_PATH)

        if not path.exists():
            log("WARN", self.check_id, f"配置文件不存在: {UBM_CONFIGURATION_PATH}")
            return ubm_ip_map

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            data = json.loads(content)
            ubmc_nodes = data.get("ubmc-nodes", [])

            for node in ubmc_nodes:
                slot_id = node.get("board-id")
                first_ip = self._extract_ip_from_uri(node.get("device-first-ip-port", ""))
                second_ip = self._extract_ip_from_uri(node.get("device-second-ip-port", ""))
                if slot_id:
                    ubm_ip_map[slot_id] = (first_ip, second_ip)

            log("INFO", self.check_id, f"成功加载 {len(ubm_ip_map)} 个UBM配置")
            return ubm_ip_map

        except json.JSONDecodeError as e:
            log("FAIL", self.check_id, f"JSON解析失败: {e}")
            return ubm_ip_map
        except Exception as e:
            log("FAIL", self.check_id, f"读取配置文件异常: {e}")
            return ubm_ip_map

    def load_account_config(self) -> dict:
        """从account.json加载设备账户配置"""
        path = Path(ACCOUNT_CONFIG_PATH)
        if not path.exists():
            log("WARN", self.check_id, f"配置文件不存在: {ACCOUNT_CONFIG_PATH}")
            return {}

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            account_data = json.loads(content)
            log("INFO", self.check_id, "成功加载账户配置文件")
            return account_data
        except Exception as e:
            log("FAIL", self.check_id, f"读取账户配置异常: {e}")
            return {}

    def decrypt_password(self, encrypted_password: str) -> Optional[str]:
        """使用DecryptTool解密密码"""
        if not encrypted_password:
            return None

        try:
            cmd = [DECRYPT_TOOL_PATH, KEYSTORE_KA_PATH, KEYSTORE_KB_PATH, encrypted_password]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                output = result.stdout.strip()
                if output.startswith("plainText="):
                    return output[len("plainText="):]
                return output
            log("WARN", self.check_id, f"密码解密失败: {result.stderr}")
            return None
        except Exception as e:
            log("FAIL", self.check_id, f"密码解密异常: {e}")
            return None

    def build_pod_manager_config(self) -> PodManagerConfig:
        """构建完整配置对象"""
        config = PodManagerConfig()
        config.bmc_ip_map = self.load_real_bmc_uri()
        config.ubm_ip_map = self.load_ubm_configuration()

        account_data = self.load_account_config()
        config.account_data = account_data

        return config

    def _extract_ip_from_uri(self, uri: str) -> Optional[str]:
        """从URI中提取IP地址，支持格式：https://IP:port 或 IP:port"""
        match = re.search(r"(?:https?://)?([^:/]+)", uri)
        return match.group(1) if match else None

class NetworkChecker:
    """网络连通性检查：Ping、端口检测、SSH连接"""

    def __init__(self, check_id: str = "NETWORK_CHECK"):
        self.check_id = check_id

    def ping(self, ip: str, count: int = 4, timeout: int = 5) -> bool:
        try:
            cmd = ["ping", "-c", str(count), "-W", str(timeout), ip]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout * count + 10)
            return result.returncode == 0
        except Exception:
            return False

    def check_port_open(self, ip: str, port: int, timeout: int = 5) -> bool:
        try:
            result = subprocess.run(
                ["bash", "-c", f"timeout {timeout} bash -c 'cat < /dev/null > /dev/tcp/{ip}/{port}' 2>/dev/null"],
                capture_output=True, text=True, timeout=timeout + 5)
            return result.returncode == 0
        except Exception:
            return False

    def ssh_connect(self, ip, username, password, timeout=10):
        """使用 paramiko 进行 SSH 连接测试"""
        
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh_client.connect(
                hostname=ip,
                port=22,
                username=username,
                password=password,
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout
            )
            
            # 3. 验证连接成功
            log("INFO", self.check_id, f"SSH connection to {ip} SUCCESS")
            return True
            
        except paramiko.AuthenticationException:
            # 认证失败（密码错误或用户不存在）
            log("FAIL", self.check_id, f"SSH AUTH FAILED for {ip}: Invalid credentials.")
            return False
            
        except paramiko.SSHException as e:
            # SSH 协议层面的错误（如连接被拒绝、密钥交换失败等）
            log("FAIL", self.check_id, f"SSH PROTOCOL ERROR for {ip}: {str(e)}")
            return False
            
        except socket.timeout:
            # 连接超时
            log("FAIL", self.check_id, f"SSH connection to {ip} TIMED OUT after {timeout}s")
            return False
            
        except ConnectionRefusedError:
            # 端口不通或服务未启动
            log("FAIL", self.check_id, f"SSH connection to {ip} REFUSED: Port 22 not open.")
            return False
            
        except Exception as e:
            # 其他未知错误
            log("FAIL", self.check_id, f"SSH connection to {ip} FAILED with unexpected exception: {type(e).__name__}: {e}")
            return False
            
        finally:
            if ssh_client:
                ssh_client.close()


class MeswitchChecker:
    """MeSwitch纳管检查：Ping + UBM REST API状态 + SSH备用"""

    def __init__(self, config: PodManagerConfig, check_id: str = "MESWITCH_CHECK"):
        self.config = config
        self.check_id = check_id
        self.network_checker = NetworkChecker(check_id)

    def _get_meswitch_credential(self, slot_id: str) -> Optional[DeviceCredential]:
        """根据slot_id获取MeSwitch凭据"""
        account_data = self.config.account_data
        ubm_data = account_data.get("ubm", {})

        slot_num = re.search(r'\d+', slot_id)
        if not slot_num:
            log("WARN", self.check_id, f"  -> 无法从{slot_id}提取slot编号")
            return None

        pwd_key = f"default_password_{slot_num.group()}"
        encrypted_password = ubm_data.get(pwd_key, "")

        if not encrypted_password:
            log("WARN", self.check_id, f"  -> 未找到{slot_id}对应的密码字段: {pwd_key}")
            return None

        username = ubm_data.get("default_user", "huawei")
        return DeviceCredential(username=username, encrypted_password=encrypted_password)

    def _decrypt_credential(self, cred: DeviceCredential) -> bool:
        """解密凭据"""
        if not cred or not cred.encrypted_password:
            return False
        if cred.decrypted_password:
            return True

        cred.decrypted_password = ConfigLoader(self.check_id).decrypt_password(cred.encrypted_password)
        return bool(cred.decrypted_password)

    def run_check(self, slot_id: str = "361") -> bool:
        log("INFO", self.check_id, "=" * 40)
        log("INFO", self.check_id, f"开始检查MeSwitch (槽位: {slot_id})")

        meswitch_ip = self._get_meswitch_ip(slot_id)
        if not meswitch_ip:
            log("FAIL", self.check_id, f"未找到槽位 {slot_id} 的MeSwitch IP")
            return False

        log("INFO", self.check_id, f"MeSwitch IP: {meswitch_ip}")

        meswitch_cred = self._get_meswitch_credential(slot_id)
        if meswitch_cred:
            self._decrypt_credential(meswitch_cred)
            self.config.meswitch_credential = meswitch_cred

        if not self._check_ping(meswitch_ip):
            log("FAIL", self.check_id, f"MeSwitch {meswitch_ip} Ping不可达")
            return False

        ubmc_status_ok = self._check_ubmc_meswitch_status(slot_id)
        ssh_status_ok = self._check_ssh_fallback(meswitch_ip)

        if ubmc_status_ok and ssh_status_ok:
            log("PASS", self.check_id, f"MeSwitch {meswitch_ip} 纳管检查通过且 SSH 连接成功")
            return True
        else:
            if not ubmc_status_ok:
                log("FAIL", self.check_id, f"MeSwitch {meswitch_ip} UBMC 纳管状态异常 (2.2)")
            if not ssh_status_ok:
                log("FAIL", self.check_id, f"MeSwitch {meswitch_ip} SSH 连接失败 (2.3)")
            return False

    def _get_meswitch_ip(self, slot_id: str) -> Optional[str]:
        ubm_config = self.config.ubm_ip_map.get(slot_id)
        return ubm_config[0] if ubm_config else None

    def _check_ping(self, ip: str) -> bool:
        return self.network_checker.ping(ip)

    def _check_ubmc_meswitch_status(self, slot_id: str) -> bool:
        """查询UBMC REST API检查MeSwitch connect-status"""
        try:
            cmd = ["curl", "-X", "GET",
                f"{LOCAL_API_BASE}/restconf/data/huawei-devm:devm/inventory/ubm-logical-entitys",
                "-H", "Accept: application/yang-data+xml",
                "-H", "Content-Type: application/yang-data+xml", "-v"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return False
            output = result.stdout + result.stderr
            pattern = rf'<ubm-logical-entity>.*?<position>{slot_id}</position>.*?<device-status>connected</device-status>.*?</ubm-logical-entity>'
            if re.search(pattern, output, re.DOTALL):
                return True    
            return False

        except Exception as e:
            return False

    def _check_ssh_fallback(self, ip: str) -> bool:
        """SSH连通性检查"""
        cred = self.config.meswitch_credential
        if not cred:
            return False
        if not cred.decrypted_password:
            cred.decrypted_password = ConfigLoader(self.check_id).decrypt_password(cred.encrypted_password)
        if not cred.decrypted_password:
            return False
        return self.network_checker.ssh_connect(
            ip=ip, 
            username=cred.username, 
            password=cred.decrypted_password
        )

class BmcChecker:
    """BMC纳管检查：Ping + 证书链校验 + 超节点配置对比"""

    def __init__(self, config: PodManagerConfig, check_id: str = "BMC_CHECK"):
        self.config = config
        self.check_id = check_id
        self.network_checker = NetworkChecker(check_id)
        self.token = None
        self._pod_manager_username = None
        self._pod_manager_password = None
        self._pod_manager_ip = None
        self._auth_verified = False

    def _read_pod_manager_auth(self) -> bool:
        """读取PoDManager认证信息"""
        if self._auth_verified:
            return True
        log("INFO", self.check_id, "=" * 40)
        log("INFO", self.check_id, "请输入PoDManager认证信息:")
        try:
            self._pod_manager_username = input("  USERNAME: ").strip()
            self._pod_manager_password = input_password("  PASSWORD: ")
            self._pod_manager_ip = input("  IP: ").strip()
            if not self._pod_manager_username or not self._pod_manager_password or not self._pod_manager_ip:
                log("FAIL", self.check_id, "  -> USERNAME、PASSWORD和IP不能为空")
                return False
            self._auth_verified = True
            log("INFO", self.check_id, f"  -> PoDManager认证信息已记录: IP={self._pod_manager_ip}")
            log("INFO", self.check_id, "=" * 40)
            return True

        except Exception as e:
            log("FAIL", self.check_id, f"  -> 输入异常: {e}")
            return False

    def _get_bmc_credential(self, blade_id: str) -> Optional[DeviceCredential]:
        """根据blade_id获取BMC凭据"""
        account_data = self.config.account_data
        bmc_data = account_data.get("bmc", {})
        blade_num = re.search(r'\d+', blade_id)
        if not blade_num:
            log("WARN", self.check_id, f"  -> 无法从{blade_id}提取blade编号")
            return None

        pwd_key = f"default_password_{blade_num.group()}"
        encrypted_password = bmc_data.get(pwd_key, "")

        if not encrypted_password:
            log("WARN", self.check_id, f"  -> 未找到{blade_id}对应的密码字段: {pwd_key}")
            return None

        username = bmc_data.get("default_user", "PoDMInnerAdmin")
        return DeviceCredential(username=username, encrypted_password=encrypted_password)

    def _decrypt_credential(self, cred: DeviceCredential) -> bool:
        """解密凭据"""
        if not cred or not cred.encrypted_password:
            return False
        if cred.decrypted_password:
            return True

        cred.decrypted_password = ConfigLoader(self.check_id).decrypt_password(cred.encrypted_password)
        return bool(cred.decrypted_password)

    def run_check(self, blade_id: str) -> bool:

        log("INFO", self.check_id, "=" * 40)

        log("INFO", self.check_id, f"开始检查BMC (Blade: {blade_id})")

        if not self._read_pod_manager_auth():
            log("FAIL", self.check_id, "PoDManager认证信息读取失败")
            return False

        bmc_cred = self._get_bmc_credential(blade_id)
        if not bmc_cred:
            log("FAIL", self.check_id, f"  -> 无法获取{blade_id}的BMC凭据")
            return False

        if not self._decrypt_credential(bmc_cred):
            log("WARN", self.check_id, "  -> BMC密码解密失败")

        self.config.bmc_credential = bmc_cred

        bmc_ip = self.config.bmc_ip_map.get(blade_id)
        if not bmc_ip:
            log("FAIL", self.check_id, f"未找到Blade {blade_id} 的BMC IP")
            return False

        log("INFO", self.check_id, f"BMC IP: {bmc_ip}")

        if not self._check_ping(bmc_ip):
            log("FAIL", self.check_id, f"BMC {bmc_ip} Ping不可达")
            return False
        if not self._check_certificates(blade_id, bmc_ip):
            log("FAIL", self.check_id, f"BMC {bmc_ip} 证书检查失败")
            return False
        if not self._check_superpod_config(blade_id, bmc_ip):
            log("FAIL", self.check_id, f"BMC {bmc_ip} 超节点配置不一致")
            return False
        else:
            log("PASS", self.check_id, f"BMC {bmc_ip} 超节点配置一致")

        log("PASS", self.check_id, f"BMC {bmc_ip} 纳管检查通过")
        return True

    def _check_ping(self, ip: str) -> bool:
        return self.network_checker.ping(ip)

    def _check_certificates(self, blade_id: str, bmc_ip: str) -> bool:
        """证书链校验"""
        log("INFO", self.check_id, "步骤3.2: 开始证书链校验")
        all_pass = True

        log("INFO", self.check_id, "步骤3.2: 尝试BMC Redfish登录 (50080端口)")
        if not self._check_bmc_redfish_login(bmc_ip):
            log("WARN", self.check_id, "步骤3.2: BMC Redfish登录失败")
            all_pass = False
        else:
            log("INFO", self.check_id, "步骤3.2: BMC Redfish登录成功")

        log("INFO", self.check_id, "步骤3.2.1-3.2.4: 检查PoDManager信任证书")
        if not self._check_podmanager_trust_certs(blade_id):
            log("FAIL", self.check_id, "步骤3.2.1-3.2.4: PoDManager信任证书检查失败")
            all_pass = False
        else:
            log("INFO", self.check_id, "步骤3.2.1-3.2.4: PoDManager信任证书检查通过")

        log("INFO", self.check_id, "步骤3.2.5-3.2.7: 检查PoDManager南向身份证书")
        if not self._check_podmanager_south_cert(blade_id):
            log("FAIL", self.check_id, "步骤3.2.5-3.2.7: PoDManager南向身份证书检查失败")
            all_pass = False
        else:
            log("INFO", self.check_id, "步骤3.2.5-3.2.7: PoDManager南向身份证书检查通过")

        log("INFO", self.check_id, "步骤3.2.9-3.2.12: 检查BMC SSL证书")
        if not self._check_bmc_ssl_cert(blade_id, bmc_ip):
            log("FAIL", self.check_id, "步骤3.2.9-3.2.12: BMC SSL证书检查失败")
            all_pass = False
        else:
            log("INFO", self.check_id, "步骤3.2.9-3.2.12: BMC SSL证书检查通过")

        log("INFO", self.check_id, "步骤3.2.13-3.2.16: 检查BMC CA证书")
        if not self._check_bmc_ca_certs(blade_id, bmc_ip):
            log("FAIL", self.check_id, "步骤3.2.13-3.2.16: BMC CA证书检查失败")
            all_pass = False
        else:
            log("INFO", self.check_id, "步骤3.2.13-3.2.16: BMC CA证书检查通过")

        return all_pass

    def _check_bmc_redfish_login(self, bmc_ip: str) -> bool:
        """使用PoDManager证书发起BMC Redfish认证"""
        log("INFO", self.check_id, "  -> 解密server_key_pwd.txt")
        try:
            server_key_pwd_path = "/data/trust/cert/south/server_key_pwd.txt"
            server_key_pwd = Path(server_key_pwd_path).read_text().strip()
            decrypted_key_pwd = ConfigLoader(self.check_id).decrypt_password(server_key_pwd)
            if not decrypted_key_pwd:
                log("WARN", self.check_id, "  -> 解密密码失败")
                return False
            log("INFO", self.check_id, f"  -> 解密成功，密码: {decrypted_key_pwd}")

            log("INFO", self.check_id, f"  -> 执行curl请求 https://{bmc_ip}:50080/redfish/v1/SessionService/Sessions")
            cmd = ["curl", "-v", "-X", "POST",
                   "--cert", "/data/trust/cert/south/server.pem",
                   "--key", "/data/trust/cert/south/server.key",
                   "--pass", decrypted_key_pwd,
                   "--cacert", "/usr/local/podmanager/rackd-service/crt/ca.crt",
                   "-k", "-i", f"https://{bmc_ip}:50080/redfish/v1/SessionService/Sessions"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            log("INFO", self.check_id, f"  -> curl返回码: {result.returncode}")
            if result.returncode == 0:
                token_match = re.search(r'X-auth-token:\s*(\S+)', result.stdout + result.stderr, re.IGNORECASE)
                if token_match:
                    self.token = token_match.group(1)
                    log("INFO", self.check_id, f"  -> 获取到token: {self.token[:8]}...")
            return result.returncode == 0
        except Exception as e:
            log("WARN", self.check_id, f"  -> 异常: {e}")
            return False

    def _check_podmanager_trust_certs(self, blade_id: str) -> bool:
        """检查PoDManager信任证书链 (3.2.1-3.2.4)"""
        try:
            username = self._pod_manager_username
            password = self._pod_manager_password
            pod_manager_ip = self._pod_manager_ip

            if not username or not password:
                log("WARN", self.check_id, "  -> PoDManager凭据为空")
                return False

            log("INFO", self.check_id, "  -> [3.2.1] 查询PoDManager信任证书列表")
            cmd = ["curl", "-X", "GET",
                "-u", f"{username}:{password}",
                f"https://{pod_manager_ip}/redfish/v1/Managers/SMN1/Certificates", "-k"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                log("WARN", self.check_id, f"  -> [3.2.1] 查询失败: {result.stderr[:100]}")
                return False

            cert_ids = re.findall(r'"@odata.id":\s*"[^"]*Certificates/([^"]+)"', result.stdout)
            if not cert_ids:
                log("WARN", self.check_id, "  -> [3.2.1] 未找到证书ID")
                return False
            log("INFO", self.check_id, f"  -> [3.2.1] 找到 {len(cert_ids)} 个证书")

            root_ca_1_found = False
            computing_ca_2_found = False

            for cert_id in cert_ids:
                log("INFO", self.check_id, f"  -> [3.2.2] 遍历证书 {cert_id}")
                cmd = ["curl", "-X", "GET",
                    "-u", f"{username}:{password}",
                    f"https://{pod_manager_ip}/redfish/v1/Managers/SMN1/Certificates/{cert_id}", "-k"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    continue

                issuer_match = re.search(r'"Issuer":\s*"(/C=CN/O=Huawei/CN=Huawei RSA-PSS Equipment Root CA 1)"', result.stdout)
                subject_match = re.search(r'"Subject":\s*"(/C=CN/O=Huawei/CN=[^"]+)"', result.stdout)
                
                if not issuer_match:
                    issuer_match = re.search(r'"Issuer":\s*\{[^}]*"CommonName":\s*"Huawei RSA-PSS Equipment Root CA 1"', result.stdout)
                if not subject_match:
                    subject_match = re.search(r'"Subject":\s*\{[^}]*"CommonName":\s*"([^"]+)"', result.stdout)

                # 根据Issuer值判断证书类型
                if issuer_match:
                    issuer_value = issuer_match.group(1)
                    log("INFO", self.check_id, f"  -> [3.2.3] 证书{cert_id} Issuer: {issuer_value}")
                    
                    if subject_match:
                        subject_value = subject_match.group(1)
                        log("INFO", self.check_id, f"  -> [3.2.3] 证书{cert_id} Subject: {subject_value}")
                        
                        if "Huawei RSA-PSS Equipment Root CA 1" in issuer_value and "Huawei RSA-PSS Equipment Root CA 1" in subject_value:
                            root_ca_1_found = True
                            log("INFO", self.check_id, f"  -> [3.2.3] 找到Root CA 1自签名证书")
                        elif "Huawei RSA-PSS Equipment Root CA 1" in issuer_value and "Huawei Computing RSA-PSS Equipment CA 2" in subject_value:
                            computing_ca_2_found = True
                            log("INFO", self.check_id, f"  -> [3.2.3] 找到由Root CA 1签发的Computing CA 2")

                # 3.2.4: 检查证书有效期
                valid_not_before_match = re.search(r'"ValidNotBefore":\s*"([^"]+)"', result.stdout)
                valid_not_after_match = re.search(r'"ValidNotAfter":\s*"([^"]+)"', result.stdout)
                if valid_not_before_match and valid_not_after_match:
                    log("INFO", self.check_id, f"  -> [3.2.4] 证书{cert_id}有效期: {valid_not_before_match.group(1)} ~ {valid_not_after_match.group(1)}")

            if not (root_ca_1_found and computing_ca_2_found):
                log("WARN", self.check_id, f"  -> [3.2.3] 未找到所需的全部证书: RootCA1={root_ca_1_found}, ComputingCA2={computing_ca_2_found}")
                return False

            log("INFO", self.check_id, "  -> [3.2.3] PoDManager信任证书检查通过")
            return True
        except Exception as e:
            log("WARN", self.check_id, f"  -> 异常: {e}")
            return False

    def _check_podmanager_south_cert(self, blade_id: str) -> bool:
        """检查PoDManager南向身份证书 (3.2.5-3.2.7)"""
        try:
            username = self._pod_manager_username
            password = self._pod_manager_password
            pod_manager_ip = self._pod_manager_ip

            if not username or not password:
                log("WARN", self.check_id, "  -> PoDManager凭据为空")
                return False

            log("INFO", self.check_id, "  -> [3.2.5] 查询南向身份证书")
            cmd = ["curl", "-X", "GET",
                "-u", f"{username}:{password}",
                f"https://{pod_manager_ip}/redfish/v1/Managers/SMN1/SecurityService/HttpsSouthCert", "-k"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                log("WARN", self.check_id, f"  -> [3.2.5] 查询失败: {result.stderr[:100]}")
                return False

            log("INFO", self.check_id, "  -> [3.2.6] 检查南向身份证书subject")
            subject_match = re.search(r'"Subject":\s*"([^"]+)"', result.stdout)
            if subject_match:
                subject = subject_match.group(1)
                expected_subject = "/C=CN/O=Huawei/CN=podmanager"
                if subject != expected_subject:
                    log("WARN", self.check_id, f"  -> [3.2.6] subject不匹配: {subject}")
                    return False
                log("INFO", self.check_id, f"  -> [3.2.6] subject正确: {subject}")

            log("INFO", self.check_id, "  -> [3.2.7] 检查南向身份证书有效期")
            valid_not_before_match = re.search(r'"ValidNotBefore":\s*"([^"]+)"', result.stdout)
            valid_not_after_match = re.search(r'"ValidNotAfter":\s*"([^"]+)"', result.stdout)
            if valid_not_before_match and valid_not_after_match:
                log("INFO", self.check_id, f"  -> [3.2.7] 证书有效期: {valid_not_before_match.group(1)} ~ {valid_not_after_match.group(1)}")

            return True
        except Exception as e:
            log("WARN", self.check_id, f"  -> 异常: {e}")
            return False

    def _check_bmc_ssl_cert(self, blade_id: str, bmc_ip: str) -> bool:
        """检查BMC SSL证书 (3.2.9-3.2.12)"""
        try:
            cred = self.config.bmc_credential
            if not cred or not cred.decrypted_password:
                log("WARN", self.check_id, "  -> 凭据为空或密码未解密")
                return False

            log("INFO", self.check_id, f"  -> [3.2.9] 查询BMC {bmc_ip} 的SSL证书")
            cmd = ["curl", "-X", "GET",
                "-u", f"{cred.username}:{cred.decrypted_password}",
                f"https://{bmc_ip}/redfish/v1/Managers/1/SecurityService/HttpsCert", "-k"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                log("WARN", self.check_id, f"  -> [3.2.9] 查询失败: {result.stderr[:100]}")
                return False

            log("INFO", self.check_id, "  -> [3.2.10] 检查BMC SSL证书subject")
            subject_match = re.search(r'"Subject":\s*"([^"]+)"', result.stdout)
            if subject_match:
                subject = subject_match.group(1)
                expected_subject = "CN=podmanager, OU=, O=Huawei, L=, S=, C=CN"
                if subject != expected_subject:
                    log("WARN", self.check_id, f"  -> [3.2.10] subject不匹配: {subject}")
                    return False
                log("INFO", self.check_id, f"  -> [3.2.10] subject正确: {subject}")

            log("INFO", self.check_id, f"  -> [3.2.11] 查询BMC {bmc_ip} 当前时间")
            cmd = ["curl", "-X", "GET",
                "-u", f"{cred.username}:{cred.decrypted_password}",
                f"https://{bmc_ip}/redfish/v1/Managers/1", "-k"]
            result_bmc_time = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result_bmc_time.returncode == 0:
                datetime_match = re.search(r'"DateTime":\s*"([^"]+)"', result_bmc_time.stdout)
                if datetime_match:
                    log("INFO", self.check_id, f"  -> [3.2.11] BMC时间: {datetime_match.group(1)}")

            log("INFO", self.check_id, "  -> [3.2.12] 检查BMC SSL证书有效期")
            valid_not_before_match = re.search(r'"ValidNotBefore":\s*"([^"]+)"', result.stdout)
            valid_not_after_match = re.search(r'"ValidNotAfter":\s*"([^"]+)"', result.stdout)
            if valid_not_before_match and valid_not_after_match:
                log("INFO", self.check_id, f"  -> [3.2.12] 证书有效期: {valid_not_before_match.group(1)} ~ {valid_not_after_match.group(1)}")

            return True
        except Exception as e:
            log("WARN", self.check_id, f"  -> 异常: {e}")
            return False

    def _check_bmc_ca_certs(self, blade_id: str, bmc_ip: str) -> bool:
        """检查BMC CA证书 (3.2.13-3.2.16)"""
        try:
            cred = self.config.bmc_credential
            if not cred or not cred.decrypted_password:
                log("WARN", self.check_id, "  -> 凭据为空或密码未解密")
                return False

            log("INFO", self.check_id, f"  -> [3.2.13] 获取BMC {bmc_ip} 的CA证书列表")
            cmd = ["curl", "-X", "GET",
                "-u", f"{cred.username}:{cred.decrypted_password}",
                f"https://{bmc_ip}/redfish/v1/Managers/1/Certificates", "-k"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                log("WARN", self.check_id, f"  -> [3.2.13] 查询失败: {result.stderr[:100]}")
                return False

            cert_ids = re.findall(r'"@odata.id":\s*"[^"]*Certificates/([^"]+)"', result.stdout)
            if not cert_ids:
                log("WARN", self.check_id, "  -> [3.2.13] 未找到证书ID")
                return False
            log("INFO", self.check_id, f"  -> [3.2.13] 找到 {len(cert_ids)} 个CA证书")

            root_ca_1_found = False
            computing_ca_2_found = False

            for cert_id in cert_ids:
                log("INFO", self.check_id, f"  -> [3.2.14] 遍历CA证书 {cert_id}")
                cmd = ["curl", "-X", "GET",
                    "-u", f"{cred.username}:{cred.decrypted_password}",
                    f"https://{bmc_ip}/redfish/v1/Managers/1/Certificates/{cert_id}", "-k"]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode != 0:
                    continue

                issuer_cn_match = re.search(r'"Issuer":\s*\{[^}]*"CommonName":\s*"([^"]+)"', result.stdout)
                subject_cn_match = re.search(r'"Subject":\s*\{[^}]*"CommonName":\s*"([^"]+)"', result.stdout)

                if issuer_cn_match and issuer_cn_match.group(1) == "Huawei RSA-PSS Equipment Root CA 1":
                    log("INFO", self.check_id, f"  -> [3.2.15] CA证书{cert_id} Issuer: {issuer_cn_match.group(1)}")
                    if subject_cn_match:
                        subject_cn = subject_cn_match.group(1)
                        log("INFO", self.check_id, f"  -> [3.2.15] CA证书{cert_id} Subject: {subject_cn}")
                        if subject_cn == "Huawei RSA-PSS Equipment Root CA 1":
                            root_ca_1_found = True
                            log("INFO", self.check_id, f"  -> [3.2.15] 找到Root CA 1自签名证书")
                        elif subject_cn == "Huawei Computing RSA-PSS Equipment CA 2":
                            computing_ca_2_found = True
                            log("INFO", self.check_id, f"  -> [3.2.15] 找到由Root CA 1签发的Computing CA 2")

                valid_not_before_match = re.search(r'"ValidNotBefore":\s*"([^"]+)"', result.stdout)
                valid_not_after_match = re.search(r'"ValidNotAfter":\s*"([^"]+)"', result.stdout)
                if valid_not_before_match and valid_not_after_match:
                    log("INFO", self.check_id, f"  -> [3.2.16] CA证书{cert_id}有效期: {valid_not_before_match.group(1)} ~ {valid_not_after_match.group(1)}")

            if not (root_ca_1_found and computing_ca_2_found):
                log("WARN", self.check_id, f"  -> [3.2.15] 未找到所需的全部CA证书: RootCA1={root_ca_1_found}, ComputingCA2={computing_ca_2_found}")
                return False

            log("INFO", self.check_id, "  -> [3.2.15-16] BMC CA证书校验通过")
            return True
        except Exception as e:
            log("WARN", self.check_id, f"  -> 异常: {e}")
            return False

    def _check_superpod_config(self, blade_id: str, bmc_ip: str) -> bool:
        """对比PoDManager与BMC的超节点配置是否一致"""
        try:
            pod_superpod = self._get_pod_manager_superpod()
            bmc_superpod = self._get_bmc_superpod(bmc_ip)
            if not pod_superpod or not bmc_superpod:
                return True
            if pod_superpod != bmc_superpod:
                log("FAIL", self.check_id, f"超节点配置不一致: PoDManager={pod_superpod}, BMC={bmc_superpod}")
                return False
            return True
        except Exception:
            return True

    def _get_pod_manager_superpod(self) -> Optional[str]:
        try:
            cmd = ["curl", "-i", "-k", "-X", "GET", f"{POD_MANAGER_API_BASE}/redfish/v1/Chassis/SMN1"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            match = re.search(r'"SuperpodLabel"\s*:\s*"([^"]+)"', result.stdout)
            return match.group(1) if match else None
        except Exception:
            return None

    def _get_bmc_superpod(self, bmc_ip: str) -> Optional[str]:
        if not self.token:
            return None
        try:
            cmd = ["curl", "-i", "-k", "-X", "GET",
                   "-H", f"X-auth-token: {self.token}",
                   f"https://{bmc_ip}:443/redfish/v1/Chassis/1"]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            match = re.search(r'"SuperpodLabel"\s*:\s*"([^"]+)"', result.stdout)
            return match.group(1) if match else None
        except Exception:
            return None

class UbmChecker:
    """UBM纳管检查：Ping + UBM REST API状态 + SSH连接检查"""

    def __init__(self, config: PodManagerConfig, check_id: str = "UBM_CHECK"):
        self.config = config
        self.check_id = check_id
        self.network_checker = NetworkChecker(check_id)

    def _get_ubm_credential(self, slot_id: str) -> Optional[DeviceCredential]:
        """根据slot_id获取UBM凭据"""
        account_data = self.config.account_data
        ubm_data = account_data.get("ubm", {})

        slot_num = re.search(r'\d+', slot_id)
        if not slot_num:
            log("WARN", self.check_id, f"  -> 无法从{slot_id}提取slot编号")
            return None

        pwd_key = f"default_password_{slot_num.group()}"
        encrypted_password = ubm_data.get(pwd_key, "")

        if not encrypted_password:
            log("WARN", self.check_id, f"  -> 未找到{slot_id}对应的密码字段: {pwd_key}")
            return None

        username = ubm_data.get("default_user", "huawei")
        return DeviceCredential(username=username, encrypted_password=encrypted_password)

    def _decrypt_credential(self, cred: DeviceCredential) -> bool:
        """解密凭据"""
        if not cred or not cred.encrypted_password:
            return False
        if cred.decrypted_password:
            return True

        cred.decrypted_password = ConfigLoader(self.check_id).decrypt_password(cred.encrypted_password)
        return bool(cred.decrypted_password)

    def run_check(self, slot_id: str) -> bool:
        log("INFO", self.check_id, "=" * 40)
        log("INFO", self.check_id, f"开始检查UBM (槽位: {slot_id})")

        ubm_ip_tuple = self.config.ubm_ip_map.get(slot_id)
        if not ubm_ip_tuple:
            log("FAIL", self.check_id, f"未找到槽位 {slot_id} 的UBM IP")
            return False

        ubm_ip = ubm_ip_tuple[0]
        if not ubm_ip:
            log("FAIL", self.check_id, f"槽位 {slot_id} 的UBM IP为空")
            return False

        log("INFO", self.check_id, f"UBM IP: {ubm_ip}")

        ubm_cred = self._get_ubm_credential(slot_id)
        if ubm_cred:
            self._decrypt_credential(ubm_cred)
            self.config.ubm_credential = ubm_cred

        if not self._check_ping(ubm_ip):
            log("FAIL", self.check_id, f"UBM {ubm_ip} Ping不可达")
            return False

        ubmc_status_ok = self._check_ubmc_ubm_status(slot_id)
        ssh_status_ok = self._check_ssh_fallback(ubm_ip)

        if ubmc_status_ok and ssh_status_ok:
            log("PASS", self.check_id, f"UBM {ubm_ip} 纳管检查通过")
            return True
        else:
            if not ubmc_status_ok:
                log("FAIL", self.check_id, f"UBM {ubm_ip} 纳管状态异常 (4.2)")
            if not ssh_status_ok:
                log("FAIL", self.check_id, f"UBM {ubm_ip} SSH 连接失败 (4.3)")
            return False

    def _get_ubm_ip(self, slot_id: str) -> Optional[str]:
        ubm_config = self.config.ubm_ip_map.get(slot_id)
        return ubm_config[0] if ubm_config else None

    def _check_ping(self, ip: str) -> bool:
        return self.network_checker.ping(ip)

    def _check_ubmc_ubm_status(self, slot_id: str) -> bool:
        """查询UBMC REST API检查UBM connect-status"""
        try:
            cmd = ["curl", "-X", "GET",
                f"{LOCAL_API_BASE}/restconf/data/huawei-devm:devm/inventory/ubm-logical-entitys",
                "-H", "Accept: application/yang-data+xml",
                "-H", "Content-Type: application/yang-data+xml", "-v"]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                return False
            output = result.stdout + result.stderr
            pattern = rf'<ubm-logical-entity>.*?<position>{slot_id}</position>.*?<device-status>connected</device-status>.*?</ubm-logical-entity>'
            if re.search(pattern, output, re.DOTALL):
                return True
            return False

        except Exception as e:
            return False

    def _check_ssh_fallback(self, ip: str) -> bool:
        """SSH连接检查"""
        cred = self.config.ubm_credential
        if not cred:
            return False
        if not cred.decrypted_password:
            cred.decrypted_password = ConfigLoader(self.check_id).decrypt_password(cred.encrypted_password)
        if not cred.decrypted_password:
            return False
        return self.network_checker.ssh_connect(
            ip=ip, 
            username=cred.username, 
            password=cred.decrypted_password
        )

def pod_manager_check(args):
    """PoDManager纳管条件检查主入口"""
    CHECK_ID = "POD_MANAGER"
    
    log("INFO", CHECK_ID, "=" * 60)
    log("INFO", CHECK_ID, "开始PoDManager纳管条件检查")
    log("INFO", CHECK_ID, "=" * 60)
    
    # 加载配置
    loader = ConfigLoader(CHECK_ID)
    config = loader.build_pod_manager_config()
    
    if not config.bmc_ip_map:
        log("FAIL", CHECK_ID, "未找到任何BMC IP映射")
        return {"bmc": {}, "meswitch": {}, "ubm": {}}
    
    log("INFO", CHECK_ID, f"加载到 {len(config.bmc_ip_map)} 个BMC IP映射")
    log("INFO", CHECK_ID, f"加载到 {len(config.ubm_ip_map)} 个UBM IP映射")
    
    # 创建各检查器
    meswitch_checker = MeswitchChecker(config, CHECK_ID)
    bmc_checker = BmcChecker(config, CHECK_ID)
    ubm_checker = UbmChecker(config, CHECK_ID)
    
    results = {"bmc": {}, "meswitch": {}, "ubm": {}}
    
    # MeSwitch检查（仅361槽位）
    if "361" in config.ubm_ip_map:
        results["meswitch"]["361"] = meswitch_checker.run_check("361")
    
    # BMC检查（遍历所有blade）
    for blade_id in config.bmc_ip_map.keys():
        results["bmc"][blade_id] = bmc_checker.run_check(blade_id)
    
    # UBM检查（遍历所有slot）
    for slot_id in config.ubm_ip_map.keys():
        results["ubm"][slot_id] = ubm_checker.run_check(slot_id)
    
    # 汇总结果
    bmc_pass = sum(1 for v in results["bmc"].values() if v)
    meswitch_pass = sum(1 for v in results["meswitch"].values() if v)
    ubm_pass = sum(1 for v in results["ubm"].values() if v)
    
    log("INFO", CHECK_ID, "=" * 60)
    log("INFO", CHECK_ID, f"检查完成: BMC检查成功数量:{bmc_pass}/{len(results['bmc'])}, MeSwitch检查成功数量:{meswitch_pass}/{len(results['meswitch'])}, UBM检查成功数量:{ubm_pass}/{len(results['ubm'])}")
    log("INFO", CHECK_ID, "=" * 60)
    
    return results

if __name__ == "__main__":
    print("=" * 60)
    print("PoDManager纳管条件检查")
    print("=" * 60)
    class Args:
        pass
    pod_manager_check(Args())