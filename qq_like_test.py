#!/usr/bin/env python3
"""UI smoke test for liking and scrolling a QQ mini-program test feed.

This script is intentionally limited to an already-open test page in the QQ
Android client. It never launches QQ or navigates between mini-programs.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence
from xml.etree import ElementTree

import cv2
import numpy as np
import uiautomator2 as u2
from PIL import Image


LIKE_PATTERN = re.compile(
    r"^(?:赞|点赞|喜欢|like|like button)(?:\s*[\d.,kKmM万]+)?$",
    re.IGNORECASE,
)
LIKED_MARKERS = (
    "已赞",
    "取消赞",
    "已喜欢",
    "liked",
    "unlike",
    "already liked",
)
BOUNDS_PATTERN = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
TEMPLATE_SCALES = (
    0.60,
    0.70,
    0.80,
    0.90,
    0.95,
    1.00,
    1.05,
    1.15,
    1.30,
    1.45,
    1.60,
    1.70,
    1.80,
    1.90,
    2.00,
)
FAST_TEMPLATE_SCALES = (1.70, 1.80, 1.90, 2.00)
FAST_TEMPLATE_THRESHOLD = 0.84
FAST_SIGNATURE_MAX_DISTANCE = 4


@dataclass(frozen=True)
class Candidate:
    x1: int
    y1: int
    x2: int
    y2: int
    label: str
    source: str
    score: float
    signature: tuple[bool, ...] = ()

    @property
    def center(self) -> tuple[int, int]:
        return (self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Click like controls and scroll a QQ test mini-program feed."
    )
    parser.add_argument(
        "--serial",
        help="ADB serial. Omit when exactly one Android device is connected.",
    )
    parser.add_argument(
        "--package-prefix",
        default="com.tencent.mobileqq",
        help="Optional foreground package prefix label.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        action="append",
        help="Crop of an unliked icon. Repeat to match multiple templates.",
    )
    parser.add_argument(
        "--liked-template",
        type=Path,
        action="append",
        help="Optional liked-state icon. Repeat to verify against multiple templates.",
    )
    parser.add_argument(
        "--template-threshold",
        type=float,
        default=0.86,
        help="OpenCV template matching threshold (default: 0.86).",
    )
    parser.add_argument(
        "--expected-text",
        help="Fail if this text is not visible on the test page, e.g. TEST ENV.",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=12,
        help="Maximum total like clicks (default: 12, hard limit: 100).",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=30,
        help="Maximum feed swipes (default: 30, hard limit: 100).",
    )
    parser.add_argument(
        "--scroll-duration",
        type=float,
        default=0.35,
        help="Swipe duration in seconds (default: 0.35).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect and log controls without tapping.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Reduce UI waits and skip step screenshots; verification stays enabled.",
    )
    parser.add_argument(
        "--capture-screen",
        action="store_true",
        help="Save the current screen and exit. Does not tap anything.",
    )
    parser.add_argument(
        "--i-confirm-test-account",
        action="store_true",
        help="Required acknowledgement that the current page is a test account.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("artifacts/like_test"),
    )
    args = parser.parse_args()

    if not args.capture_screen and not args.i_confirm_test_account:
        parser.error("--i-confirm-test-account is required outside capture mode")
    if not 1 <= args.max_actions <= 100:
        parser.error("--max-actions must be between 1 and 100")
    if not 1 <= args.max_scrolls <= 100:
        parser.error("--max-scrolls must be between 1 and 100")
    if not 0 <= args.template_threshold <= 1:
        parser.error("--template-threshold must be between 0 and 1")
    for template in args.template or []:
        if not template.is_file():
            parser.error(f"template does not exist: {template}")
    for template in args.liked_template or []:
        if not template.is_file():
            parser.error(f"liked template does not exist: {template}")
    return args


def screenshot_rgb(device: u2.Device) -> Image.Image:
    image = device.screenshot()
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, np.ndarray):
        return Image.fromarray(image).convert("RGB")
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image)).convert("RGB")
    if isinstance(image, (str, Path)):
        return Image.open(image).convert("RGB")
    raise TypeError(f"Unsupported screenshot type: {type(image)!r}")


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def looks_like_unliked_control(text: str, resource_id: str, clickable: bool) -> bool:
    label = normalize(text)
    if not label:
        return False
    if any(marker in label for marker in LIKED_MARKERS):
        return False
    if LIKE_PATTERN.fullmatch(label):
        return True
    resource = resource_id.casefold()
    return clickable and "like" in resource and not any(
        marker in resource for marker in LIKED_MARKERS
    )


def ui_candidates(
    hierarchy_xml: str,
    frame: Image.Image,
) -> list[Candidate]:
    width, height = frame.size
    root = ElementTree.fromstring(hierarchy_xml)
    found: list[Candidate] = []

    for node in root.iter("node"):
        text = " ".join(
            part
            for part in (node.attrib.get("text", ""), node.attrib.get("content-desc", ""))
            if part
        )
        resource_id = node.attrib.get("resource-id", "")
        clickable = node.attrib.get("clickable", "false").lower() == "true"
        if not looks_like_unliked_control(text, resource_id, clickable):
            continue

        match = BOUNDS_PATTERN.fullmatch(node.attrib.get("bounds", ""))
        if not match:
            continue
        x1, y1, x2, y2 = map(int, match.groups())
        box_width, box_height = x2 - x1, y2 - y1
        if box_width < 8 or box_height < 8:
            continue
        if box_width > width * 0.45 or box_height > height * 0.25:
            continue
        if y1 < height * 0.04 or y2 > height * 0.94:
            continue

        candidate = Candidate(
            x1=x1,
            y1=y1,
            x2=x2,
            y2=y2,
            label=text or resource_id,
            source="ui",
            score=1.0,
        )
        found.append(with_signature(candidate, frame))

    return deduplicate(found, min_distance=24)


def template_candidates(
    frame: Image.Image,
    template_path: Path,
    threshold: float,
    scales: Sequence[float] = TEMPLATE_SCALES,
) -> list[Candidate]:
    frame_rgb = np.asarray(frame)
    frame_gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
    template_bytes = np.fromfile(template_path, dtype=np.uint8)
    template_gray = cv2.imdecode(template_bytes, cv2.IMREAD_GRAYSCALE)
    if template_gray is None:
        raise ValueError(f"Could not read template: {template_path}")

    screen_height, screen_width = frame_gray.shape
    raw_boxes: list[tuple[int, int, int, int, float]] = []

    # Keep 1.0 in the list: templates cropped at the device's native scale
    # otherwise score poorly after unnecessary interpolation.
    for scale in scales:
        resized = cv2.resize(
            template_gray,
            dsize=None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
        )
        template_height, template_width = resized.shape
        if template_width < 8 or template_height < 8:
            continue
        if template_width >= screen_width or template_height >= screen_height:
            continue

        result = cv2.matchTemplate(frame_gray, resized, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= threshold)
        if len(xs) > 500:
            order = np.argsort(result[ys, xs])[-500:]
            ys, xs = ys[order], xs[order]

        for x, y in zip(xs.tolist(), ys.tolist()):
            raw_boxes.append(
                (x, y, template_width, template_height, float(result[y, x]))
            )

    boxes = non_max_suppression(raw_boxes, iou_threshold=0.35)
    candidates: list[Candidate] = []
    for x, y, box_width, box_height, score in boxes:
        center_y = y + box_height // 2
        if center_y < screen_height * 0.04 or center_y > screen_height * 0.94:
            continue
        candidate = Candidate(
            x1=x,
            y1=y,
            x2=x + box_width,
            y2=y + box_height,
            label=f"template:{template_path.name}",
            source="template",
            score=score,
        )
        candidates.append(with_signature(candidate, frame))
    return candidates


def non_max_suppression(
    boxes: Sequence[tuple[int, int, int, int, float]],
    iou_threshold: float,
) -> list[tuple[int, int, int, int, float]]:
    ordered = sorted(boxes, key=lambda item: item[4], reverse=True)
    kept: list[tuple[int, int, int, int, float]] = []
    for box in ordered:
        if all(intersection_over_union(box, existing) <= iou_threshold for existing in kept):
            kept.append(box)
    return kept


def intersection_over_union(
    left: tuple[int, int, int, int, float],
    right: tuple[int, int, int, int, float],
) -> float:
    ax1, ay1, aw, ah, _ = left
    bx1, by1, bw, bh, _ = right
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    intersection_width = max(0, min(ax2, bx2) - max(ax1, bx1))
    intersection_height = max(0, min(ay2, by2) - max(ay1, by1))
    intersection = intersection_width * intersection_height
    if intersection == 0:
        return 0.0
    union = aw * ah + bw * bh - intersection
    return intersection / union


def with_signature(candidate: Candidate, frame: Image.Image) -> Candidate:
    return Candidate(
        x1=candidate.x1,
        y1=candidate.y1,
        x2=candidate.x2,
        y2=candidate.y2,
        label=candidate.label,
        source=candidate.source,
        score=candidate.score,
        signature=context_hash(candidate, frame),
    )


def context_hash(candidate: Candidate, frame: Image.Image) -> tuple[bool, ...]:
    """Hash a wide region around the control to recognize repeated posts."""
    center_x, center_y = candidate.center
    width, height = frame.size
    half_width = max(110, int(width * 0.18))
    half_height = max(130, int(height * 0.08))
    x1 = max(0, center_x - half_width)
    y1 = max(0, center_y - half_height)
    x2 = min(width, center_x + half_width)
    y2 = min(height, center_y + half_height)
    patch = frame.crop((x1, y1, x2, y2)).convert("L").resize((17, 16))
    array = np.asarray(patch, dtype=np.int16)
    return tuple((array[:, 1:] > array[:, :-1]).flatten().tolist())


def signature_seen(
    signature: tuple[bool, ...],
    seen: Sequence[tuple[bool, ...]],
    max_distance: int = 10,
) -> bool:
    if not signature:
        return False
    for previous in seen:
        if sum(a != b for a, b in zip(signature, previous)) <= max_distance:
            return True
    return False


def deduplicate(candidates: Iterable[Candidate], min_distance: int) -> list[Candidate]:
    ordered = sorted(candidates, key=lambda item: (item.source != "ui", -item.score))
    kept: list[Candidate] = []
    for candidate in ordered:
        center = candidate.center
        if all(
            abs(center[0] - existing.center[0]) >= min_distance
            or abs(center[1] - existing.center[1]) >= min_distance
            for existing in kept
        ):
            kept.append(candidate)
    return kept


def collect_candidates(
    device: u2.Device,
    frame: Image.Image,
    template_paths: Sequence[Path] | None,
    template_threshold: float,
    template_scales: Sequence[float] = TEMPLATE_SCALES,
) -> list[Candidate]:
    candidates = ui_candidates(device.dump_hierarchy(compressed=False), frame)
    for template_path in template_paths or []:
        candidates.extend(
            template_candidates(
                frame,
                template_path,
                template_threshold,
                scales=template_scales,
            )
        )
    return sorted(deduplicate(candidates, min_distance=36), key=lambda item: item.center[1])


def frame_hash(frame: Image.Image) -> tuple[bool, ...]:
    patch = frame.convert("L").resize((17, 16))
    array = np.asarray(patch, dtype=np.int16)
    return tuple((array[:, 1:] > array[:, :-1]).flatten().tolist())


def hashes_close(
    left: tuple[bool, ...],
    right: tuple[bool, ...],
    max_distance: int = 6,
) -> bool:
    return sum(a != b for a, b in zip(left, right)) <= max_distance


def save_screenshot(frame: Image.Image, path: Path) -> None:
    frame.save(path, format="PNG")


def main() -> int:
    args = parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    artifacts = args.artifacts_dir / timestamp
    artifacts.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(artifacts / "run.log", encoding="utf-8"),
        ],
    )
    log = logging.getLogger("qq-like-test")

    device = u2.connect(args.serial)
    current = device.app_current()
    package = current.get("package", "")
    log.info("Foreground package: %s", package)

    initial_frame = screenshot_rgb(device)
    if args.capture_screen:
        target = artifacts / "current_screen.png"
        save_screenshot(initial_frame, target)
        log.info("Saved screen to %s", target.resolve())
        return 0

    if args.expected_text:
        hierarchy = device.dump_hierarchy(compressed=False)
        if args.expected_text not in hierarchy:
            log.error("Expected test page marker not found: %r", args.expected_text)
            return 3

    if not args.template:
        log.warning(
            "No --template supplied. Only controls exposed in the UI tree can be found."
        )
    else:
        log.info(
            "Template fallback enabled (%d): %s",
            len(args.template),
            ", ".join(str(path.resolve()) for path in args.template),
        )

    result = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "serial": args.serial,
        "package": package,
        "dry_run": args.dry_run,
        "clicks": 0,
        "likes_verified": 0,
        "detections": 0,
        "scrolls": 0,
        "stopped_reason": "",
        "steps": [],
    }
    seen: list[tuple[bool, ...]] = []
    stop_reason = "max_actions"
    no_motion_count = 0
    template_scales = FAST_TEMPLATE_SCALES if args.fast else TEMPLATE_SCALES
    template_threshold = (
        min(args.template_threshold, FAST_TEMPLATE_THRESHOLD)
        if args.fast
        else args.template_threshold
    )
    signature_max_distance = (
        FAST_SIGNATURE_MAX_DISTANCE if args.fast else 10
    )
    click_delay = 0.3 if args.fast else 0.55
    scroll_delay = 0.4 if args.fast else 0.8
    save_step_images = not args.fast

    try:
        for scroll_index in range(args.max_scrolls + 1):
            screen_action_count = 0
            while result["clicks"] < args.max_actions:
                before = screenshot_rgb(device)
                candidates = collect_candidates(
                    device,
                    before,
                    args.template,
                    template_threshold,
                    template_scales=template_scales,
                )
                fresh = [
                    candidate
                    for candidate in candidates
                    if not signature_seen(
                        candidate.signature,
                        seen,
                        max_distance=signature_max_distance,
                    )
                ]
                result["detections"] += len(fresh)
                if not fresh:
                    break

                candidate = fresh[0]
                result["steps"].append(
                    {
                        "scroll": scroll_index,
                        "action": result["clicks"] + 1,
                        "x": candidate.center[0],
                        "y": candidate.center[1],
                        "label": candidate.label,
                        "source": candidate.source,
                        "score": round(candidate.score, 4),
                    }
                )
                seen.append(candidate.signature)

                stem = f"step_{result['clicks'] + 1:03d}"
                if save_step_images:
                    save_screenshot(before, artifacts / f"{stem}_before.png")
                log.info(
                    "Detected like control at (%d, %d) via %s: %s",
                    candidate.center[0],
                    candidate.center[1],
                    candidate.source,
                    candidate.label,
                )

                if not args.dry_run:
                    device.click(*candidate.center)
                    time.sleep(click_delay)
                    result["clicks"] += 1
                    after_frame = screenshot_rgb(device)
                    if save_step_images:
                        save_screenshot(after_frame, artifacts / f"{stem}_after.png")
                    if args.liked_template:
                        liked_matches = [
                            match
                            for template in args.liked_template
                            for match in template_candidates(
                                after_frame,
                                template,
                                template_threshold,
                                scales=template_scales,
                            )
                        ]
                        verified = any(
                            abs(match.center[0] - candidate.center[0]) <= 35
                            and abs(match.center[1] - candidate.center[1]) <= 35
                            for match in liked_matches
                        )
                        result["steps"][-1]["liked_state_verified"] = verified
                        if verified:
                            result["likes_verified"] += 1
                        else:
                            log.warning("Liked-state template was not found at the click point")
                    log.info("Clicked action %d", result["clicks"])

                screen_action_count += 1
                if screen_action_count >= 20:
                    break

            if result["clicks"] >= args.max_actions:
                stop_reason = "max_actions"
                break

            if scroll_index >= args.max_scrolls:
                stop_reason = "max_scrolls"
                break

            width, height = initial_frame.size
            pre_scroll = screenshot_rgb(device)
            device.swipe(
                int(width * 0.50),
                int(height * 0.78),
                int(width * 0.50),
                int(height * 0.28),
                args.scroll_duration,
            )
            time.sleep(scroll_delay)
            result["scrolls"] += 1
            after = screenshot_rgb(device)
            if save_step_images:
                save_screenshot(after, artifacts / f"scroll_{scroll_index + 1:03d}.png")
            if hashes_close(frame_hash(pre_scroll), frame_hash(after)):
                no_motion_count += 1
                if no_motion_count >= 2:
                    stop_reason = "page_end"
                    break
            else:
                no_motion_count = 0
            log.info("Scrolled %d/%d", result["scrolls"], args.max_scrolls)
    except KeyboardInterrupt:
        stop_reason = "interrupted"
        log.warning("Stopped by user")
    finally:
        result["stopped_reason"] = stop_reason
        result["finished_at"] = datetime.now().isoformat(timespec="seconds")
        (artifacts / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    log.info(
        "Finished: clicks=%d, verified=%d, scrolls=%d, reason=%s, artifacts=%s",
        result["clicks"],
        result["likes_verified"],
        result["scrolls"],
        stop_reason,
        artifacts.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
