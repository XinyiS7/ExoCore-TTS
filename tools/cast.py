#!/usr/bin/env python.exe
"""Thin CLI entry point for the casting bench.

Must run inside this service's own environment (torch/CUDA live only there):

    E:/Miniconda3/envs/voxcpm_runtime/python.exe -m pip install -e .
    E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py --help
"""
import sys

try:
    from exocore_tts.casting import main
except ImportError as exc:  # wrong interpreter, or the package was never installed
    sys.exit(
        f"Cannot import exocore_tts ({exc}).\n"
        "Run casting with this service's own interpreter and install the package once:\n"
        "  E:/Miniconda3/envs/voxcpm_runtime/python.exe -m pip install -e .\n"
        "  E:/Miniconda3/envs/voxcpm_runtime/python.exe tools/cast.py --help"
    )

if __name__ == "__main__":
    raise SystemExit(main())
