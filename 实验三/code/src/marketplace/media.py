"""Validate and normalize uploaded pictures before a database write."""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from .domain import BusinessError

MAX_BYTES = 8 * 1024 * 1024
MAX_PHOTOS = 6


def prepare_photo(data):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        raise BusinessError("单张图片须为 1 字节至 8 MiB")
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as image:
            width, height = image.size
            if max(width, height) > 4096 or width * height > 12_000_000:
                raise BusinessError("图片最长边不得超过 4096 像素，总像素不得超过 1200 万")
            if getattr(image, "n_frames", 1) != 1:
                raise BusinessError("请选择静态 JPEG 或 PNG 图片")
            image.verify()
        with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as image:
            picture = ImageOps.exif_transpose(image).convert("RGB")
            content = BytesIO()
            picture.save(content, format="JPEG", quality=90)
            picture.thumbnail((72, 48))
            thumbnail = BytesIO()
            picture.save(thumbnail, format="PNG")
            return content.getvalue(), thumbnail.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise BusinessError("图片损坏或不是有效的 JPEG / PNG") from exc


def prepare_photos(photos):
    if not isinstance(photos, (list, tuple)) or len(photos) > MAX_PHOTOS:
        raise BusinessError("每件商品最多保存 6 张图片")
    return [photo if type(photo) is int else prepare_photo(photo) for photo in photos]


def replace_photos(conn, product_id, photos):
    existing = {
        row["id"]: (row["content"], row["thumbnail"])
        for row in conn.execute(
            "SELECT id,content,thumbnail FROM product_photos WHERE product_id=?", (product_id,)
        )
    }
    selected = set()
    rows = []
    for position, photo in enumerate(photos):
        if isinstance(photo, int):
            if photo not in existing or photo in selected:
                raise BusinessError("图片不属于该商品或被重复选中，请刷新后重试")
            selected.add(photo)
            photo = existing[photo]
        rows.append((product_id, position, *photo))
    conn.execute("DELETE FROM product_photos WHERE product_id=?", (product_id,))
    conn.executemany(
        "INSERT INTO product_photos(product_id,position,content,thumbnail) VALUES(?,?,?,?)", rows
    )
