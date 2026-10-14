from jokerps5.images import Screenshot
from fake_server import make_tiny_png


def test_to_png_downscales():
    png_bytes = make_tiny_png()  # 4000x2000
    shot = Screenshot(data=png_bytes, format="png", path="/data/x.png")
    out = shot.to_png(max_width=1280)

    from PIL import Image
    import io

    img = Image.open(io.BytesIO(out))
    assert img.width == 1280
    assert img.height == 640


def test_to_png_no_downscale_when_smaller():
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (100, 50), color=(1, 2, 3)).save(buf, format="PNG")
    shot = Screenshot(data=buf.getvalue(), format="png", path="/data/x.png")
    out = shot.to_png(max_width=1280)

    img = Image.open(io.BytesIO(out))
    assert img.width == 100
    assert img.height == 50
