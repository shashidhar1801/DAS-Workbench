"""Independent PDF render and native SVG opening for app-exported figures."""
from pathlib import Path
import json,subprocess,sys,xml.etree.ElementTree as ET
from PySide6.QtWidgets import QApplication
from PySide6.QtSvgWidgets import QSvgWidget
from PIL import Image


def main(root):
    b=Path(root);app=QApplication.instance() or QApplication([]);results={};out=b/'reports/visual';out.mkdir(parents=True,exist_ok=True)
    for case in ['F01','F02','F03','F04','S01']:
        d=b/'examples'/case
        subprocess.run(['pdftoppm','-scale-to','1700','-singlefile','-png',str(d/'figure.pdf'),str(out/(case+'_pdf'))],check=True,capture_output=True)
        svg=QSvgWidget(str(d/'figure.svg'));svg.resize(1200,1100 if case=='F04' else 960);svg.show();app.processEvents();assert svg.renderer().isValid();svg.grab().save(str(out/(case+'_svg_open.png')));svg.close()
        tree=ET.parse(d/'figure.svg');ns={'s':'http://www.w3.org/2000/svg'};texts=tree.findall('.//s:text',ns);images=tree.findall('.//s:image',ns)
        assert texts and images
        with Image.open(d/'figure.png') as p:size=p.size
        results[case]=dict(svg_native_open=True,svg_vector_text_elements=len(texts),svg_raster_images=len(images),png_pixels=size,pdf_render=str((out/(case+'_pdf.png')).relative_to(b)),visual_inspection='pending')
    (b/'reports/visual_qa.json').write_text(json.dumps(results,indent=2))


if __name__=='__main__':main(sys.argv[1])
