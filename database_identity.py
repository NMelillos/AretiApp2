"""Temporary main-session identity screen. No application initialization or writes.

Remove this screen and its app entry hook after identity capture and infrastructure
integration are complete; repeat anonymous/THIRD/direct-access authorization QA.
"""
from contextlib import closing
import ipaddress
import os
import re

import streamlit as st

from existing_import_compare import authorized

IDENTITY_QUERY = ('SELECT current_setting(\'server_version\'), current_database(), '
                  'inet_server_addr()::text, inet_server_port(), current_user::text, session_user::text')


class IdentityBlocked(ValueError):
    pass


def safe_hostname(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise IdentityBlocked('Database identity is unavailable.')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if not all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', part)
                   for part in value.rstrip('.').split('.')):
            raise IdentityBlocked('Database identity is unavailable.') from None
    return value.lower().rstrip('.')


def provider_hint(host):
    resource = 'Not available from hostname'
    if re.fullmatch(r'db\.([a-z0-9-]+)\.supabase\.co', host):
        provider, resource = 'Supabase', host.split('.')[1]
    elif host.endswith('.pooler.supabase.com'):
        provider = 'Supabase'
    elif host.endswith('.neon.tech'):
        provider = 'Neon'
        if re.fullmatch(r'ep-[a-z0-9-]+', host.split('.')[0]):
            resource = host.split('.')[0]
    elif host.endswith('.render.com') or re.fullmatch(r'dpg-[a-z0-9]+-[a-z]', host):
        provider = 'Render'
        match = re.fullmatch(r'(dpg-[a-z0-9]+)-[a-z]', host.split('.')[0])
        if match:
            resource = match.group(1)
    elif host.endswith(('.proxy.rlwy.net', '.railway.internal')):
        provider = 'Railway'
    else:
        provider = 'Unknown'
    return provider + ' (hostname hint only)', resource


def supabase_project_reference(parsed, live, host, port, current_user, session_user):
    unknown = 'PROJECT REFERENCE NOT DETERMINED'
    if (not re.fullmatch(r'aws-[0-9]+-[a-z0-9-]+\.pooler\.supabase\.com', host)
            or port != 5432 or parsed.get('dbname') != 'postgres'
            or not isinstance(live, dict)):
        return unknown
    if any(parsed.get(key) or live.get(key) for key in ('hostaddr', 'service', 'servicefile', 'options')):
        return unknown
    user = parsed.get('user')
    match = re.fullmatch(r'postgres\.([a-z0-9]{20})', user) if isinstance(user, str) else None
    if not match:
        return unknown
    live_host = live.get('host')
    # Supavisor may expose only the base role on the PostgreSQL session. Bind the
    # tenant suffix to the actual connected client, not just environment text.
    if (not isinstance(live_host, str) or live_host.lower().rstrip('.') != host
            or str(live.get('port')) != str(port)
            or live.get('dbname') != parsed['dbname'] or live.get('user') != user
            or current_user not in ('postgres', user) or session_user not in ('postgres', user)):
        return unknown
    return match.group(1)


def read_identity():
    if not authorized():
        raise IdentityBlocked('Database identity is unavailable for this session.')
    try:
        import db
        from psycopg2.extensions import parse_dsn
        active = 'DATABASE_URL' if os.environ.get('DATABASE_URL') else 'POSTGRES_URL'
        config = os.environ.get(active)
        if not db.USING_POSTGRES or not config or config != db.DATABASE_URL:
            raise ValueError('Configuration unavailable')
        # Parse locally; never render/log the DSN, username, password or options.
        parsed = parse_dsn(config)
        host = safe_hostname(parsed.get('host'))
        port = int(parsed.get('port') or 5432)
        name = parsed.get('dbname')
        if not 1 <= port <= 65535 or not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,63}', name):
            raise ValueError('Unsupported identity representation')
        with closing(db.get_connection()) as conn:
            try:
                try:
                    live = getattr(conn, '_connection', conn).get_dsn_parameters()
                except Exception:
                    live = None
                with closing(conn.cursor()) as cur:
                    # One fixed built-in SELECT; no SET, session change or dynamic SQL.
                    cur.execute(IDENTITY_QUERY)
                    version, current, address, server_port, current_user, session_user = cur.fetchone()
            finally:
                conn.rollback()
        if not isinstance(version, str) or not re.fullmatch(r'[0-9][A-Za-z0-9. ()_+-]{0,100}', version):
            raise ValueError('Unsupported server version')
        if current != name:
            raise ValueError('Database identity mismatch')
        if address is not None:
            interface = ipaddress.ip_interface(address)
            if interface.network.prefixlen != interface.max_prefixlen:
                raise ValueError('Unexpected network instead of server address')
            address = str(interface.ip)
        else:
            address = 'Unavailable'
        if server_port is not None and (type(server_port) is not int or not 1 <= server_port <= 65535):
            raise ValueError('Invalid server port')
        provider, resource = provider_hint(host)
        reference = supabase_project_reference(parsed, live, host, port, current_user, session_user)
        return {'Active variable': active, 'Hostname': host, 'Configured port': port,
                'Database name': name, 'PostgreSQL server version': version,
                'current_database()': current, 'inet_server_addr()': address,
                'inet_server_port()': server_port if server_port is not None else 'Unavailable',
                'Provider hostname hint': provider, 'Resource/reference from hostname': resource,
                'Supabase project reference': reference}
    except Exception:
        raise IdentityBlocked('Database identity is unavailable. No configuration details were logged.') from None


def render_database_identity():
    requested = st.query_params.get('page') == 'DatabaseIdentity'
    permitted = authorized()
    if requested:
        if not permitted:
            st.error('Database identity is unavailable for this session.')
            return True
        st.subheader('Database identity diagnostics')
        if st.button('Read database identity', key='read_database_identity'):
            try:
                result = read_identity()
            except IdentityBlocked as error:
                st.error(str(error))
            else:
                st.table([{'Field': field, 'Value': str(value)} for field, value in result.items()])
        if st.button('Back to application', key='leave_database_identity'):
            del st.query_params['page']
            st.rerun()
        return True
    if permitted:
        if st.sidebar.button('Database identity diagnostics', key='open_database_identity'):
            st.query_params['page'] = 'DatabaseIdentity'
            st.rerun()
    return False
