from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import sqlite3
import json
import uuid
from .contracts import SCHEMA, SourceFile, jsonable, atomic_json, read_json
from .adapter import probe, read_metadata, validate_time_and_status, group_runs


class Project:
    """One owning process writes the catalogue; no waveform blobs in SQLite."""
    def __init__(self, directory):
        self.path = Path(directory).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        mp = self.path/"project.json"
        if not mp.exists():
            atomic_json(mp, dict(schema_version=SCHEMA, roots={}, notes=[], saved_selections=[],
                                 recipes=[], result_paths=[], display={}, source_doi=None))
        self.document = read_json(mp)
        if self.document.get("schema_version") != SCHEMA:
            raise ValueError("Unsupported project version. Open with its original application; no automatic destructive migration is performed.")
        self.db = sqlite3.connect(self.path/"catalogue.sqlite", timeout=10)
        # Old runtime remains usable in rollback-journal mode.
        self.db.execute("PRAGMA journal_mode="+("WAL" if sqlite3.sqlite_version_info >= (3,51,3) else "DELETE"))
        self.db.execute("CREATE TABLE IF NOT EXISTS sources (root TEXT, relative TEXT, size INTEGER, mtime INTEGER, data TEXT, PRIMARY KEY(root,relative))")
        self.db.execute("CREATE TABLE IF NOT EXISTS validation (source_id TEXT PRIMARY KEY, report TEXT)")
        self.db.commit()

    @property
    def roots(self):
        return self.document["roots"]

    def save(self):
        atomic_json(self.path/"project.json", self.document)

    def add_root(self, directory):
        path = str(Path(directory).resolve())
        for rid, existing in self.roots.items():
            if existing == path:
                return rid
        rid = uuid.uuid4().hex
        self.roots[rid] = path
        self.save()
        return rid

    def scan(self, paths=(), deep=False, progress=lambda *a:None, cancelled=lambda:False):
        errors, skipped, seen = [], 0, 0
        for p in paths:
            p = Path(p).resolve()
            self.add_root(p.parent if p.is_file() else p)
        # Explicit file import indexes only that file; folder import refreshes all roots.
        only_files = {str(Path(p).resolve()) for p in paths if Path(p).is_file()}
        for rid, root in self.roots.items():
            rp = Path(root)
            if not rp.is_dir():
                errors.append(dict(path=rid, error="Source root unavailable; relink or reconnect drive."))
                continue
            candidates = sorted(p for p in rp.rglob("*") if p.is_file() and p.suffix.lower() in (".h5", ".hdf5"))
            found = set()
            for p in candidates:
                if p.name.startswith("._"):
                    skipped += 1
                    continue
                if only_files and str(p) not in only_files:
                    continue
                if cancelled():
                    self.db.commit()
                    return dict(cancelled=True, errors=errors, seen=seen, ignored_sidecars=skipped)
                rel = str(p.relative_to(rp))
                found.add(rel)
                try:
                    stat = p.stat()
                    row = self.db.execute("SELECT size,mtime,data FROM sources WHERE root=? AND relative=?", (rid,rel)).fetchone()
                    if row and row[:2] == (stat.st_size, stat.st_mtime_ns):
                        s = SourceFile(**json.loads(row[2]))
                    else:
                        if not probe(p):
                            raise ValueError("Unsupported HDF5 signature or required schema.")
                        s = read_metadata(p, rid, rp)
                    if deep:
                        _, report = validate_time_and_status(s, self.roots)
                        s = replace(s, validation="time and status validated")
                        self.db.execute("INSERT OR REPLACE INTO validation VALUES (?,?)", (s.id,json.dumps(report)))
                    self.db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?)", (rid,rel,s.size,s.mtime_ns,json.dumps(jsonable(s))))
                    seen += 1
                except Exception as exc:
                    errors.append(dict(path=rel, error=str(exc)))
                    self.db.execute("DELETE FROM sources WHERE root=? AND relative=?", (rid,rel))
                if seen % 32 == 0:
                    self.db.commit()
                progress(seen, len(candidates), p.name)
            if not only_files:
                for (old,) in self.db.execute("SELECT relative FROM sources WHERE root=?", (rid,)).fetchall():
                    if old not in found:
                        self.db.execute("DELETE FROM sources WHERE root=? AND relative=?", (rid,old))
            self.db.commit()
        runs, conflicts = self.runs()
        report = dict(files=len(self.sources()), runs=len(runs), conflicts=conflicts,
                      errors=errors, ignored_sidecars=skipped, deep=deep, sqlite=sqlite3.sqlite_version)
        atomic_json(self.path/"last_import.json", report)
        return report

    def sources(self):
        return [SourceFile(**json.loads(row[0])) for row in self.db.execute("SELECT data FROM sources ORDER BY root,relative")]

    def runs(self):
        return group_runs(self.sources())

    def relink(self, root_id, new_path):
        candidate = Path(new_path).resolve()
        originals = [s for s in self.sources() if s.root_id == root_id]
        updated = []
        for old in originals:
            new = read_metadata(candidate/old.relative_path, root_id, candidate)
            if new.id != old.id or new.config_id != old.config_id:
                raise ValueError(f"Relink identity mismatch for {old.relative_path}.")
            updated.append(new)
        # Metadata identity only: changed mtimes invalidate old job signatures.
        for s in updated:
            self.db.execute("UPDATE sources SET mtime=?,data=? WHERE root=? AND relative=?", (s.mtime_ns,json.dumps(jsonable(s)),root_id,s.relative_path))
        self.db.commit()
        self.roots[root_id] = str(candidate)
        self.save()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
