"""Disposable, quota-limited display cache. Never an engine analysis input."""
from pathlib import Path
import os
import time
import json
import numpy as np
from .contracts import atomic_json,read_json,file_hash,jsonable


class DisplayCache:
    def __init__(self,directory,quota_bytes=64*1024**2):
        self.path=Path(directory);self.path.mkdir(parents=True,exist_ok=True)
        self.quota=quota_bytes

    def get(self,key):
        p=self.path/(key+".npz");m=self.path/(key+".json")
        try:
            meta=read_json(m)
            if p.stat().st_size != meta["bytes"] or file_hash(p)!=meta["sha256"]:return None
            with np.load(p,allow_pickle=False) as values:
                result={name:values[name] for name in values.files}
            result.update(meta["metadata"]);result["display_cache_hit"]=True
            os.utime(p,None)
            return result
        except (OSError,ValueError,KeyError):return None

    def put(self,key,result):
        arrays={k:v for k,v in result.items() if isinstance(v,np.ndarray)}
        metadata={k:v for k,v in result.items() if not isinstance(v,np.ndarray)}
        p=self.path/(key+".npz");tmp=self.path/(key+".tmp.npz")
        np.savez_compressed(tmp,**arrays)
        if tmp.stat().st_size>self.quota:tmp.unlink();return
        os.replace(tmp,p)
        atomic_json(self.path/(key+".json"),dict(bytes=p.stat().st_size,sha256=file_hash(p),metadata=jsonable(metadata)))
        self.evict()

    def evict(self):
        files=sorted(self.path.glob("*.npz"),key=lambda p:p.stat().st_mtime_ns)
        total=sum(p.stat().st_size for p in files)+sum(p.stat().st_size for p in self.path.glob("*.json"))
        for p in files:
            if total<=self.quota:break
            m=p.with_suffix(".json")
            total-=p.stat().st_size;p.unlink()
            if m.exists():total-=m.stat().st_size;m.unlink()
