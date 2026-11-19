#!/usr/bin/env python3
"""Compile a diagnostic against a private, hash-pinned OHQ source snapshot."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(repo, output, qmake, resume=False):
    repo, output = Path(repo).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=resume)
    if resume:
        previous=json.loads((output/'source_manifest.json').read_text())
        if 'binary_sha256' in previous:
            raise ValueError('Refusing to replace a completed diagnostic build')
        if any(sha(repo/name)!=digest for name,digest in previous['source_sha256'].items()):
            raise ValueError('Original sources changed since the failed build')
        for name,digest in previous['source_sha256'].items():
            if name not in {'terminal/TOpenHydroQual/main.cpp','terminal/TOpenHydroQual/TOpenHydroQual.pro'}:
                if sha(output/'source'/name)!=digest:
                    raise ValueError('Failed-build snapshot changed; cached objects cannot be reused')
        old_main=output/'source/terminal/TOpenHydroQual/main.cpp'
        if sha(old_main)!=previous['probe_sha256']:
            raise ValueError('Diagnostic source changed since the failed build')
        project='terminal/TOpenHydroQual/TOpenHydroQual.pro'
        expected=(repo/project).read_text().replace('TARGET = OpenHydroQual-Console','TARGET = ohq_initialization_probe')
        if (output/'source'/project).read_text()!=expected:
            raise ValueError('Failed-build project settings changed')
    snapshot = output/'source'
    files = subprocess.check_output(['git','ls-files','--recurse-submodules','-z'], cwd=repo).decode().split('\0')
    selected = [p for p in files if p and Path(p).suffix in {'.h','.hpp','.cpp','.c','.cc','.pro','.pri','.tpp','.inc','.ipp','.inl'} and (repo/p).is_file()]
    before = {name:sha(repo/name) for name in selected}
    for name in selected:
        target = snapshot/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo/name, target)
    probe = Path(__file__).with_name('ohq_initialization_probe.cpp')
    main = snapshot/'terminal/TOpenHydroQual/main.cpp'
    shutil.copy2(probe, main)
    project = main.with_name('TOpenHydroQual.pro')
    project.write_text(project.read_text().replace('TARGET = OpenHydroQual-Console','TARGET = ohq_initialization_probe'))
    work = output/'objects'
    work.mkdir(exist_ok=resume)
    receipt = {'source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
               'source_sha256':before,'probe_sha256':sha(probe),'qmake':str(qmake),
               'note':'Original source untouched, including pre-existing modified console main; private main replaced by diagnostic.'}
    (output/'source_manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    with (output/('build_resume.log' if resume else 'build.log')).open('x') as log:
        subprocess.run([str(qmake),str(project),'CONFIG+=debug','CONFIG-=release'],cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True)
        subprocess.run(['make','-j2'],cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True)
    if before != {name:sha(repo/name) for name in selected}:
        raise RuntimeError('Original sources changed during diagnostic build')
    receipt['binary_sha256'] = sha(work/'ohq_initialization_probe')
    receipt['snapshot_sha256'] = {str(p.relative_to(snapshot)):sha(p) for p in snapshot.rglob('*') if p.is_file()}
    (output/'source_manifest.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(work/'ohq_initialization_probe')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for n in ('repo','new_private_output','qmake'): parser.add_argument(n,type=Path)
    parser.add_argument('--resume',action='store_true',help='Resume a failed build only if original source hashes are unchanged')
    a=parser.parse_args();build(a.repo,a.new_private_output,a.qmake,a.resume)
