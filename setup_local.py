"""Create a private local demo configuration without printing passwords."""
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent
path = root / '.env'
try:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except FileExistsError:
    print('保留已有 .env，未覆盖。')
else:
    with os.fdopen(fd, 'w') as file:
        file.write('ADMIN_USERNAME=admin\nADMIN_PASSWORD='+secrets.token_urlsafe(24)+'\nTG_BOT_TOKEN=\nDEMO=1\nDATABASE_PATH=data/demo.sqlite3\nHOST=127.0.0.1\nPORT=8000\nCOOKIE_SECURE=0\n')
    print('已生成仅当前用户可读的 .env；请在本地查看或修改管理密码。默认演示模式，不连接 Telegram。')
