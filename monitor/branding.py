"""Local console appearance, independent of advertising account settings."""
import base64
import binascii
import copy
import json
import os
import struct
import threading

DEFAULT_BRANDING={'name':'千川巡航','subtitle':'多账户调控台','icon_data_url':None}
MAX_ICON_BYTES=256*1024


def normalize(value):
    if not isinstance(value,dict) or set(value)!=set(DEFAULT_BRANDING):raise ValueError('请填写名称、副标题和图标')
    out={}
    for key,limit in (('name',32),('subtitle',48)):
        text=value[key]
        if not isinstance(text,str) or any(ord(ch)<32 or ord(ch)==127 for ch in text):raise ValueError('名称和副标题请使用单行文字')
        text=text.strip()
        if len(text)>limit or key=='name' and not text:raise ValueError('名称须为1–32个字，副标题最多48个字')
        out[key]=text
    icon=value['icon_data_url']
    if icon is not None:
        prefix='data:image/png;base64,'
        if not isinstance(icon,str) or not icon.startswith(prefix) or len(icon)>MAX_ICON_BYTES*4//3+100:raise ValueError('请上传PNG、JPG或WebP图标，由页面压缩后保存')
        try:raw=base64.b64decode(icon[len(prefix):],validate=True)
        except (ValueError,binascii.Error):raise ValueError('图标数据无效') from None
        if len(raw)>MAX_ICON_BYTES or len(raw)<45 or raw[:8]!=b'\x89PNG\r\n\x1a\n' or raw[8:16]!=b'\x00\x00\x00\rIHDR' or raw[-12:]!=b'\x00\x00\x00\x00IEND\xaeB`\x82':raise ValueError('图标须为完整PNG图片')
        width,height=struct.unpack('>II',raw[16:24])
        if not 1<=width<=512 or not 1<=height<=512:raise ValueError('保存图标的尺寸须在512×512以内')
    out['icon_data_url']=icon
    return out


class BrandingStore:
    def __init__(self,path):
        self.path=path;self.lock=threading.RLock()

    def get(self):
        with self.lock:
            try:return normalize(json.loads(self.path.read_text(encoding='utf-8')))
            except (OSError,ValueError):return copy.deepcopy(DEFAULT_BRANDING)

    def save(self,value):
        saved=normalize(value)
        with self.lock:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(saved,ensure_ascii=False),encoding='utf-8')
            os.replace(temporary,self.path)
        return saved
