"""Compatibility layer for historical source guards, not behavioral tests.

Only reviewed Decimal and writer-fence bodies/imports are normalized. Every other
AST node must still match the deployed baseline. Runtime tests use live code.
"""
import ast
import hashlib
import subprocess

BASE = '48a6efd9cdc4b58972e87887a7f7af7d613aeca6'
FUNCTIONS = {
    'db.py': {
        'init_db': '2af9575cb7800356c8fb6ace7d19891caa1b788e871940c46c16ba3f8283e1dc',
        '_parse_rate_month_cell': 'b0065c228508624233a111fbd75aa64d978d598db81a0c600e756b6cf4cbc2c6',
        'get_transaction_change_log': '017fe0876342c0c2057475aba755d614deaf37f6f55cc7fe9f1c99498bc194ac',
        'get_import_transaction_audit': 'b9ce759f29fb048c1cd1d5b0cb3d8ba510c3c2b6b80116239d802f847975b848',
        '_optional_float_equal': '784529c002e01420a7aff7d324f880fc3d80d6a25e15e2e31377358c46ccd37c',
        '_split_amount_or_none': 'ffeaf88a52595b23caa2d1b28481e113216272175f46947756b981b190a337f5',
        '_transaction_line_key': '6798edcb31b9c987041e6f68dd6d4bf3ee1321b8d330b7da8ce5371cfc73571d',
        'get_exact_duplicate_audit': '785b3a6a527f38b8081242013698c701872b2af90ba2b31b36a2de38fec83896',
        'get_cross_statement_duplicate_audit': 'b740964b04c91e16a995a972828ca778da89570c0bbf1396303faa1651f28f1c',
        'save_reviewed_rows': '8227ccf1ba2a9b33cc34926667591dd98545ceec2ccc380c09f81d3e6d58f3cb',
        'backfill_missing_usd_amounts': '7c4d805bb70833da38c7fa843786b4599808330e5f2b35726ae32b02f28a805c',
        'replace_rates_from_excel': '1f2e22fd0e0b3e58e3aa7597bac657b3a2e93cc673d9aac36a99caa6059c47a6',
        '_apply_balance_reconciliation': 'e221186294a3199f5c4740dbf5bbe8842c58ea70843e089b896523f03b74d214',
        'import_database_updates_from_excel': '8a3866e31043bdefdd05d6fac0b7848e7b8789fa9062260ef2aea81844e2cf6b',
        'get_rates': '06af70bee1625b52985f34fb74d96735801e752879f31d53fc9b2a6028691e29',
        'get_statement_balances': 'eb25d8f067e1e68963044d370d8c1f332d16dc32578c77fef04b8318c10f73ee',
        'get_import_history': '120cc83638ecff1fe25476060c35c6844e5161319c9a22b14a26fd50b70a197f',
        '_float_or_none': '7d6b7fd034c5df2ce998ada0409b93495ec8306e075d2a1e901c4f5e24e6f6f0',
        '_rate_from_label_value': '80eff0c936a0734f97808be1f3a5de1a73f6fa2cddc9f32d3616e0b5dd2eb132',
        '_load_rate_lookup': '499f929da995d24dbfc2079f9a1fa88c5a9f67854ccecc1cfd88fdce83fa7195',
        '_lookup_rate': '3f85da9252c53f3944bdfd2b1262a49bbcec132ed5896e812eda239abaef123b',
        '_usd_from_amount': 'c07e95c07648b8e6530b3aa1f256cc23d56edb68727703a7cf66e238275ce052',
        'apply_account_and_rates': 'fdcff2c3904a33bb52572de716d85f720fa4e079b73bd4444551c0273cf615b7',
        'save_pending_transactions': 'f2aa3eb8ead13d363c90e5478cf0c07ad1eaed55c3b56a470841f68f05449a75',
        'get_pending_transactions': '8e67be3918f75dd53c2fca52cf9433d07b74c23ffb09ed165d797cf6d80bc174',
        'get_saved_transactions': '353aad3a5dfbd67c6cbf060124f82bfdaf7dace8e192fed451a0d371f4ebffc5',
        'get_all_transactions': '61954e25661d23e637c2f54facbe2f0274ddf931916ef82677b246c8ed7fa13c',
        'get_transaction_edit_states': 'd00c4c50c61213949e7f537f707b4d5b9027d8d294d048d56ef61bd6927125bc',
        'split_transaction': 'c1487c02954c70a7978aa31d9e4490d3f4ad1f1ba653f2383a57ec3307c69ebb',
        'insert_manual_transaction': 'd150c24cdb924b67e84f1713cc42a0c7c72746df2e7b57fe1103bf99c6a0ae07',
        'dataframe_to_excel_bytes': '08967116efaac6d9db4011a9e49568344ac049319ee77bf260b1c0de16893d23',
    },
    'parsing.py': {
        'parse_csv': 'f97e7022463ffca9fb3d7d29ce8c603f36e82d4d915af79f392a9121c41fc7eb',
        'parse_excel': 'afee1f5edcc4e76fa723940de6a08b2016df425eb1a7086f84536b37cc41f796',
        '_parse_amount': '0c0a96569b6fc086236400cf885fdcfd214ba174c61d29877e1257c3594c8f23',
        '_parse_revolut_business_pdf_text': '62641204f4c347885bc8d3de30d572f188f6c5930756fd8891b9ecc1692dd059',
        '_parse_revolut_pdf_text': '5e09c514912cc1d512caa083635e9a819eeefd864f8faefa2e4f52ea7e993fdd',
    },
    'utils.py': {'format_currency': '6a5ea97cec5ff50a5c536cdfdbacf30fac622c6a5be8bddc021cbe0e081410f2'},
    'classification.py': {'conservative_rule_category': '5dfd3e7b7d3f05653f1edeaaf6fbad8342786ad5544ea6d4d925bb863849eb22'},
    'reporting.py': {
        'build_sample_expenses_report': '46a277737d7a08b2df4134b191015eb34996b32ac8fc7dcf916e6f891c0b0baf',
        'build_excel_report': 'b1218b5296d288927d26e80f473d7abc3c93689de73a60f66ec552be08a1f378',
        '_format_percent': '0b5fb80c24a7ad70e266fedee2ba37a679c15bd4f800d69211f16d27dc4c5cd9',
        '_prepare_report_data': 'a10e41a21b719201ca91b9ac71636db60f7f0013d2c60eb83e3fcef68662c31a',
        '_build_report_frame': '84f4e4efb0ee2d8f84da226ef84050b61036422ae2ea23e486f988a06cf20460',
        '_write_report_sheet': 'b1c1a79806be4aaba2478e36851f93244b8d881e20eb121420adae6450f84cc0',
        '_build_income_deposits_frame': '6cfa9b14eef68940d021ee8391340d74c2276248416252b8a6d9791c438a12e4',
        '_build_own_funds_frame': '88c4ead394f018480c34b2cd331993db017b8723000ef838457d134d9725f618',
        '_format_money': 'a16e89dd0d013404ec62e8174a28bd4083ef88e370f0c8de27c6de394dbdc941',
        '_pdf_lines_for_sections': '94b64531381086a45d6066311511659b2dfda8f2a2a2c04be5669675e966b067',
        '_month_totals': '1cd1c0321fefeb458987c52e0605010f58de99c681563f9c80f19ccb0b5e3553',
        '_build_sections': '290ebc0392ed3c8dc0c4bba26b3a8b88ab3524aa8ac5d7e365fd7ba9833cb09e',
        '_prepare_verification_data': '606347fa817dd25d37d5735c6411aef19fdc19097fcf76254f607709dc65da14',
        'build_report_verification': '7b486b938e1c545e11832f112c21b5b748dafb37f471c20950a85277dcdf8ed0',
    },
}
ADDITIONS = {
    'utils.py': '',
    'classification.py': '',
    'db.py': 'from decimal import Decimal\nfrom financial_decimal import decimal_value, product, cents, reciprocal\nsqlite3.register_adapter(Decimal, str)',
    'parsing.py': 'from decimal import Decimal, InvalidOperation',
    'reporting.py': 'from report_money import decimal_sum\nfrom financial_decimal import cents, optional_decimal, excel_value, exact_frame',
}


