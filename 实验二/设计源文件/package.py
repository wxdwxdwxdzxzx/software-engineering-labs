from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parent.parent
FOLDER = '实验2_241220020_王旭东'
OUT = ROOT / f'{FOLDER}.zip'

sources = [(BASE/'实验报告.pdf','实验报告.pdf')]
for file in sorted((BASE/'UML').glob('*')):
    if file.suffix in ('.png','.puml'):
        sources.append((file,f'UML/{file.name}'))
for file in sorted((BASE/'UI界面').glob('*.png')):
    sources.append((file,f'UI界面/{file.name}'))

assert len([x for x,_ in sources if x.suffix=='.puml'])==4
assert len([x for x,_ in sources if x.suffix=='.png' and x.parent.name=='UML'])==4
assert len([x for x,_ in sources if x.suffix=='.png' and x.parent.name=='UI界面'])==12

with ZipFile(OUT,'w',ZIP_DEFLATED,compresslevel=9) as z:
    for file,arc in sources:
        z.write(file,f'{FOLDER}/{arc}')
with ZipFile(OUT) as z:
    assert z.testzip() is None
    assert z.namelist()==[f'{FOLDER}/{arc}' for _,arc in sources]
    for file,arc in sources:
        assert z.read(f'{FOLDER}/{arc}')==file.read_bytes()
print(OUT)
print('file_count=',len(sources))
