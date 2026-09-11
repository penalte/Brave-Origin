"""Filesystem confinement tests; run in a disposable Linux container."""
import importlib.util
import os
from pathlib import Path
import tempfile
spec=importlib.util.spec_from_file_location('picker','/usr/local/bin/file-picker.py')
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)/'Downloads'; root.mkdir()
    (root/'safe.txt').write_text('safe')
    (root/'folder').mkdir(); (root/'folder'/'nested.txt').write_text('nested')
    (root/'escape').symlink_to('/etc')
    (root/'linked.txt').symlink_to('/etc/passwd')
    os.mkfifo(root/'pipe')
    files=p.Files(root)
    assert {x[0] for x in files.entries([])}=={'safe.txt','folder'}
    assert files.uri(['safe.txt'])==(root/'safe.txt').as_uri()
    assert files.uri(['folder','new.txt'],save=True)==(root/'folder'/'new.txt').as_uri()
    for parts in [['..','etc','passwd'],['escape','passwd'],['linked.txt'],['pipe'],['/etc/passwd'],['folder','../../escape']]:
        for save in (False,True):
            try: files.uri(parts,save=save)
            except (OSError,ValueError): pass
            else: raise AssertionError(parts)
    # A previously displayed directory replaced by an outside link is rejected.
    (root/'folder').rename(root/'old'); (root/'folder').symlink_to('/etc')
    try: files.uri(['folder','passwd'])
    except (OSError,ValueError): pass
    else: raise AssertionError('Directory substitution allowed')
    os.close(files.fd)
print('PASS: picker confines regular files, save paths and navigation; rejects links, traversal and special files')
