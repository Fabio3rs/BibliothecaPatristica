import os
import subprocess
import sys

FONTS = [
    {
        "src": "/usr/share/fonts/truetype/gentiumplus/GentiumPlus-Regular.ttf",
        "out": "web/public/fonts/GentiumPlus-Regular.woff2",
        "unicodes": "U+0000-00FF,U+0100-024F,U+0250-02AF,U+0370-03FF,U+1E00-1EFF,U+1F00-1FFF,U+2000-206F,U+2070-209F,U+20A0-20CF,U+2100-214F,U+2600-26FF",
    },
    {
        "src": "/usr/share/fonts/truetype/gentiumplus/GentiumPlus-Italic.ttf",
        "out": "web/public/fonts/GentiumPlus-Italic.woff2",
        "unicodes": "U+0000-00FF,U+0100-024F,U+0250-02AF,U+0370-03FF,U+1E00-1EFF,U+1F00-1FFF,U+2000-206F,U+2070-209F,U+20A0-20CF,U+2100-214F,U+2600-26FF",
    },
    {
        "src": "/usr/share/fonts/truetype/gentiumplus/GentiumPlus-Bold.ttf",
        "out": "web/public/fonts/GentiumPlus-Bold.woff2",
        "unicodes": "U+0000-00FF,U+0100-024F,U+0250-02AF,U+0370-03FF,U+1E00-1EFF,U+1F00-1FFF,U+2000-206F,U+2070-209F,U+20A0-20CF,U+2100-214F,U+2600-26FF",
    },
    {
        "src": "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
        "out": "web/public/fonts/Inter-Regular.woff2",
        "unicodes": "U+0000-00FF,U+0100-024F,U+0250-02AF,U+1E00-1EFF,U+2000-206F,U+20A0-20CF",
    },
    {
        "src": "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf",
        "out": "web/public/fonts/Inter-SemiBold.woff2",
        "unicodes": "U+0000-00FF,U+0100-024F,U+0250-02AF,U+1E00-1EFF,U+2000-206F,U+20A0-20CF",
    },
    {
        "src": "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
        "out": "web/public/fonts/Inter-Bold.woff2",
        "unicodes": "U+0000-00FF,U+0100-024F,U+0250-02AF,U+1E00-1EFF,U+2000-206F,U+20A0-20CF",
    },
    {
        "src": "/usr/share/fonts/truetype/noto/NotoSansSyriac-Regular.ttf",
        "out": "web/public/fonts/NotoSansSyriac-Regular.woff2",
        "unicodes": "U+0700-074F,U+0860-086F",
    },
    {
        "src": "/usr/share/fonts/truetype/noto/NotoSansCoptic-Regular.ttf",
        "out": "web/public/fonts/NotoSansCoptic-Regular.woff2",
        "unicodes": "U+03E2-03EF,U+2C80-2CFF",
    },
    {
        "src": "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "out": "web/public/fonts/NotoNaskhArabic-Regular.woff2",
        "unicodes": "U+0600-06FF,U+0750-077F,U+08A0-08FF,U+FB50-FDFF,U+FE70-FEFF",
    },
    {
        "src": "/usr/share/fonts/truetype/noto/NotoSerifHebrew-Regular.ttf",
        "out": "web/public/fonts/NotoSerifHebrew-Regular.woff2",
        "unicodes": "U+0590-05FF,U+FB1D-FB4F",
    },
    {
        "src": "/usr/share/fonts/truetype/noto/NotoSerifArmenian-Regular.ttf",
        "out": "web/public/fonts/NotoSerifArmenian-Regular.woff2",
        "unicodes": "U+0530-058F,U+FB13-FB17",
    },
    {
        "src": "/usr/share/fonts/truetype/noto/NotoSerifEthiopic-Regular.ttf",
        "out": "web/public/fonts/NotoSerifEthiopic-Regular.woff2",
        "unicodes": "U+1200-137F,U+1380-139F,U+2D80-2DDF,U+AB00-AB2F",
    },
]

env = os.environ.copy()
env["PYTHONPATH"] = "/usr/lib/python3/dist-packages"
pyftsubset = "/homessddata/Projects/pdfocr/.venv/bin/pyftsubset"

for item in FONTS:
    cmd = [
        pyftsubset,
        item["src"],
        f"--unicodes={item['unicodes']}",
        "--flavor=woff2",
        f"--output-file={item['out']}",
    ]
    print(f"Generating {item['out']} from {item['src']}...")
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error generating {item['out']}:", res.stderr)
        sys.exit(1)

print("All fonts successfully converted to WOFF2!")
