"""Read-only PDF font/physical-size structure audit, requires pypdf."""
import json,sys
from pathlib import Path
from pypdf import PdfReader
b=Path(sys.argv[1]);out={}
for case in ['F01','F02','F03','F04','S01']:
    reader=PdfReader(b/'examples'/case/'figure.pdf');p=reader.pages[0];fonts=[]
    for ref in p['/Resources']['/Font'].values():
        f=ref.get_object();objects=[f]+[d.get_object() for d in f.get('/DescendantFonts',[])];embedded=False
        for obj in objects:
            if '/FontDescriptor' in obj:embedded |= any(k in obj['/FontDescriptor'].get_object() for k in ('/FontFile','/FontFile2','/FontFile3'))
        fonts.append(dict(name=str(f.get('/BaseFont')),embedded=embedded))
    assert fonts and all(x['embedded'] for x in fonts)
    dims=[float(p.mediabox.width)*2.54/72,float(p.mediabox.height)*2.54/72];assert abs(dims[0]-25)<1e-6 and abs(dims[1]-(23 if case=='F04' else 20))<1e-6
    txt=p.extract_text();assert len(txt)>150 and len(reader.pages)==1
    out[case]=dict(passed=True,fonts=fonts,physical_size_cm=dims,vector_text=True,pages=1)
(b/'reports/pdf_structure.json').write_text(json.dumps(out,indent=2))
