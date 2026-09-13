"""Exercise startup/recovery probe scheduling without a live WARP connection."""
import importlib.util
import json
import signal
import tempfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('network', '/usr/local/bin/browser-network.py')
n = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n)

class Daemon:
    def poll(self): return None
    def terminate(self): pass
    def wait(self, timeout): pass

with tempfile.TemporaryDirectory() as directory:
    state = Path(directory)
    (state/'config.json').write_text(json.dumps({'mode':'warp'}))
    clock = [0]
    probes = []
    statuses = []
    handlers = {}
    results = iter((False, True, False, True))
    def probe(config):
        probes.append(clock[0])
        return next(results)
    def write(path, payload):
        if 'checked_at' in payload:
            statuses.append(payload['state'])
            if len(statuses)==4: handlers[signal.SIGTERM]()
    with patch.object(n,'STATE',state), patch.dict(n.os.environ,{'OIDC_ENABLED':'true'}), \
         patch.object(n,'desired',return_value={'mode':'warp'}), \
         patch.object(n,'cli'), patch.object(n.subprocess,'Popen',return_value=Daemon()), \
         patch.object(n,'probe',side_effect=probe), patch.object(n,'write_json',side_effect=write), \
         patch.object(n.signal,'signal',side_effect=lambda sig,fn:handlers.update({sig:fn})), \
         patch.object(n.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)), \
         patch.object(n.time,'time',side_effect=lambda:clock[0]), \
         patch.object(n.Path,'mkdir'), patch.object(n.Path,'chmod'):
        n.serve()
    assert probes == [0,2,17,19], probes
    assert statuses == ['unavailable','connected','unavailable','connected'], statuses
print('PASS: fast WARP startup/recovery probes, steady-state interval, no optimistic connected status')
