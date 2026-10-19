"""No SQL execution: compare unchanged writer-guard decisions across parser pins."""
import ast
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch
import pglast
import db
import financial_writer_control as guard


def corpus():
    source=ast.parse(Path('_qa_fenced_reads.py').read_text(encoding='utf-8-sig'))
    main=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    cases={n.targets[0].id:ast.literal_eval(n.value) for n in main.body
           if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('reads','mutations')}
    queries=set(cases['reads']+cases['mutations'])
    # Include every static SQL literal in the current application, not financial data.
    for path in Path('.').glob('*.py'):
        if path.name.startswith('_qa'):continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if isinstance(node,ast.Constant) and isinstance(node.value,str):
                if re.match(r'^\s*(SELECT|WITH|SHOW|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|SET|BEGIN)\b',node.value,re.I):
                    queries.add(node.value)
    extra=(
        'WITH a AS (SELECT 1) SELECT * FROM a',
        'WITH a AS (SELECT 1), b AS (DELETE FROM sample RETURNING *) SELECT * FROM a',
        'SELECT pg_catalog.count(*) FROM sample',
        'SELECT evil.count(*) FROM sample',
        'SELECT public.unknown_function()',
        "SELECT set_config('transaction_read_only','off',false)",
        'EXPLAIN ANALYZE DELETE FROM sample',
        "COPY sample TO '/tmp/not-written'", 'CALL procedure_name()',
        'SELECT * FROM sample FOR NO KEY UPDATE', 'SELECT * FROM sample FOR KEY SHARE',
        'SELECT (SELECT * FROM sample FOR UPDATE)',
        "SELECT 'DELETE;DROP TABLE sample'", 'SELECT 1 /* DELETE FROM sample */',
        'SELECT', 'not valid SQL', '', 'SELECT 1; SELECT 2',
    )
    queries.update(extra)
    return sorted(queries),cases


def main():
    mode=sys.argv[1]
    root=Path(os.environ['ARETI_INCOME_EVIDENCE'])
    if mode=='candidate':
        assert str(pglast.__version__).lstrip('v')=='7.20',pglast.__version__
        assert Path(pglast.__file__).resolve().is_relative_to(root/'pglast720')
    else:
        assert str(pglast.__version__).lstrip('v')=='7.18',pglast.__version__
    queries,cases=corpus()
    # A test attempting a database connection fails before any SQL can execute.
    with patch.object(db,'get_connection',side_effect=AssertionError('SQL execution forbidden')):
        decisions={query:guard.needs_guard(query) for query in queries}
        for query in cases['reads']:assert not decisions[query],query
        for query in cases['mutations']:assert decisions[query],query
        for query in ('SELECT evil.count(*) FROM sample','SELECT public.unknown_function()',
                      "SELECT set_config('transaction_read_only','off',false)",'not valid SQL','',
                      'SELECT * FROM sample FOR NO KEY UPDATE','SELECT * FROM sample FOR KEY SHARE',
                      'SELECT (SELECT * FROM sample FOR UPDATE)','SELECT 1; SELECT 2'):
            assert decisions[query],query
        # PostgreSQL permits an empty SELECT target list. Both versions parse it
        # as a plain SelectStmt without functions, writes or locks.
        assert not decisions['SELECT']
    if mode=='baseline':
        (root/'guard-v718-reference.json').write_text(json.dumps(decisions,sort_keys=True,indent=2))
    else:
        expected=json.loads((root/'guard-v718-reference.json').read_text())
        assert decisions==expected,'SQL guard classification changed'
        assert Path('requirements.txt').read_text().count('pglast==7.20')==1
    print(f'PASS pglast {pglast.__version__}: {len(queries)} SQL decisions; known reads/mutations; unsafe calls, nested writes, locks, invalid and multi-statement SQL fail closed; zero SQL executed')


if __name__=='__main__':main()
