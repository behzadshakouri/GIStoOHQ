#!/usr/bin/env python3
"""Build the actual OHQ code generator using a completed frozen core build."""
import argparse
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from relink_ohq_snapshot_variant import sha


def build(snapshot, output, codegen_source=None, core_object_source=None):
    snapshot, output = Path(snapshot).resolve(), Path(output).resolve()
    manifest = json.loads((snapshot/'source_manifest.json').read_text())
    if 'binary_sha256' not in manifest:
        raise ValueError('Core snapshot build is not complete')
    if any(sha(snapshot/'source'/p) != h for p, h in manifest['snapshot_sha256'].items()):
        raise ValueError('Snapshot source changed')
    output.mkdir(exist_ok=False, parents=True)
    work = snapshot/'objects'
    text = (work/'Makefile').read_text().replace('\\\n', ' ')
    variables = {m.group(1): m.group(2).strip() for m in re.finditer(r'(?m)^(\w+)\s*=\s*(.*)$', text)}
    def value(name):
        s = variables[name]
        for _ in range(10):
            new = re.sub(r'\$\((\w+)\)', lambda m: variables.get(m.group(1), ''), s)
            if new == s:
                return shlex.split(s)
            s = new
        raise ValueError('Unresolved Makefile variable')
    objects = [(work/p).resolve() for p in value('OBJECTS') if Path(p).name != 'main.o']
    hashes = {str(p):sha(p) for p in objects}
    codegen=snapshot/'source/codegen'
    variant_hashes={}
    if codegen_source:
        original=Path(codegen_source).resolve()
        codegen=output/'source_codegen'
        for sub in ('include','runtime','src'):
            for source in (original/sub).rglob('*'):
                if source.is_file() and source.suffix in ('.h','.cpp'):
                    target=codegen/source.relative_to(original)
                    target.parent.mkdir(exist_ok=True,parents=True)
                    before=sha(source);shutil.copy2(source,target)
                    if sha(target)!=before or sha(source)!=before:raise ValueError('Codegen source changed during snapshot')
                    variant_hashes[str(source)]=before
    includes = ['-I'+str(codegen/n) for n in ('include', 'runtime')]
    commands = []
    extra = []
    for name in ('CodeGenerator', 'DependencyAnalyzer', 'EmbeddedRuntime', 'ExpressionEmitter', 'ohq_generate'):
        source = codegen/'src'/(name+'.cpp')
        obj = output/(name+'.o')
        commands.append(value('CXX')+value('CXXFLAGS')+value('INCPATH')+includes+['-c', str(source), '-o', str(obj)])
        extra.append(str(obj))
    if core_object_source:
        source=Path(core_object_source).resolve()
        replaced=[p for p in objects if p.name==source.stem+'.o']
        if len(replaced)!=1 or source.suffix!='.cpp':raise ValueError('Invalid core object override')
        local=output/source.name;before=sha(source);shutil.copy2(source,local)
        if sha(local)!=before or sha(source)!=before:raise ValueError('Core override changed during snapshot')
        variant_hashes[str(source)]=before
        obj=output/(source.stem+'.o')
        commands.append(value('CXX')+value('CXXFLAGS')+value('INCPATH')+['-c',str(local),'-o',str(obj)])
        objects=[p for p in objects if p not in replaced]
        extra.append(str(obj))
    binary = output/'ohq_generate'
    commands.append(value('LINK')+value('LFLAGS')+['-o', str(binary)]+list(map(str, objects))+extra+value('LIBS'))
    with (output/'build.log').open('x') as log:
        for command in commands:
            subprocess.run(command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True)
    if any(sha(p)!=h for p,h in hashes.items()) or any(sha(p)!=h for p,h in variant_hashes.items()):
        raise ValueError('Frozen objects changed')
    (output/'receipt.json').write_text(json.dumps(dict(binary_sha256=sha(binary), commands=commands,
        source_manifest_sha256=sha(snapshot/'source_manifest.json'), core_object_sha256=hashes, variant_source_sha256=variant_hashes), indent=2)+'\n')
    print(binary)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('snapshot', type=Path)
    p.add_argument('new_output', type=Path)
    p.add_argument('--codegen-source',type=Path)
    p.add_argument('--core-object-source',type=Path)
    a = p.parse_args()
    build(a.snapshot, a.new_output, a.codegen_source, a.core_object_source)
