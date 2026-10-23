#!/usr/bin/env python3
"""Compatibility entrypoint; the sibling canonical skill must be installed."""
from pathlib import Path
import runpy
import sys

script = Path(__file__).resolve().parents[2] / 'ntu-paper-batch-download' / 'scripts' / 'zotero_connector_batch.py'
if not script.is_file():
    sys.exit('Install the sibling ntu-paper-batch-download skill before using this compatibility entrypoint.')
runpy.run_path(str(script), run_name='__main__')
