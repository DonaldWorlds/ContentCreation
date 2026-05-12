def get_platform_preset(platform: str, layout: str = "vertical") -> dict:
    """
    Returns the target output settings for a (platform, layout) pair.

    `layout`:
        'vertical' = 1080x1920 9:16 (default, original behavior)
        'square'   = 1080x1080 1:1  (talking-head profile accounts)
        'split'    = 1080x1920 9:16 composed as face-top + gameplay-bottom
                     (F9 gameplay marker — composition lives in SplitProcessor,
                      this just supplies the canvas size).
    """
    if layout == "square":
        # Same safe-area top/bottom as vertical for now — the caption is
        # placed proportionally inside the canvas via MarginV in _captions.py.
        return {
            "canvas_width": 1080,
            "canvas_height": 1080,
            "aspect_ratio": "1:1",
            "safe_area": {"top": 90, "bottom": 150},
        }

    if layout == "split":
        # 9:16 canvas, but visually divided in half: face 1080x960 on top,
        # gameplay 1080x960 on bottom. Captions land just below the seam
        # (MarginV picked by SplitProcessor, not the LAYOUT_MARGIN_V table).
        return {
            "canvas_width": 1080,
            "canvas_height": 1920,
            "aspect_ratio": "9:16",
            "safe_area": {"top": 120, "bottom": 260},
        }

    # Default: vertical 9:16.
    presets = {
        "tiktok": {
            "canvas_width": 1080,
            "canvas_height": 1920,
            "aspect_ratio": "9:16",
            "safe_area": {"top": 120, "bottom": 260},
        },
        "youtube_shorts": {
            "canvas_width": 1080,
            "canvas_height": 1920,
            "aspect_ratio": "9:16",
            "safe_area": {"top": 120, "bottom": 260},
        },
        "facebook_reels": {
            "canvas_width": 1080,
            "canvas_height": 1920,
            "aspect_ratio": "9:16",
            "safe_area": {"top": 140, "bottom": 280},
        },
        "twitter": {
            "canvas_width": 1080,
            "canvas_height": 1920,
            "aspect_ratio": "9:16",
            "safe_area": {"top": 120, "bottom": 260},
        },
    }

    return presets.get(platform.lower(), presets["tiktok"])


def decide_crop_vs_pad(width: int, height: int, target_aspect: str) -> str:
    """
    Decides whether the source should be cropped or padded to fit the target aspect ratio.
    """
    target_w, target_h = map(int, target_aspect.split(":"))
    source_ratio = width / height
    target_ratio = target_w / target_h

    if source_ratio > target_ratio:
        return "crop"
    return "pad"


def get_centered_crop_config(width: int, height: int, target_aspect: str) -> dict:
    """
    Returns centered crop settings for clips that should stay visually balanced.
    """
    target_w, target_h = map(int, target_aspect.split(":"))
    target_ratio = target_w / target_h

    if width / height > target_ratio:
        crop_width = int(height * target_ratio)
        crop_height = height
    else:
        crop_width = width
        crop_height = int(width / target_ratio)

    x = max(0, (width - crop_width) // 2)
    y = max(0, (height - crop_height) // 2)

    return {
        "mode": "crop",
        "crop_x": x,
        "crop_y": y,
        "crop_width": crop_width,
        "crop_height": crop_height,
    }


def get_golden_zone_crop_config(width: int, height: int, target_aspect: str) -> dict:
    """
    Returns crop settings biased toward the golden zone instead of the exact center.
    """
    target_w, target_h = map(int, target_aspect.split(":"))
    target_ratio = target_w / target_h
    center_bias = 0.42

    if width / height > target_ratio:
        crop_width = int(height * target_ratio)
        crop_height = height
    else:
        crop_width = width
        crop_height = int(width / target_ratio)

    x = max(0, int((width - crop_width) * center_bias))
    y = max(0, int((height - crop_height) * center_bias))

    return {
        "mode": "crop",
        "crop_x": x,
        "crop_y": y,
        "crop_width": crop_width,
        "crop_height": crop_height,
    }


def get_talking_head_template(platform: str, layout: str = "vertical") -> dict:
    """
    Returns the composition template for talking-head style clips, for the
    given (platform, layout) pair.
    """
    preset = get_platform_preset(platform, layout=layout)

    return {
        "template": "talking_head",
        "platform": platform.lower(),
        "layout": layout,
        "canvas_width": preset["canvas_width"],
        "canvas_height": preset["canvas_height"],
        "aspect_ratio": preset["aspect_ratio"],
        "safe_area": preset["safe_area"],
        "composition_type": "golden_zone",
        "center_bias": 0.42,
    }


def build_processing_config(metadata: dict, platform: str, style: str = "talking_head", layout: str = "vertical") -> dict:
    """
    Builds the final framing config that the FFmpeg generator can consume.

    `layout` selects the canvas: 'vertical' (1080x1920) or 'square' (1080x1080).
    """
    preset = get_platform_preset(platform, layout=layout)
    width = metadata["width"]
    height = metadata["height"]

    crop_mode = decide_crop_vs_pad(width, height, preset["aspect_ratio"])

    if style == "talking_head":
        template = get_talking_head_template(platform, layout=layout)
    else:
        template = {
            "template": "default",
            "platform": platform.lower(),
            "layout": layout,
            "canvas_width": preset["canvas_width"],
            "canvas_height": preset["canvas_height"],
            "aspect_ratio": preset["aspect_ratio"],
            "safe_area": preset["safe_area"],
        }

    return {
        "input_width": width,
        "input_height": height,
        "canvas_width": template["canvas_width"],
        "canvas_height": template["canvas_height"],
        "aspect_ratio": template["aspect_ratio"],
        "safe_area": template["safe_area"],
        "template": template["template"],
        "layout": layout,
        "mode": crop_mode,
        "center_bias": template.get("center_bias", 0.5),
        "zoom_amount": 1.0,
        "crop_anchor": "center",
        "scaling_mode": "fit" if crop_mode == "pad" else "fill",
        "final_platform_output": template["platform"],
    }


def main():
    print(get_platform_preset("tiktok"))
    print(decide_crop_vs_pad(1920, 1080, "9:16"))
    print(get_talking_head_template("tiktok"))


if __name__ == "__main__":
    main()
