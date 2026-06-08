#!/usr/bin/env python3
"""List all valid KB IDs and names."""
import sys, os
sys.path.insert(0, "/ragflow")
os.environ.setdefault("rag_project_base", "/ragflow")

from common import settings
settings.init_settings()

from api.db.db_models import Knowledgebase

for kb in Knowledgebase.select().dicts():
    kb_id = kb["id"]
    name = kb.get("name", "")
    print(f"{kb_id}  |  {name}")
