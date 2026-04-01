"""Generate OG image for 공약 코치 page — bold typography, clean layout."""
from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630

# Colors
BG1 = (10, 28, 54)
BG2 = (16, 50, 82)
BRAND = (255, 114, 16)
WHITE = (248, 250, 252)
LIGHT = (226, 232, 240)
CYAN = (56, 189, 248)

# --- Gradient background ---
img = Image.new("RGB", (W, H), BG1)
draw = ImageDraw.Draw(img)
for y in range(H):
    t = y / H
    c = tuple(int(BG1[i] + (BG2[i] - BG1[i]) * t) for i in range(3))
    draw.line([(0, y), (W, y)], fill=c)

# --- Orange accent bar (left edge) ---
draw.rectangle([0, 0, 8, H], fill=BRAND)

# --- Large orange glow (subtle, top area) ---
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
for r in range(500, 0, -3):
    a = int(40 * (1 - r / 500) ** 1.2)
    gd.ellipse([80 - r, 120 - r, 80 + r, 120 + r], fill=(255, 114, 16, a))
for r in range(400, 0, -3):
    a = int(25 * (1 - r / 400) ** 1.2)
    gd.ellipse([1050 - r, 500 - r, 1050 + r, 500 + r], fill=(56, 189, 248, a))
img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
draw = ImageDraw.Draw(img)

# --- Fonts ---
f_badge = ImageFont.truetype("malgunbd.ttf", 22)
f_title = ImageFont.truetype("malgunbd.ttf", 80)
f_sub = ImageFont.truetype("malgunbd.ttf", 36)
f_desc = ImageFont.truetype("malgun.ttf", 24)
f_url = ImageFont.truetype("malgunbd.ttf", 22)

x = 80

# --- "개혁신당" badge ---
draw.rounded_rectangle([x, 80, x + 160, 118], radius=6, fill=BRAND)
draw.text((x + 13, 82), "개혁신당", fill=(10, 20, 42), font=f_badge)

# --- Main title: "AI 공약 코치" ---
draw.text((x, 140), "AI 공약 코치", fill=WHITE, font=f_title)

# --- Horizontal divider line ---
draw.rectangle([x, 245, x + 120, 249], fill=BRAND)

# --- Subtitle ---
draw.text((x, 272), "공약 방향을 잡아주고", fill=LIGHT, font=f_sub)
draw.text((x, 318), "정강정책과 연결해 함께 만듭니다", fill=LIGHT, font=f_sub)

# --- Three feature keywords (inline, separated by ·) ---
features = "지역 현안 분석  ·  당 정책 연계  ·  공약 방향 정리"
draw.text((x, 400), features, fill=(148, 163, 184), font=f_desc)

# --- URL pill ---
draw.rounded_rectangle([x, 470, x + 370, 520], radius=999, fill=BRAND)
draw.text((x + 22, 482), "policy.reformparty.kr/pledge", fill=(10, 20, 42), font=f_url)

# --- Right side: large decorative quotation mark ---
f_deco = ImageFont.truetype("malgunbd.ttf", 280)
deco_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
dd = ImageDraw.Draw(deco_layer)
dd.text((820, 100), "\u201C", fill=(255, 255, 255, 22), font=f_deco)
img = Image.alpha_composite(img.convert("RGBA"), deco_layer).convert("RGB")

# --- Save ---
img.save("static/og-coach.png", "PNG", optimize=True)
sz = img.size
import os
kb = os.path.getsize("static/og-coach.png") // 1024
print(f"Saved: static/og-coach.png ({sz[0]}x{sz[1]}, {kb}KB)")
