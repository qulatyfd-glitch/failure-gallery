# -*- coding: utf-8 -*-
"""
图床上传模块（标准库实现，零依赖）
====================================
提供外部图片存储，避免把用户上传的图片存在服务器本地
（免费托管平台的文件可能随重启丢失）。

用法：
    在环境变量里设置 IMAGE_HOST 即可启用外部图床：
      - IMAGE_HOST=catbox   -> 使用 Catbox（免费，无需注册，匿名上传）
    不设置时，退回本地存储（static/uploads 等）。

保存图片时返回一个"可访问的 URL 字符串"。
本地模式返回文件名；外部模式返回完整 http URL。
"""

import os
import io
import uuid
import mimetypes

IMAGE_HOST = os.environ.get("IMAGE_HOST", "").strip().lower()
# 可选：Catbox 匿名上传不需要密钥；Imgur 需要 client id（这里暂不实现）
CATBOX_UPLOAD_URL = "https://catbox.moe/user/api.php"


def _catbox_upload(data: bytes, filename: str) -> str:
    """用 multipart/form-data 上传到 Catbox，返回图片 URL。"""
    boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
    reqtype = "fileupload"
    body = io.BytesIO()

    def add_field(name, value):
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.write(f"{value}\r\n".encode())

    add_field("reqtype", reqtype)
    body.write(f"--{boundary}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="fileToUpload"; filename="{filename}"\r\n'.encode())
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body.write(f"Content-Type: {ctype}\r\n\r\n".encode())
    body.write(data)
    body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())

    import urllib.request
    req = urllib.request.Request(
        CATBOX_UPLOAD_URL,
        data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8").strip()


def upload_to_host(data: bytes, original_filename: str) -> str:
    """上传图片到外部图床（若启用），否则返回空字符串表示未启用。

    返回：外部图片 URL（如 https://files.catbox.moe/xxx.png）
    未启用外部存储时返回 ""。
    """
    if not IMAGE_HOST:
        return ""
    if IMAGE_HOST == "catbox":
        return _catbox_upload(data, original_filename)
    # 其他平台暂未实现，退回本地
    return ""


def is_external_url(filename_or_url: str) -> bool:
    """判断这个图片引用是不是外部 URL（http/https）。"""
    return bool(filename_or_url) and filename_or_url.startswith(("http://", "https://"))
