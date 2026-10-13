"""Cron pre-run wrapper for the intel-nuc-sentinel skill.

Cron runs only scripts inside this profile's scripts/ directory, so copy this file to
<HERMES_HOME>/scripts/intel_nuc_sentinel.py. It runs the skill's sentinel.py in `run` mode and
its stdout (the evidence report) becomes the cron agent's context.
"""
import os
import runpy
import sys
from pathlib import Path

home = Path(os.environ["HERMES_HOME"])
script = home / "skills" / "devops" / "intel-nuc-sentinel" / "scripts" / "sentinel.py"
if not script.is_file():
    sys.exit(f"intel-nuc-sentinel not installed at {script}")
sys.argv = [str(script), "run", "--out-dir", str(home / "sentinel" / "intel-nuc")]
runpy.run_path(str(script), run_name="__main__")
