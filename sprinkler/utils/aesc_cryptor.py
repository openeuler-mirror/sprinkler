from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
import os
import base64


class AESCryptor:
    def __init__(self):
        """
        初始化AES加密器
        :param key: 密钥（16/24/32字节长度）
        """
        self.key = b"sprinkleraesc2026052014345689abc"
        self.backend = default_backend()

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        加密数据
        :param plaintext: 明文数据（bytes）
        :return: 加密后的数据（包含IV）
        """
        iv = os.urandom(16)  # 生成16字节IV
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv), backend=self.backend)
        encryptor = cipher.encryptor()
        
        # PKCS7填充
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded_data = padder.update(plaintext) + padder.finalize()
        
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()
        return iv + ciphertext  # 返回IV+密文

    def decrypt(self, ciphertext: bytes) -> bytes:
        """
        解密数据
        :param ciphertext: 密文数据（包含IV）
        :return: 解密后的明文
        """
        iv = ciphertext[:16]  # 提取IV
        actual_ciphertext = ciphertext[16:]
        
        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv), backend=self.backend)
        decryptor = cipher.decryptor()
        
        padded_plaintext = decryptor.update(actual_ciphertext) + decryptor.finalize()
        
        # 去除填充
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
        return plaintext

# 使用示例
if __name__ == "__main__":
    cryptor = AESCryptor()
    
    # 加密示例
    original_data = input("请输入密码：")
    if not original_data:
        original_data = 'Huawei12#$'
    encode_data = original_data.encode("utf-8") 
    encrypted_data = base64.b64encode(cryptor.encrypt(encode_data)).decode("utf-8")
    print(f"加密后数据: {encrypted_data}")

    # 解密示例
    decrypted_data = cryptor.decrypt(base64.b64decode(encrypted_data))
    print(f"解密后数据: {decrypted_data.decode()}")
