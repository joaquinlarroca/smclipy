from pathlib import Path

import climage
from PIL import Image, ImageOps, ImageStat


def crop_image_1_to_1(path: Path) -> None:
    with Image.open(str(path)) as image:
        height = image.size[1]
        ImageOps.fit(image, (height, height)).save(str(path))


def is_image_1_to_1(path: Path) -> bool:
    with Image.open(str(path)) as image:
        width, height = image.size
    return width == height


def is_image_pillarbox(path: Path) -> bool:
    std_threshold = 5
    color_diff_threshold = 10
    with Image.open(str(path)) as image:
        img_rgb = image.convert("RGB")
        width, height = img_rgb.size

    if height == width:
        return False

    def col_stats(x: int) -> tuple[list[float], list[float]]:
        stat = ImageStat.Stat(img_rgb.crop((x, 0, x + 1, height)))
        return stat.mean, stat.stddev

    def col_is_solid(x: int) -> bool:
        _, stddev = col_stats(x)
        return max(stddev) < std_threshold

    left_solid = col_is_solid(0)
    right_solid = col_is_solid(width - 1)

    if not (left_solid or right_solid):
        return False

    def padding_width(from_left: bool) -> int:
        ref_mean, _ = col_stats(0 if from_left else width - 1)
        rng = range(width) if from_left else range(width - 1, -1, -1)
        count = 0
        for x in rng:
            mean, stddev = col_stats(x)
            if (
                max(stddev) < std_threshold
                and max(abs(m - r) for m, r in zip(mean, ref_mean, strict=True))
                < color_diff_threshold
            ):
                count += 1
            else:
                break
        return count

    left_pad = padding_width(True) if left_solid else 0
    right_pad = padding_width(False) if right_solid else 0

    return (left_pad + right_pad) > 0


def display_image(path: Path) -> None:
    image = climage.convert(str(path), is_unicode=True)
    print(image)
