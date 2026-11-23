"""Build a source-only delivery archive, excluding local observations and secrets."""
from pathlib import Path
import json
import os
import zipfile


def main():
    root=Path(__file__).resolve().parent.parent
    target=root.parent/'只读监测助手-源码交付包.zip'
    temp=target.with_suffix('.tmp')
    count=0
    with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            if not path.is_file():continue
            rel=path.relative_to(root)
            if any(p in {'data','__pycache__','.git','node_modules','evidence','screenshots'} for p in rel.parts):continue
            if path.suffix.lower() in {'.pyc','.log','.zip','.tmp'}:continue
            archive.write(path,Path(root.name)/rel)
            count+=1
    os.replace(temp,target)
    print(json.dumps({'archive':str(target),'files':count,'bytes':target.stat().st_size},ensure_ascii=True))


if __name__=='__main__':main()
