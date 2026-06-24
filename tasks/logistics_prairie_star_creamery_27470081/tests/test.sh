#!/bin/bash
pip install openpyxl pdfplumber python-docx 2>/dev/null
python /tests/sop_verifier.py
