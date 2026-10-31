"""Read-only PDF structure audit; run with a Python containing pypdf."""
from pathlib import Path
import json
import sys
from pypdf import PdfReader
root=Path(sys.argv[1]);results={}
for case in ['D01','D02','D03','D04']:
    reader=PdfReader(root/'real'/(case+'_bundle')/'overview.pdf')
    page=reader.pages[0];fonts=[]
    for key,ref in page['/Resources']['/Font'].items():
        font=ref.get_object();objects=[font]
        objects += [d.get_object() for d in font.get('/DescendantFonts',[])]
        embedded=False
        for obj in objects:
            descriptor=obj.get('/FontDescriptor')
            if descriptor:
                descriptor=descriptor.get_object()
                embedded |= any(k in descriptor for k in ['/FontFile','/FontFile2','/FontFile3'])
        fonts.append(dict(resource=str(key),base_font=str(font.get('/BaseFont')),embedded=embedded))
    assert fonts and all(f['embedded'] for f in fonts)
    text=page.extract_text()
    assert 'Per-channel phase RMS' in text and 'Vendor-processed optical phase' in text
    width=float(page.mediabox.width)*2.54/72;height=float(page.mediabox.height)*2.54/72
    assert abs(width-25)<1e-6 and abs(height-18.5)<1e-6
    images=[k for k,v in page['/Resources'].get('/XObject',{}).items() if v.get_object().get('/Subtype')=='/Image']
    assert images
    results[case]=dict(passed=True,fonts=fonts,physical_size_cm=[width,height],extractable_vector_text=True,raster_image_objects=list(map(str,images)),pages=len(reader.pages))
(root/'pdf_fonts.json').write_text(json.dumps(results,indent=2))
