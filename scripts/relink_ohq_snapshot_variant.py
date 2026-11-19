#!/usr/bin/env python3
"""Link one diagnostic main against verified, untouched frozen-build objects."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def relink(build, main, output, object_source=None):
    build, main, output=map(lambda p:Path(p).resolve(),(build,main,output))
    output.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((build/'source_manifest.json').read_text())
    if any(sha(build/'source'/name)!=digest for name,digest in manifest['snapshot_sha256'].items()):
        raise ValueError('Frozen snapshot changed')
    work=build/'objects'
    text=(work/'Makefile').read_text().replace('\\\n',' ')
    variables={m.group(1):m.group(2).strip() for m in re.finditer(r'(?m)^(\w+)\s*=\s*(.*)$',text)}
    def value(name):
        s=variables.get(name,'')
        for _ in range(10):
            new=re.sub(r'\$\((\w+)\)',lambda m:variables.get(m.group(1),''),s)
            if new==s:return shlex.split(s)
            s=new
        raise ValueError('Unresolved Makefile variables')
    replaced=None
    if object_source:
        object_source=Path(object_source).resolve()
        replaced=object_source.stem+'.o'
        if object_source.suffix!='.cpp' or replaced=='main.o' or replaced not in {Path(p).name for p in value('OBJECTS')}:
            raise ValueError('Override must name one existing non-main C++ object')
    objects=[(work/p).resolve() for p in value('OBJECTS') if Path(p).name not in {'main.o',replaced}]
    before={str(p):sha(p) for p in objects}
    copy=output/'main.cpp';copy.write_bytes(main.read_bytes())
    commands=[value('CXX')+value('CXXFLAGS')+value('INCPATH')+['-c',str(copy),'-o',str(output/'main.o')]]
    extra=[]
    if object_source:
        candidate=output/object_source.name;candidate.write_bytes(object_source.read_bytes())
        extra=[str(output/replaced)]
        commands.append(value('CXX')+value('CXXFLAGS')+value('INCPATH')+['-c',str(candidate),'-o',extra[0]])
    commands.append(value('LINK')+value('LFLAGS')+['-o',str(output/'console')]+list(map(str,objects))+[str(output/'main.o')]+extra+value('LIBS'))
    with (output/'build.log').open('x') as log:
        for command in commands:subprocess.run(command,cwd=work,stdout=log,stderr=subprocess.STDOUT,check=True)
    if before!={str(p):sha(p) for p in objects}:raise ValueError('Frozen objects changed')
    receipt={'commands':commands,'input_object_sha256':before,'main_sha256':sha(copy),
             'source_manifest_sha256':sha(build/'source_manifest.json'),'binary_sha256':sha(output/'console')}
    if object_source:receipt['object_override']={'source_sha256':sha(candidate),'replaced_object':replaced,'original_object_sha256':sha(work/replaced)}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(output/'console')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['frozen_build','main_cpp','new_output']:p.add_argument(name,type=Path)
    p.add_argument('--object-source',type=Path,help='Diagnostic-only replacement for one C++ object; snapshot and other objects remain untouched')
    a=p.parse_args();relink(a.frozen_build,a.main_cpp,a.new_output,a.object_source)
