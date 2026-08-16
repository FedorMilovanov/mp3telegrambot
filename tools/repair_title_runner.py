#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().with_name("zero_runtime_marathon.py")
text = path.read_text(encoding="utf-8")
replacements = {
'''    import_anchor = "from typing import Any\\n"\n    if import_anchor not in text:\n        raise RuntimeError("DubStore import anchor missing")\n    text = text.replace(import_anchor, import_anchor + "\\nfrom core.media_title_policy import canonical_media_title\\n", 1)\n''':
'''    import_anchor = "from __future__ import annotations\\n"\n    if import_anchor not in text:\n        raise RuntimeError("DubStore future-import anchor missing")\n    text = text.replace(import_anchor, import_anchor + "\\nfrom core.media_title_policy import canonical_media_title\\n", 1)\n''',
'''    import_anchor = "from typing import Any\\n"\n    if import_anchor not in text:\n        raise RuntimeError("Dub runtime import anchor missing")\n    text = text.replace(import_anchor, import_anchor + "\\nfrom core.media_title_policy import canonical_media_title\\n", 1)\n''':
'''    import_anchor = "from __future__ import annotations\\n"\n    if import_anchor not in text:\n        raise RuntimeError("Dub runtime future-import anchor missing")\n    text = text.replace(import_anchor, import_anchor + "\\nfrom core.media_title_policy import canonical_media_title\\n", 1)\n''',
'''    import_anchor = "from typing import Any\\n"\n    if import_anchor not in text:\n        raise RuntimeError("dub_delivery import anchor missing")\n    text = text.replace(import_anchor, import_anchor + "\\nfrom core.media_title_policy import canonical_delivery_filename\\n", 1)\n''':
'''    import_anchor = "from __future__ import annotations\\n"\n    if import_anchor not in text:\n        raise RuntimeError("dub_delivery future-import anchor missing")\n    text = text.replace(import_anchor, import_anchor + "\\nfrom core.media_title_policy import canonical_delivery_filename\\n", 1)\n''',
'''    import_anchor = "from typing import Any\\n"\n    if import_anchor not in text:\n        raise RuntimeError("livedub output import anchor missing")\n    text = text.replace(import_anchor, import_anchor + "\\nfrom core.media_title_policy import canonical_media_title\\n", 1)\n''':
'''    import_anchor = "from __future__ import annotations\\n"\n    if import_anchor not in text:\n        raise RuntimeError("livedub output future-import anchor missing")\n    text = text.replace(import_anchor, import_anchor + "\\nfrom core.media_title_policy import canonical_media_title\\n", 1)\n''',
}
for old, new in replacements.items():
    if old not in text:
        raise RuntimeError("temporary runner repair anchor missing")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("temporary title runner import anchors repaired")
