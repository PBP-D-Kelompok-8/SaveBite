import json
import os
from pathlib import Path
import subprocess
import sys

OUT = Path(__file__).resolve().parent
keys = ['HOST', 'PORT', 'USER', 'PASSWORD', 'CONTROL_DB', 'MAINTENANCE_DB']
values = [os.environ.get('SAVEBITE_TEST_PG_' + k, '') for k in keys]
command = [sys.executable, '-X', 'utf8', '-B', str(OUT/'validate.py')]
(OUT/'command.json').write_text(json.dumps(dict(command=command, labels=[],
    invocation='DiscoverRunner.run_tests([])', parallel=1, keepdb=False,
    environment='Only missing six SAVEBITE_TEST_PG_* inputs forwarded from existing Windows User scope; no values persisted'),indent=2)+'\n')
process = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           text=True,encoding='utf-8',errors='replace')
with (OUT/'postgres-full.log').open('w',encoding='utf-8') as log:
    for line in process.stdout:
        for value in sorted(set(values),key=len,reverse=True):
            if value:
                line = line.replace(value,'[REDACTED]')
        log.write(line)
        log.flush()
code = process.wait()
(OUT/'postgres-full.exit').write_text(str(code)+'\n')
print(json.dumps(dict(exit=code, evidence=str(OUT.relative_to(OUT.parents[1])))))
sys.exit(code)
