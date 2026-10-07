"""Priority 4 control-state tests plus all preserved completion-message checks."""
from contextlib import contextmanager
from unittest.mock import patch
import unittest

import db
import streamlit as st
from streamlit.delta_generator import DeltaGenerator
import _qa_post_import_message as messages


class ButtonTests(messages.CompletionTests):
    def import_file(self, app):
        super().import_file(app)
        if any('have been imported into the database' in e.value or
               'Statement imported successfully' in e.value for e in app.success):
            self.assertFalse(any(b.label in ('Import statement','Importing statement...') for b in app.button))
            self.assertFalse(any('before confirming Import statement' in e.value for e in app.warning))

    def test_disabled_before_commit_and_clear_processing_state(self):
        from import_history import commit_statement
        original_button=DeltaGenerator.button
        original_spinner=st.spinner
        rendered=[]; processing=False
        def button(slot,label,*args,**kwargs):
            if kwargs.get('key')=='confirm_statement_import':
                rendered.append((label,kwargs.get('disabled',False)))
            return original_button(slot,label,*args,**kwargs)
        @contextmanager
        def spinner(text,*args,**kwargs):
            nonlocal processing
            self.assertIn('Please wait for confirmed completion',text)
            processing=True
            with original_spinner(text,*args,**kwargs): yield
            processing=False
        def commit(*args,**kwargs):
            self.assertEqual(rendered[-1],('Importing statement...',True))
            self.assertTrue(processing)
            self.assertNotIn('statement_import_request',app.session_state)
            return commit_statement(*args,**kwargs)
        app=self.run_app(self.app())
        self.assertFalse(next(b for b in app.button if b.label=='Import statement').disabled)
        with patch.object(DeltaGenerator,'button',button), patch.object(st,'spinner',spinner), patch('import_history.commit_statement',side_effect=commit) as committed:
            self.import_file(app)
            self.assertEqual(committed.call_count,1)
        self.complete(app,2)

    def test_queued_stale_click_cannot_submit_second_import(self):
        from import_history import commit_statement
        app=self.run_app(self.app())
        stale_button=next(b for b in app.button if b.label=='Import statement')
        with patch('import_history.commit_statement',wraps=commit_statement) as committed:
            self.import_file(app)
            before=self.snapshot()
            with patch.object(st,'file_uploader',return_value=self.file): stale_button.click().run()
            self.assertFalse(app.exception)
            self.assertEqual(committed.call_count,1)
            self.assertEqual(self.snapshot(),before)
            self.assertFalse(any(b.label=='Import statement' for b in app.button))
        self.complete(app,2)

    def test_rollback_restores_enabled_retry_and_preserves_error(self):
        app=self.run_app(self.app()); before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=ValueError('Synthetic button rollback')):
            self.import_file(app)
        self.assertTrue(any('Synthetic button rollback' in e.value for e in app.error))
        self.assertEqual(self.snapshot(),before)
        self.assertFalse(next(b for b in app.button if b.label=='Import statement').disabled)
        self.assertNotIn('statement_import_request',app.session_state)
        self.assertNotIn('completed_statement_import_message',app.session_state)
        self.import_file(app); self.complete(app,2)
        self.assertEqual(self.counts(),(2,1,1))


if __name__=='__main__': unittest.main(verbosity=2)
