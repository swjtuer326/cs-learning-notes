#!/usr/bin/python3
# ==============================================================================
#
# Copyright (C) 2022 sophon Technologies Inc.  All rights reserved.
#
# TPU-MLIR is licensed under the 2-Clause BSD License except for the
# third-party components.
#
# ==============================================================================

import sys
from ._version import version as __version__

# Generated profile definitions still import these historical top-level names.
# Alias them to the package modules without modifying sys.path or loading a
# second copy of the same modules.
from . import debugger as _debugger
from . import profile_helper as _profile_helper

sys.modules.setdefault("debugger", _debugger)
sys.modules.setdefault("profile_helper", _profile_helper)
