from PIL import Image

from smclipy.images import crop_image_1_to_1, is_image_1_to_1, is_image_pillarbox


def _write_image(path, size, color):
    Image.new("RGB", size, color).save(path)
    return path


def test_is_image_1_to_1_square(tmp_path):
    img = _write_image(tmp_path / "square.png", (100, 100), "red")
    assert is_image_1_to_1(img) is True


def test_is_image_1_to_1_non_square(tmp_path):
    img = _write_image(tmp_path / "wide.png", (200, 100), "red")
    assert is_image_1_to_1(img) is False


def test_crop_image_1_to_1_produces_square(tmp_path):
    img = _write_image(tmp_path / "wide.png", (200, 100), "red")
    crop_image_1_to_1(img)
    with Image.open(img) as result:
        assert result.size == (100, 100)


def test_is_image_pillarbox_detects_bars(tmp_path):
    img = tmp_path / "pillarbox.png"
    picture = Image.new("RGB", (200, 100), "black")
    for x in range(60, 140):
        for y in range(100):
            picture.putpixel((x, y), (255, 0, 0))
    picture.save(img)
    assert is_image_pillarbox(img) is True


def test_is_image_pillarbox_false_for_square(tmp_path):
    img = _write_image(tmp_path / "square.png", (100, 100), "red")
    assert is_image_pillarbox(img) is False


def test_is_image_pillarbox_false_for_gradient_edges(tmp_path):
    img = tmp_path / "gradient.png"
    picture = Image.new("RGB", (200, 100))
    for x in range(200):
        for y in range(100):
            picture.putpixel((x, y), (y, 0, 0))
    picture.save(img)
    assert is_image_pillarbox(img) is False
