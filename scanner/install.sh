#!/bin/sh
set -e
echo ''
echo '  ■ ZERO OS Installer'
echo ''
# Check Python 3.10+
python3 -c 'import sys; assert sys.version_info >= (3, 10), f"Python 3.10+ required, got {sys.version}"' || { echo 'Python 3.10+ required'; exit 1; }
# Install via pip
pip install zeroos
echo ''
echo '  ✓ Installed: zeroos'
echo ''
echo '  Run: zeroos init'
echo ''
