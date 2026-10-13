from app.routers.promotion_candidates_report import _promotion_projection
import pytest


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://catalog.test/photo.jpg", "https://catalog.test/photo.jpg"),
        (None, None),
        ("", None),
        ("javascript:alert(1)", None),
        ("data:image/png;base64,AA", None),
        ("https://user:secret@catalog.test/photo.jpg", None),
    ],
)
def test_projection_keeps_only_public_image_links(value, expected):
    assert _promotion_projection({"image_url": value})["image_url"] == expected
