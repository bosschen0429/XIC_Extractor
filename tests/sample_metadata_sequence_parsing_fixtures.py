"""Helpers shared by test_sample_metadata_sequence_parsing and other test modules."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


def _create_synthetic_docx(path: Path, *, index_header: str = "ID") -> None:
    """Create a minimal synthetic docx with a sequence table and a remark table."""
    xml_content = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Parameter</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Col Temp</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>40 C</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>ID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Sample</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Storage</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>999</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Inventory only</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Freezer A</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>ID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>File Name</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Instrument Method</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Sample</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Inj</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Remark: Start of Batch 1</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Blank_1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Method_Blank</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>DIW</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>20</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>2</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>QC_Pool_1</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Method_CID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Pooled QC</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>10</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>3</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Tumor tissue BC001_DNA</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Method_CID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>BC001 Tumor Tissue + ISTDs</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>20</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>4</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Normal tissue BC001_DNA</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Method_CID</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>BC001 Normal Tissue + ISTDs</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>20</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>"""

    xml_content = xml_content.replace("<w:t>ID</w:t>", f"<w:t>{index_header}</w:t>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml_content)
    path.write_bytes(buf.getvalue())
