"""Read-only PNG/PDF/SVG inspection artifacts; SVG opened in a Qt SVG widget."""
from pathlib import Path
import argparse
import subprocess
import xml.etree.ElementTree as ET
from PySide6.QtWidgets import QApplication
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtCore import Qt
from das_workbench.contracts import atomic_json,read_json
from PIL import Image


def main(root):
    root=Path(root);app=QApplication.instance() or QApplication([]);reports={}
    for case in ['D01','D02','D03','D04']:
        bundle=root/'real'/(case+'_bundle')
        png=bundle/'overview.png';pdf=bundle/'overview.pdf';svg=bundle/'overview.svg'
        with Image.open(png) as im:shape=list(im.size)
        assert shape==[2952,2185]
        subprocess.run(['pdftoppm','-scale-to','1800','-png','-singlefile',str(pdf),str(root/(case+'_pdf_render'))],check=True)
        info=subprocess.run(['pdfinfo',str(pdf)],capture_output=True,text=True,check=True).stdout
        (root/(case+'_pdf_info.txt')).write_text(info)
        tree=ET.parse(svg);ns={'s':'http://www.w3.org/2000/svg'}
        assert tree.findall('.//s:image',ns) and tree.findall('.//s:text',ns)
        widget=QSvgWidget(str(svg));widget.setStyleSheet('background: white');widget.resize(1500,1110);widget.show();app.processEvents()
        assert widget.renderer().isValid()
        widget.grab().save(str(root/(case+'_svg_open.png')));widget.close();app.processEvents()
        reports[case]=dict(png_pixels=shape,physical_size_cm=[25,18.5],dpi=300,pdf_independent_renderer='Poppler pdftoppm; page raster inspected separately',svg_open='PySide6 QSvgWidget opened full SVG, valid renderer and saved actual widget screenshot',svg_text_elements=len(tree.findall('.//s:text',ns)),svg_raster_images=len(tree.findall('.//s:image',ns)),visual_review='pending explicit image inspection')
    atomic_json(root/'visual_qa.json',reports)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root');main(parser.parse_args().root)
