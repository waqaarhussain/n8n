"""Bounded local extraction for email documents. Never downloads URLs from email."""
import base64
import binascii
import re
import os
import time
import resource
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree

MAX_FILE = 10 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_TEXT = 120_000
MAX_FILES = 10
MAX_PAGES = 30


def command(args, timeout=30):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=timeout).stdout.decode('utf-8', errors='replace')


def extract_pdf(path):
    info = command(['pdfinfo', str(path)])
    match = re.search(r'^Pages:\s+(\d+)', info, re.M)
    if not match or not 1 <= int(match.group(1)) <= MAX_PAGES:
        raise ValueError('PDF page limit exceeded')
    result = []
    for number in range(1, int(match.group(1)) + 1):
        text = command(['pdftotext', '-layout', '-f', str(number), '-l', str(number), str(path), '-'])
        if len(re.sub(r'\W', '', text)) < 20:
            target = path.parent / f'ocr-{number}'
            command(['pdftoppm', '-f', str(number), '-l', str(number), '-scale-to', '2400',
                     '-singlefile', '-png', str(path), str(target)])
            text = command(['tesseract', str(target) + '.png', 'stdout', '-l', 'eng'], timeout=40)
        if not text.strip():
            raise ValueError('PDF page has no readable text')
        result.append(f'Page {number}\n{text.strip()}')
        if sum(map(len, result)) > MAX_TEXT:
            raise ValueError('Extracted text too long')
    return '\n\n'.join(result)


def extract_docx(path):
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 500 or sum(item.file_size for item in entries) > 30 * 1024 * 1024:
            raise ValueError('Word document expansion limit exceeded')
        documents = [n for n in archive.namelist() if re.fullmatch(r'word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml', n)]
        if 'word/document.xml' not in documents:
            raise ValueError('Not a Word document')
        result = []
        for name in documents:
            root = ElementTree.fromstring(archive.read(name))
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            for paragraph in root.iter(ns + 'p'):
                value = ''.join(node.text or '' for node in paragraph.iter(ns + 't'))
                if value.strip():
                    result.append(value)
        # Embedded image-only invitations require review, rather than claiming they were read.
        if not result:
            raise ValueError('Word document has no readable text')
        return '\n'.join(result)


def worker(kind, path):
    # Limits apply to this isolated parser process and its child tools.
    os.environ['OMP_THREAD_LIMIT'] = '1'
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    if kind == 'pdf':
        text = extract_pdf(path)
    elif kind == 'docx':
        text = extract_docx(path)
    else:
        text = command(['antiword', '-m', 'UTF-8.txt', str(path)])
    if not text.strip() or len(text) > MAX_TEXT:
        raise ValueError('No usable document text or text limit exceeded')
    print(text)


def extract_attachments(data):
    items = data.get('attachments', [])
    issues = list(data.get('attachment_issues', []))
    if not isinstance(items, list) or len(items) > MAX_FILES:
        return '', ['Too many attachments or invalid attachment payload; review manually.']
    sections = []
    deadline = time.monotonic() + 90
    total = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            issues.append('Invalid attachment metadata; review manually.')
            continue
        name = Path(str(item.get('filename', f'document-{index}'))).name[:180]
        kind = Path(name).suffix.lower().lstrip('.')
        if kind not in ('pdf', 'doc', 'docx'):
            issues.append(f'Unsupported attachment: {name}. Review manually.')
            continue
        try:
            encoded = item.get('data', '')
            if not isinstance(encoded, str) or len(encoded) > (MAX_FILE * 4 // 3 + 8):
                raise ValueError('Attachment too large')
            raw = base64.b64decode(encoded, validate=True)
            total += len(raw)
            if not raw or len(raw) > MAX_FILE or total > MAX_TOTAL:
                raise ValueError('Attachment byte limit exceeded')
            with tempfile.TemporaryDirectory(prefix='school-document-') as directory:
                path = Path(directory) / ('input.' + kind)
                path.write_bytes(raw)
                output = Path(directory) / 'extracted.txt'
                with output.open('wb') as target:
                    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), kind, str(path)],
                                            stdout=target, stderr=subprocess.DEVNULL, timeout=max(1, min(45, deadline - time.monotonic())))
                if result.returncode != 0 or output.stat().st_size > MAX_TEXT * 4:
                    raise ValueError('Attachment extraction failed')
                text = output.read_text().strip()
                if not text or len(text) > MAX_TEXT:
                    raise ValueError('No usable attachment text')
            sections.append(f'Attachment: {name}\n{text}')
            if sum(map(len, sections)) > MAX_TEXT:
                return '', issues + ['Combined attachment text limit exceeded; review manually.']
        except (ValueError, OSError, subprocess.SubprocessError, binascii.Error):
            issues.append(f'Could not fully read {name}. It may be protected, corrupt, image-only or over the processing limits. Review manually.')
    return '\n\n'.join(sections), issues


if __name__ == '__main__':
    try:
        worker(sys.argv[1], Path(sys.argv[2]))
    except Exception:
        raise SystemExit(2) from None
