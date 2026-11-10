"""生成 assets/icon.ico（托盘 + 窗口 + 任务栏共用）。

图案：一个圆角方块，里面三条横杠 —— 「一列软件」的意思，16px 下也认得出。
改完图标跑一次：.venv\\Scripts\\python.exe make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
OUT = Path(__file__).resolve().parent / "assets" / "icon.ico"

TOP = (108, 164, 255)
BOTTOM = (124, 108, 255)


def rounded(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 竖向渐变
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel(
            (0, y),
            (
                int(TOP[0] + (BOTTOM[0] - TOP[0]) * t),
                int(TOP[1] + (BOTTOM[1] - TOP[1]) * t),
                int(TOP[2] + (BOTTOM[2] - TOP[2]) * t),
                255,
            ),
        )
    img = grad.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * 0.24), fill=255
    )
    img.putalpha(mask)
    return img


def draw_bars(img: Image.Image) -> Image.Image:
    size = img.size[0]
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    bar_h = int(size * 0.085)
    radius = bar_h // 2
    left = int(size * 0.235)
    right = int(size * 0.765)
    ys = [int(size * 0.30), int(size * 0.46), int(size * 0.62)]

    for index, y in enumerate(ys):
        d.rounded_rectangle(
            (left, y, right, y + bar_h), radius=radius, fill=(255, 255, 255, 255)
        )
        # 右边挖一个小圆点，像「开关」拨到了开
        cx = right - bar_h // 2
        cy = y + bar_h // 2
        dot = int(bar_h * 0.30)
        d.ellipse((cx - dot, cy - dot, cx + dot, cy + dot), fill=(124, 108, 255, 255))

    return Image.alpha_composite(img, layer)


def main() -> int:
    base = draw_bars(rounded(SIZE))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base.save(
        OUT,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"已生成 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
