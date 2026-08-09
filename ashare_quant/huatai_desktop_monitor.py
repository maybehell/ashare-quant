from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import time
from typing import Any

import numpy as np
from PIL import Image

from .huatai_desktop_rpa import HuataiDesktopPreviewDriver, READ_ONLY_PAGES, SAFE_PAGE


@dataclass(frozen=True)
class GridAnalysis:
    status: str
    width: int
    height: int
    separator_row: int
    body_dark_rows: int
    image_sha256: str


def analyze_grid_image(image: Image.Image) -> GridAnalysis:
    grayscale = np.asarray(image.convert("L"))
    if grayscale.ndim != 2 or grayscale.shape[0] < 40 or grayscale.shape[1] < 200:
        raise RuntimeError("Huatai grid image dimensions are invalid")
    height, width = grayscale.shape
    dark_per_row = (grayscale < 180).sum(axis=1)
    separator_candidates = [
        index for index, count in enumerate(dark_per_row[: min(height, 120)])
        if count >= int(width * 0.8)
    ]
    if not separator_candidates:
        raise RuntimeError("Huatai grid header separator was not detected")
    separator = max(separator_candidates)
    body_end = max(height - 20, separator + 2)
    body_rows = dark_per_row[separator + 2 : body_end]
    content_rows = int((body_rows > max(20, int(width * 0.01))).sum())
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    status = "empty_verified" if content_rows == 0 else "nonempty_unreadable"
    return GridAnalysis(
        status=status,
        width=width,
        height=height,
        separator_row=separator,
        body_dark_rows=content_rows,
        image_sha256=hashlib.sha256(buffer.getvalue()).hexdigest(),
    )


def probe_read_only_page(
    page: str,
    *,
    driver: HuataiDesktopPreviewDriver | None = None,
) -> dict[str, Any]:
    if page not in READ_ONLY_PAGES:
        raise ValueError(f"unsupported Huatai read-only page: {page}")
    active = driver or HuataiDesktopPreviewDriver()
    try:
        active.navigate(page)
        time.sleep(0.4)
        grids = active.visible_grids()
        if len(grids) != 1:
            raise RuntimeError(f"expected one visible Huatai grid on {page}, found {len(grids)}")
        image = grids[0].capture_as_image()
        if image is None:
            raise RuntimeError("Huatai grid capture returned no image")
        analysis = analyze_grid_image(image)
    finally:
        active.navigate(SAFE_PAGE)
    return {
        "page": page,
        "status": analysis.status,
        "environment": "live",
        "broker": "huatai",
        "grid_width": analysis.width,
        "grid_height": analysis.height,
        "header_separator_row": analysis.separator_row,
        "body_dark_rows": analysis.body_dark_rows,
        "image_sha256": analysis.image_sha256,
        "submit_invoked": False,
        "cancel_invoked": False,
    }
