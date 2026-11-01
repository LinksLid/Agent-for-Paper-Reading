"""Download public paper PDFs and preserve URL, hash and parse status."""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import time
import httpx
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT/'config/paper-catalog.json').read_text(encoding='utf-8'))

def fetch(item):
    result = dict(item)
    target = ROOT/'paper'/('MAC-SQL.pdf' if item['id']=='macsql' else item['id']+'.pdf')
    try:
        if not target.exists():
            with httpx.Client(timeout=90, follow_redirects=True, trust_env=False) as client:
                for attempt in range(2):
                    try:
                        response = client.get(item['pdf'])
                        response.raise_for_status()
                        raw = response.content
                        if not raw.startswith(b'%PDF-') or len(raw)>50_000_000:
                            raise ValueError('Response is not a supported PDF')
                        reader = PdfReader(__import__('io').BytesIO(raw))
                        if not reader.pages:
                            raise ValueError('Empty PDF')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(raw)
                        break
                    except Exception:
                        if attempt:
                            raise
                        time.sleep(3)
        raw = target.read_bytes()
        reader = PdfReader(target)
        result.update(status='downloaded', path=str(target), sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), pages=len(reader.pages), text_preview=(reader.pages[0].extract_text() or '')[:450])
    except Exception as error:
        result.update(status='failed', error_type=type(error).__name__)
    print(item['id']+': '+result['status'], flush=True)
    return result

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(fetch, CATALOG))
    path = ROOT/'paper/sources.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'downloaded_at':time.time(),'papers':results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'downloaded':sum(i['status']=='downloaded' for i in results),'total':len(results),'manifest':str(path)}))
