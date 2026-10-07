"""Opt-in real-model verification. Prints metadata only; never real deck content."""
import argparse
import json
import shutil
import uuid
from pathlib import Path
from diligence.agent import Pipeline
from diligence.lm import LMStudio
from diligence.store import Store, now


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-data', default='data')
    args = parser.parse_args()
    # Read local stored text with code, without placing it in the coding assistant's context.
    source = Store(args.source_data)
    decks = source.decks()
    if not decks:
        raise SystemExit('Import the default deck first.')
    deck = decks[0]
    slide = source.slide(deck['id'], 1)
    root = Path('data/smoke')
    target = Store(root)
    did = uuid.uuid4().hex
    folder = root / 'decks' / did
    folder.mkdir(parents=True)
    shutil.copyfile(source.image_path(deck['id'], 1), folder / '1.jpg')
    with target.connect() as db:
        db.execute('INSERT INTO decks VALUES (?,?,?,?,?)', (did, 'Partial-deck smoke test.pdf', did, 1, now()))
        db.execute('INSERT INTO slides VALUES (?,?,?,?)', (did, 1, slide['extracted_text'], '1.jpg'))
    lm = LMStudio()
    run = target.create_run(did, lm.models()[0])
    print(json.dumps({'smoke_run': run['id'], 'slides_to_test': 1}), flush=True)
    Pipeline(target, lm).run(run['id'])
    result = target.run(run['id'])
    payload = result['payload']
    print(json.dumps({'status': result['status'], 'vision_saved': bool(payload['slides'][0]['vision']),
                      'fusion_saved': bool(payload['slides'][0]['interpretation']), 'report_valid': bool(payload['report']),
                      'model_calls': sum('usage' in t for t in payload['trace']),
                      'tool_calls': sum(t['phase']=='tool' for t in payload['trace']),
                      'error': payload.get('error')}), flush=True)
    raise SystemExit(0 if result['status'] == 'complete' else 1)
