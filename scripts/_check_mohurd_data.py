import sys, json
sys.path.insert(0, '/ragflow')
from api.db.db_models import CrawlerResult, DB

@DB.connection_context()
def check():
    total = CrawlerResult.select().where(CrawlerResult.site_id == 'mohurd_mlxz').count()
    print(f"mohurd_mlxz total: {total}")

    recent = list(CrawlerResult.select().order_by(CrawlerResult.crawled_at.desc()).limit(5))
    for r in recent:
        ej = r.extracted_json
        if isinstance(ej, str):
            try: ej = json.loads(ej)
            except: pass
        sn = ej.get('section_name', '') if isinstance(ej, dict) else ''
        title = (r.title or '?')[:50]
        print(f"  site={r.site_id} cat={r.category} sn={sn} crawled_at={r.crawled_at} title={title}")

    mohurd_samples = list(CrawlerResult.select().where(CrawlerResult.site_id == 'mohurd_mlxz').limit(3))
    for r in mohurd_samples:
        print(f"  mohurd: tenant_id={r.tenant_id} id={r.id} status={r.status}")

check()
