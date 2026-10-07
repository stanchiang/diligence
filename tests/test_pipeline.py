import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from pypdf import PdfWriter
from diligence.agent import Pipeline, validate_citations, markdown
from diligence.lm import LMStudio
from diligence.store import Store, now
from diligence.tools import execute


VISION_RESULT = {'visible_text': 'Synthetic solar inspection demo', 'visual_description': 'A synthetic diagram.', 'charts_and_diagrams': [], 'uncertainties': []}
FUSION_RESULT = {'title': 'Synthetic demo', 'explanation': 'The fixture presents an inspection product.', 'claims': ['An inspection product is proposed.'], 'uncertainties': []}
STATEMENT = {'text': 'The fixture proposes an inspection product.', 'slide_numbers': [1]}
REPORT_RESULT = {'business_overview': STATEMENT, 'problem_and_customer': [], 'product_and_how_it_works': [STATEMENT], 'business_model': [], 'market_and_positioning': [], 'traction_and_stage': [], 'team_and_funding': [], 'not_explained_in_deck': ['Pricing is not explained.']}


class FakeLM:
    base_url = 'http://127.0.0.1:1234/v1'

    def __init__(self, fail_fusion=False, bad_citation=False, cancel=None):
        self.calls = []
        self.fail_fusion = fail_fusion
        self.bad_citation = bad_citation
        self.cancel = cancel

    def models(self):
        return ['qwen-fixture']

    def chat(self, model, messages, tools=None, max_tokens=900, schema=None):
        self.calls.append({'messages': messages, 'schema': schema, 'tools': tools})
        if tools:
            return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {'name': 'read_slide', 'arguments': '{"slide_number":1}'}}]}, {}
        keys = set(schema['properties'])
        if 'visible_text' in keys:
            result = VISION_RESULT
            if self.cancel:
                self.cancel.set()
        elif 'title' in keys:
            if self.fail_fusion:
                raise RuntimeError('Synthetic connection failure')
            result = FUSION_RESULT
        elif 'statements' in keys:
            result = {'statements': [STATEMENT], 'unresolved': []}
        else:
            result = dict(REPORT_RESULT)
            if self.bad_citation:
                result['business_overview'] = {'text': 'Synthetic bad citation', 'slide_numbers': [99]}
        return {'role': 'assistant', 'content': json.dumps(result)}, {'prompt_tokens': 100, 'completion_tokens': 40}


