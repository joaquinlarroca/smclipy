from pathlib import Path

import climage
from PIL import Image, ImageOps, ImageStat


def crop_image_1_to_1(path: Path) -> None:
    with Image.open(str(path)) as image:
        side: int = min(image.size)
        ImageOps.fit(image, (side, side)).save(str(path))


def is_image_1_to_1(path: Path) -> bool:
    with Image.open(str(path)) as image:
        width, height = image.size
    return width == height


def is_image_pillarbox(path: Path) -> bool:
    std_threshold = 5
    max_analysis_size = 1024
    with Image.open(str(path)) as image:
        img_rgb: Image.Image = image.convert("RGB")
        width, height = img_rgb.size
        scale: float = min(1.0, max_analysis_size / width, max_analysis_size / height)
        if scale < 1.0:
            new_width: int = round(width * scale)
            new_height: int = round(height * scale)
            img_rgb = img_rgb.resize((new_width, new_height), Image.Resampling.BILINEAR)
            width, height = img_rgb.size

    if height == width:
        return False

    def col_is_solid(x: int) -> bool:
        strip: Image.Image = img_rgb.crop((x, 0, x + 1, height))
        return max(ImageStat.Stat(strip).stddev) < std_threshold

    return col_is_solid(0) or col_is_solid(width - 1)


def display_image(path: Path) -> None:
    image = climage.convert(str(path), is_unicode=True)
    print(image)
