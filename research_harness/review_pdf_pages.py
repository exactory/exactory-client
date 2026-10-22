"""Deterministic full-page PNG derivation from checked native PDF bytes."""

import hashlib
import shutil
import struct
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from .artifacts import describe_artifact
from .errors import ResearchError


def _require(condition, message):
    if not condition:
        raise ResearchError('review_visual_source_mismatch', message)


@lru_cache(maxsize=1)
def _profile():
    executable = shutil.which('pdftoppm')
    _require(executable is not None, 'Native PDF image delivery requires pdftoppm')
    binary = Path(executable).resolve()
    result = subprocess.run([str(binary), '-v'], capture_output=True, timeout=10, check=False)
    _require(result.returncode == 0, 'The PDF renderer version must be observable')
    return {'renderer': 'pdftoppm', 'version': (result.stderr or result.stdout).decode().splitlines()[0],
            'executable_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'dpi': 150,
            'scope': 'full-page', 'rotation': 'native', 'format': 'png', 'color': 'RGB'}


def profile():
    """Return a fresh profile so packet mutation cannot change renderer identity."""
    return dict(_profile())


def dimensions(data):
    _require(isinstance(data, bytes) and len(data) >= 33 and data[:8] == b'\x89PNG\r\n\x1a\n'
             and data[12:16] == b'IHDR', 'The rendered artifact must be a PNG image')
    width, height = struct.unpack('>II', data[16:24])
    _require(width > 0 and height > 0 and data[24:26] == bytes((8, 2)),
             'The native image must have nonempty RGB pixels with eight-bit color')
    return width, height


def render_bytes(data, page_index):
    _require(isinstance(data, bytes) and data.startswith(b'%PDF-') and type(page_index) is int and page_index >= 0,
             'Render a nonnegative page of exact PDF bytes')
    expected = profile()
    executable = Path(shutil.which('pdftoppm') or '').resolve()
    _require(executable.is_file() and hashlib.sha256(executable.read_bytes()).hexdigest() == expected['executable_sha256'],
             'The PDF renderer executable changed during this evaluation')
    with tempfile.TemporaryDirectory(prefix='exactory-pdf-page-') as directory:
        source, output = Path(directory) / 'source.pdf', Path(directory) / 'page'
        source.write_bytes(data)
        result = subprocess.run([str(executable), '-r', '150', '-f', str(page_index + 1), '-l', str(page_index + 1),
                                 '-singlefile', '-png', str(source), str(output)], capture_output=True,
                                timeout=60, check=False)
        _require(result.returncode == 0 and output.with_suffix('.png').is_file(),
                 'The exact native PDF page could not be rendered')
        rendered = output.with_suffix('.png').read_bytes()
    width, height = dimensions(rendered)
    return rendered, {'image': describe_artifact(rendered, 'image/png'), 'width': width, 'height': height,
                      'render_profile': dict(expected)}


def render(artifacts, original, page_index):
    """Publish the exact rendered image; the source descriptor is verified before rendering."""
    data, result = render_bytes(artifacts.read(original), page_index)
    _require(artifacts.put(data, 'image/png') == result['image'], 'The stored rendered image differs from its bytes')
    return result
