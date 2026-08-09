import unittest

from PIL import Image, ImageDraw

from ashare_quant.huatai_desktop_monitor import analyze_grid_image


class HuataiDesktopMonitorTests(unittest.TestCase):
    def image(self, with_body=False):
        image = Image.new("RGB", (600, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.line((0, 37, 599, 37), fill="black", width=1)
        draw.text((10, 10), "header", fill="black")
        if with_body:
            draw.rectangle((20, 60, 220, 72), fill="black")
        return image

    def test_empty_grid_is_verified(self):
        result = analyze_grid_image(self.image())
        self.assertEqual(result.status, "empty_verified")
        self.assertEqual(result.separator_row, 37)
        self.assertEqual(result.body_dark_rows, 0)

    def test_nonempty_grid_fails_closed(self):
        result = analyze_grid_image(self.image(with_body=True))
        self.assertEqual(result.status, "nonempty_unreadable")
        self.assertGreater(result.body_dark_rows, 0)

    def test_missing_separator_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "separator"):
            analyze_grid_image(Image.new("RGB", (600, 300), "white"))


if __name__ == "__main__":
    unittest.main()
