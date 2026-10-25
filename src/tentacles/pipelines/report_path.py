"""Choose fresh report identities/paths; Store owns exclusive publication."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from tentacles.config import AppConfig
from tentacles.errors import UpstreamError


def new_report_location(config: AppConfig, pipeline_id: str, kind: str,
                        title: str, now: datetime) -> tuple[str, Path]:
    report_id = uuid4().hex
    slug = re.sub(r'[^\w-]', '-', title).strip('-')[:60] or kind
    root = config.reports_dir.resolve()
    folder = (root / pipeline_id).resolve()
    if not folder.is_relative_to(root):
        raise UpstreamError('pipeline report folder escapes reports_dir')
    return report_id, folder / f'{now:%Y%m%d-%H%M%S-%f}-{kind}-{slug}-{report_id}.md'
