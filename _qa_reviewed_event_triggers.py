"""Exact catalog contract tests. Optional evidence input is read-only and not logged."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    import reviewed_event_triggers as r
    sample={'server_version':'17.6','session_replication_role':'origin','targets':r.TARGETS,
        'triggers':[{key:None for key in r.FIELDS}],'dependencies':[]}
    item=sample['triggers'][0]
    item.update(trigger_name='synthetic',definition='synthetic body',
        definition_sha256=hashlib.sha256(b'synthetic body').hexdigest())
    expected=r.digest([{k:item[k] for k in r.FIELDS}])
    def blocked(value):
        try: r.validate_evidence(value)
        except r.CatalogMismatch: return
        raise AssertionError('Changed evidence accepted')
    with patch.object(r,'TRIGGERS_SHA256',expected),patch.object(r,'DEPENDENCIES_SHA256',r.digest([])):
        assert r.validate_evidence(sample)['trigger_allowlist']=='PASS'
        for field in r.FIELDS:
            bad=deepcopy(sample); bad['triggers'][0][field]='changed'; blocked(bad)
        for collection in ([],[deepcopy(item),deepcopy(item)]):
            bad=deepcopy(sample); bad['triggers']=collection; blocked(bad)
        bad=deepcopy(sample); bad['triggers'][0]['definition']='unsafe altered body'
        bad['triggers'][0]['definition_sha256']=hashlib.sha256(b'unsafe altered body').hexdigest(); blocked(bad)
        bad=deepcopy(sample); bad['dependencies']=[{'reference':'unexpected'}]; blocked(bad)
        for key in ('server_version','session_replication_role','targets'):
            bad=deepcopy(sample); bad[key]='changed'; blocked(bad)
        blocked({})
    class Cursor:
        def __init__(self): self.calls=[]
        def execute(self,q):
            assert q=='SHOW transaction_read_only'; self.calls.append(q)
        def fetchone(self): return ('off',)
    cursor=Cursor()
    try: r.validate_catalog(cursor)
    except r.CatalogMismatch: pass
    else: raise AssertionError('Write transaction accepted')
    assert len(cursor.calls)==1
    if len(sys.argv)>1:
        content=Path(sys.argv[1]).read_bytes()
        assert hashlib.sha256(content).hexdigest()==r.EVIDENCE_SHA256
        assert r.validate_evidence(json.loads(content))['trigger_allowlist']=='PASS'
        print('PASS original reviewed production export and pinned evidence hash')
    print('PASS exact identity/settings/definition/dependency mutation rejection, unknown/missing trigger rejection, read-only guard')


if __name__=='__main__': main()
