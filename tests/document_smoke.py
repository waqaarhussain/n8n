"""Exercise real PDF text extraction, scanned-PDF OCR, and Word XML extraction."""
import base64
import io
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from zipfile import ZipFile
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
try:
    import attachments
except ImportError:
    sys.path.insert(0, '/app')
    import attachments


def pdf(objects):
    data = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for number, content in enumerate(objects, 1):
        offsets.append(len(data))
        data += str(number).encode() + b' 0 obj\n' + content + b'\nendobj\n'
    xref = len(data)
    data += f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    for offset in offsets[1:]:
        data += f'{offset:010d} 00000 n \n'.encode()
    data += f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()
    return bytes(data)


def text_pdf():
    content = b'BT /F1 24 Tf 30 100 Td (School workshop 10 December 2026 at 2.15pm) Tj ET'
    return pdf([b'<< /Type /Catalog /Pages 2 0 R >>',
                b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 650 180] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                f'<< /Length {len(content)} >>\nstream\n'.encode()+content+b'\nendstream'])


def docx_bytes():
    output = io.BytesIO()
    with ZipFile(output, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>School workshop 10 December 2026 at 2.15pm</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>End time 3.30pm</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    return output.getvalue()


def scanned_pdf():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'original.pdf'
        path.write_bytes(text_pdf())
        output = Path(directory) / 'scan'
        subprocess.run(['pdftoppm', '-r', '160', '-singlefile', str(path), str(output)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        data = output.with_suffix('.ppm').read_bytes()
        header = re.match(rb'P6\s+(\d+)\s+(\d+)\s+255\s', data)
        assert header
        width, height = int(header[1]), int(header[2])
        image = zlib.compress(data[header.end():])
        content = b'q 650 0 0 180 0 0 cm /Im1 Do Q'
        return pdf([b'<< /Type /Catalog /Pages 2 0 R >>',
                    b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                    b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 650 180] /Resources << /XObject << /Im1 4 0 R >> >> /Contents 5 0 R >>',
                    f'<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(image)} >>\nstream\n'.encode()+image+b'\nendstream',
                    f'<< /Length {len(content)} >>\nstream\n'.encode()+content+b'\nendstream'])


def main():
    for name, raw in [('invite.docx', docx_bytes()), ('invite.pdf', text_pdf()), ('scan.pdf', scanned_pdf())]:
        text, issues = attachments.extract_attachments({'attachments': [{'filename': name, 'data': base64.b64encode(raw).decode()}]})
        assert not issues, (name, issues)
        assert '10December2026' in re.sub(r'\s+', '', text), (name, text)
        print('Document extraction passed: ' + name)


if __name__ == '__main__':
    main()