def protected_source(name, source):
    from safra_balances_qa import without_safra_balances
    source = without_safra_balances(name, source)
    from _qa_nomad_runtime import without_nomad_runtime
    source = without_nomad_runtime(name, source)
    if name == 'app.py' and b'from financial_decimal import' in source:
        assert hashlib.sha256(source.replace(b'\r\n', b'\n')).hexdigest() == '1f40e314c3a31c814a8ffb54f23cdde570eeede26df7da57e3ce3b58f3af7481', 'Unreviewed application change'
        return subprocess.check_output(['git', 'show', BASE + ':' + name]).replace(b'\r\n', b'\n')
    if name not in FUNCTIONS:
        return source
    marker = (b'from decimal import Decimal, InvalidOperation' if name == 'parsing.py'
              else b'from financial_decimal import')
    if marker not in source:
        return source
    baseline = subprocess.check_output(['git', 'show', BASE + ':' + name]).replace(b'\r\n', b'\n')
    old, current = ast.parse(baseline), ast.parse(source)
    old_functions = {n.name: n for n in old.body if isinstance(n, ast.FunctionDef)}
    old_classes = {n.name: n for n in old.body if isinstance(n, ast.ClassDef)}
    additions = [ast.dump(n) for n in ast.parse(ADDITIONS[name]).body]
    result, found = [], set()
    for node in current.body:
        signature = ast.dump(node)
        if name == 'db.py' and isinstance(node, ast.ClassDef) and node.name == 'PostgresCursor':
            assert hashlib.sha256(signature.encode()).hexdigest() == '91e80ad1cb19c4b1394c453b3fbc741715b89428f0cf5ea7b1df25951b050517', 'Unreviewed writer guard change'
            node = old_classes[node.name]
        if signature in additions:
            additions.remove(signature)
            continue
        if name == 'db.py' and signature == ast.dump(ast.parse(
                'MAX_SAFE_FINANCIAL_AMOUNT = Decimal((2 ** 53) - 1) / 100').body[0]):
            node = ast.parse('MAX_SAFE_FINANCIAL_AMOUNT = ((2 ** 53) - 1) / 100').body[0]
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS[name]:
            assert hashlib.sha256(signature.encode()).hexdigest() == FUNCTIONS[name][node.name], 'Unreviewed Decimal function'
            found.add(node.name)
            node = old_functions[node.name]
        result.append(node)
    assert not additions and found == set(FUNCTIONS[name]), 'Decimal boundary set changed'
    current.body = result
    assert ast.dump(current) == ast.dump(old), 'Unrelated protected code changed'
    return baseline
