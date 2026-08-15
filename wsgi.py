# -*- coding: utf-8 -*-
"""
PythonAnywhere WSGI 入口
========================
把数据库和上传文件放到用户主目录（保证可写、持久），
然后导入 Flask 应用。

在 PythonAnywhere 的 WSGI 配置文件中写入：
    import sys
    path = '/home/你的用户名/failure-gallery'
    if path not in sys.path:
        sys.path.append(path)
    from wsgi import application
"""
import os

# 把数据目录放到用户主目录下（PythonAnywhere 上保证可写、不随重启丢失）
_home = os.path.expanduser("~")
_data_dir = os.path.join(_home, "failure-gallery-data")
os.makedirs(_data_dir, exist_ok=True)
os.environ.setdefault("DB_DIR", _data_dir)

# 生产环境密钥：请在 PythonAnywhere 里设置环境变量 SECRET_KEY
os.environ.setdefault("SECRET_KEY", "please-change-me-in-pythonanywhere")
# 生产环境关闭调试
os.environ.setdefault("FLASK_ENV", "production")

from app import app as application

# 确保数据库表已创建（首次部署时）
from app import init_db
init_db()

if __name__ == "__main__":
    application.run(debug=False, port=8000)
