"""Resumable year-wide read and opportunity analysis; never sends messages."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path

from crm_engine import db
from crm_engine.gmail_client import LOCAL, MAILBOX
from crm_engine.catchup_sources import build_context, stamp
from crm_engine.email_opportunities import analyze, months_ago, VERSION
from crm_engine.email_review import store_thread


def save_json(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=True), encoding='utf-8')
    os.replace(temp, path)


def review_year(client, db_path=db.DB_PATH):
    """Caller holds email_lock. Cache permits recovery after provider throttling."""
    LOCAL.mkdir(exist_ok=True)
    query = 'after:' + (months_ago(12) - timedelta(days=1)).strftime('%Y/%m/%d') + ' -in:spam -in:trash'
    ids = list(client.list_threads(query))
    snapshot = LOCAL / 'year-threads.jsonl'
    cached = {}
    if snapshot.exists():
        for line in snapshot.read_text(encoding='utf-8').splitlines():
            try:
                t = json.loads(line)
                cached[t['id']] = t
            except ValueError:
                continue
    progress = {'state': 'reading Gmail', 'total': len(ids), 'processed': 0, 'started_at': stamp()}
    progress['downloaded'] = sum(tid in cached for tid in ids)
    save_json(LOCAL / 'opportunity-scan.json', progress)
    missing = [tid for tid in ids if tid not in cached]
    with snapshot.open('a', encoding='utf-8') as out:
        for t in client.get_threads(missing):
            cached[t['id']] = t
            out.write(json.dumps(t, ensure_ascii=True) + '\n')
            out.flush()
            progress['downloaded'] += 1
            if progress['downloaded'] % 25 == 0:
                save_json(LOCAL / 'opportunity-scan.json', progress)
    # Refresh recent threads even when recovering an older snapshot.
    for t in client.get_threads(ids[:25]):
        cached[t['id']] = t
    return analyze_snapshot([cached[tid] for tid in ids], db_path, progress=progress)


def analyze_snapshot(threads, db_path=db.DB_PATH, progress=None, index=None, analyzer=analyze):
    progress = progress or {'total': len(threads), 'processed': 0, 'started_at': stamp()}
    progress['state'] = 'matching CRM and text history'
    save_json(LOCAL / 'opportunity-scan.json', progress)
    index = index or build_context(threads, db_path)
    enriched = [index.attach(t) for t in threads if t.get('messages')]
    cache_path = LOCAL / 'opportunity-analysis.json'
    cached = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    progress['state'] = 'analyzing conversations'
    failures, count, candidates = 0, 0, 0
    def assess(t):
        key = t['id'] + ':' + t['cross_channel']['fingerprint'] + ':' + str(VERSION)
        existing = cached.get(key)
        if existing and existing.get('analysis_state') == 'complete':
            return t, key, existing
        return t, key, analyzer(t)
    # Two independent model requests; all SQLite writes remain on this parent thread.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(assess, t) for t in enriched]
        for future in as_completed(futures):
            t, key, result = future.result()
            cached[key] = result
            store_thread(t, db_path, force=True, opportunity_result=result)
            count += 1
            failures += result.get('analysis_state') == 'failed'
            candidates += bool(result.get('is_opportunity'))
            progress.update(processed=count, failed=failures, opportunities=candidates)
            if count % 20 == 0 or count == len(enriched):
                save_json(cache_path, cached)
                save_json(LOCAL / 'opportunity-scan.json', progress)
                print(f'Analyzed {count}/{len(enriched)} conversations; {candidates} opportunities; {failures} pending.', flush=True)
    progress.update(state='complete' if failures == 0 else 'analysis needs retry', completed_at=stamp())
    save_json(LOCAL / 'opportunity-scan.json', progress)
    if failures == 0:
        db.save_email_sync(MAILBOX, stamp(), None, db_path=db_path)
    else:
        db.save_email_sync(MAILBOX, None, f'{failures} conversations need an AI analysis retry. See Analysis pending.', db_path=db_path)
    return progress
