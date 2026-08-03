#!/bin/bash
set -euo pipefail

rm -rf build dist bigTpuProfile.egg-info bigTpuProfile.tar.gz
pip3 install --upgrade pip wheel setuptools_scm setuptools
python3 setup.py sdist bdist_wheel --plat-name manylinux1_x86_64
rm -rf dist/*.gz
wheel_file=$(find dist -maxdepth 1 -name "*.whl" -print -quit)
python3 -m zipfile -l "$wheel_file" | grep -q "bigTpuProfile/bmprofile_perfAI_2260.py"
cp -rf doc/* dist/
tar czvf bigTpuProfile.tar.gz dist
