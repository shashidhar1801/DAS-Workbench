import platform
import sqlite3
import sys
import shutil
import subprocess
import json
from pathlib import Path
import psutil
from .engine import versions
from .science import capabilities
from .contracts import atomic_json


def report(destination=None):
    data = dict(software=versions(),machine=platform.machine(),processor=platform.processor(),
                memory_bytes=psutil.virtual_memory().total,sqlite=sqlite3.sqlite_version,
                sqlite_wal_enabled=sqlite3.sqlite_version_info >= (3,51,3),
                frozen=bool(getattr(sys,"frozen",False)),backends=capabilities(),
                source_samples_included=False,telemetry=False)
    if platform.system()=="Darwin":
        try:
            raw = json.loads(subprocess.check_output(["system_profiler","SPHardwareDataType","SPDisplaysDataType","-json"],text=True,timeout=15))
            hw = raw["SPHardwareDataType"][0]
            data["hardware"] = {k:hw.get(k) for k in ("chip_type","machine_name","machine_model","physical_memory","number_processors")}
            data["graphics"] = [{k:d.get(k) for k in ("sppci_model","sppci_cores","spdisplays_mtlgpufamilysupport")} for d in raw["SPDisplaysDataType"]]
        except Exception as exc:
            data["hardware_query_error"] = str(exc)
    if destination:
        atomic_json(destination,data)
    return data
