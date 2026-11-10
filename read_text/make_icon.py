"""生成 assets/icon.ico（托盘图标）。

    python make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256


def build() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = int(SIZE * 0.07)
    d.rounded_rectangle(
        [pad, pad, SIZE - pad, SIZE - pad],
        radius=int(SIZE * 0.2),
        fill=(246, 247, 249, 255),
        outline=(196, 201, 209, 255),
        width=max(2, SIZE // 96),
    )

    left = int(SIZE * 0.2)
    lines = [
        (int(SIZE * 0.3), int(SIZE * 0.52)),
        (int(SIZE * 0.42), int(SIZE * 0.72)),
        (int(SIZE * 0.54), int(SIZE * 0.62)),
        (int(SIZE * 0.66), int(SIZE * 0.8)),
    ]
    thick = max(4, SIZE // 40)

    for y, x2 in lines:
        d.rounded_rectangle(
            [left, y, x2 * 10 // 10, y + thick], radius=thick // 2, fill=(74, 80, 92, 255)
        )

    # 第一行是「被选中」的状态：蓝色高亮 + 白字
    hl_y, hl_x2 = lines[0]
    d.rounded_rectangle(
        [left - thick, hl_y - thick // 2, hl_x2 + thick * 2, hl_y + thick + thick // 2],
        radius=(thick + thick // 2),
        fill=(46, 134, 222, 255),
    )
    d.rounded_rectangle(
        [left, hl_y, hl_x2, hl_y + thick], radius=thick // 2, fill=(255, 255, 255, 255)
    )

    return img


def main() -> int:
    out = Path(__file__).resolve().parent / "assets" / "icon.ico"
    img = build()
    img.save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("已生成 %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
