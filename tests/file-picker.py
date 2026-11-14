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
    (root/'.upload-fragment').write_text('partial')
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
# New folder, rename and delete stay inside the tree and never follow a link.
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)/'Downloads'; root.mkdir(); outside=Path(tmp)/'outside'; outside.mkdir()
    (outside/'keep.txt').write_text('keep'); (root/'a.txt').write_text('a'); (root/'b.txt').write_text('b')
    files=p.Files(root)
    files.mkdir(['Album']); files.mkdir(['Album','2024'])
    (root/'Album'/'2024'/'photo.jpg').write_text('x'); (root/'Album'/'way-out').symlink_to(outside)
    files.rename(['a.txt'],'notes.txt')
    assert (root/'notes.txt').read_text()=='a' and not (root/'a.txt').exists()
    name,folder,size,modified=[x for x in files.entries([]) if x[0]=='notes.txt'][0]
    assert not folder and size==1 and modified>0
    def refused(call,*errors):
        try: call()
        except errors: return
        raise AssertionError('Accepted an unsafe change')
    refused(lambda: files.rename(['notes.txt'],'b.txt'),FileExistsError)
    assert (root/'b.txt').read_text()=='b', 'Rename replaced another file'
    for bad in ('../up','.hidden','a/b','','x\x00y'):
        refused(lambda: files.rename(['notes.txt'],bad),ValueError,OSError)
        refused(lambda: files.mkdir([bad]),ValueError,OSError)
    refused(lambda: files.mkdir(['Album']),FileExistsError)
    refused(lambda: files.mkdir(['Album','way-out','inside']),OSError,ValueError)
    refused(lambda: files.remove(['..']),ValueError,OSError)
    refused(lambda: files.remove([]),ValueError)
    refused(lambda: files.rename(['Album','way-out'],'renamed'),ValueError)
    files.remove(['notes.txt']); files.remove(['Album'])
    assert not (root/'Album').exists() and not (root/'notes.txt').exists()
    assert (outside/'keep.txt').read_text()=='keep', 'Deleting a folder followed a link out of My files'
    assert {p.kind(n) for n in ('a.JPG','b.pdf','c.MP4','d.md','e.zip')}=={'image','pdf','video','text','other'}
    os.close(files.fd)
# Site filters ignore letter case; sizes read in a sensible unit.
assert p.matches('PHOTO.JPG',[(0,'*.jpg')]) and p.matches('photo.jpg',[(1,'IMAGE/*')])
assert not p.matches('notes.txt',[(0,'*.jpg'),(1,'image/*')]) and p.matches('anything.bin',[])
assert [p.readable_size(n) for n in (0,900,1536,5*1024**2,2*1024**3)]==['0 B','900 B','1.5 KB','5.0 MB','2.0 GB']
print('PASS: picker confines regular files, save paths and navigation; rejects links, traversal and special files')