def fixture(store):
    did = 'fixture'
    folder = store.root / 'decks' / did
    folder.mkdir(parents=True)
    (folder / '1.jpg').write_bytes(b'synthetic-image')
    with store.connect() as db:
        db.execute('INSERT INTO decks VALUES (?,?,?,?,?)', (did, 'Synthetic fixture.pdf', 'a'*64, 1, now()))
        db.execute('INSERT INTO slides VALUES (?,?,?,?)', (did, 1, 'Synthetic source text', '1.jpg'))
    return did


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.did = fixture(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def run_pipeline(self, lm):
        run = self.store.create_run(self.did, 'qwen-fixture')
        Pipeline(self.store, lm).run(run['id'])
        return self.store.run(run['id'])

    def test_text_vision_fusion_tools_and_citations(self):
        lm = FakeLM()
        run = self.run_pipeline(lm)
        self.assertEqual(run['status'], 'complete')
        self.assertEqual(len(lm.calls), 5)
        self.assertEqual(lm.calls[0]['messages'][1]['content'][1]['type'], 'image_url')
        fusion_input = json.loads(lm.calls[1]['messages'][1]['content'])
        self.assertEqual(fusion_input['pdf_extracted_text'], 'Synthetic source text')
        self.assertEqual(fusion_input['local_vision_observations'], VISION_RESULT)
        self.assertFalse(any(isinstance(m.get('content'), list) for m in lm.calls[-1]['messages']))
        tool_messages = [m for m in lm.calls[-1]['messages'] if m['role'] == 'tool']
        self.assertEqual(tool_messages[0]['tool_call_id'], 'call-1')
        self.assertIn('slides 1', markdown(run))

    def test_resume_skips_saved_vision(self):
        first = self.run_pipeline(FakeLM(fail_fusion=True))
        self.assertEqual(first['status'], 'failed')
        self.assertIsNotNone(first['payload']['slides'][0]['vision'])
        retry = FakeLM()
        Pipeline(self.store, retry).run(first['id'])
        run = self.store.run(first['id'])
        self.assertEqual(run['status'], 'complete')
        self.assertNotIn('visible_text', retry.calls[0]['schema']['properties'])

    def test_citations_are_rejected_and_no_report_is_published(self):
        run = self.run_pipeline(FakeLM(bad_citation=True))
        self.assertEqual(run['status'], 'failed')
        self.assertIsNone(run['payload']['report'])

    def test_cancel_keeps_resumable_state(self):
        event = threading.Event()
        run = self.store.create_run(self.did, 'qwen-fixture')
        Pipeline(self.store, FakeLM(cancel=event), event).run(run['id'])
        self.assertEqual(self.store.run(run['id'])['status'], 'cancelled')

    def test_restart_marks_active_runs_interrupted(self):
        run = self.store.create_run(self.did, 'qwen-fixture')
        reopened = Store(self.tmp.name)
        reopened.recover_interrupted()
        self.assertEqual(reopened.run(run['id'])['status'], 'interrupted')

    def test_invalid_tool_inputs_are_rejected(self):
        slides = [{'number': 1, 'interpretation': FUSION_RESULT}]
        self.assertEqual(execute(slides, 'read_slide', '{"slide_number":1}')['slides'][0]['slide_number'], 1)
        for name, args in [('shell', '{}'), ('read_slide', '{"slide_number":true}'), ('read_slide', '{"slide_number":2}'), ('search_slides', '{"query":"x","path":"/etc/passwd"}')]:
            with self.assertRaises(ValueError):
                execute(slides, name, args)

    def test_search_is_local_and_bounded(self):
        slides = [{'number': n, 'interpretation': FUSION_RESULT} for n in range(1, 8)]
        result = execute(slides, 'search_slides', {'query': 'inspection'})
        self.assertEqual(len(result['slides']), 3)

    def test_tool_result_byte_budget_does_not_mutate_memory(self):
        interpretation = dict(FUSION_RESULT, explanation='A'*1400, claims=['B'*500]*5, uncertainties=['C'*300]*5)
        slides = [{'number': 1, 'interpretation': interpretation}]
        result = execute(slides, 'read_slide', {'slide_number': 1})
        self.assertTrue(result['truncated'])
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 2400)
        self.assertEqual(slides[0]['interpretation']['explanation'], 'A'*1400)

    def test_only_loopback_lm_urls_are_allowed(self):
        for url in ['https://api.example.com/v1', 'http://127.0.0.1.evil.example/v1', 'http://user@localhost:1234/v1', 'http://localhost:1234/v1?x=1']:
            with self.assertRaises(ValueError):
                LMStudio(url)

    def test_nested_citation_validation(self):
        with self.assertRaises(ValueError):
            validate_citations({'items': [{'text': 'fixture', 'slide_numbers': [7]}]}, {1})

    def test_image_only_pdf_ingestion_and_deduplication(self):
        writer = PdfWriter()
        writer.add_blank_page(width=640, height=360)
        stream = io.BytesIO(); writer.write(stream)
        deck = self.store.ingest('Blank synthetic slide.pdf', stream.getvalue())
        duplicate = self.store.ingest('Duplicate.pdf', stream.getvalue())
        self.assertEqual(deck['id'], duplicate['id'])
        self.assertEqual(deck['slide_count'], 1)
        self.assertEqual(self.store.slide(deck['id'], 1)['extracted_text'], '')
        self.assertTrue(self.store.image_path(deck['id'], 1).is_file())


if __name__ == '__main__':
    unittest.main()
