"""Draws the bot's profile picture (assets/avatar.png)."""
import os, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont
S = 1024
PAPER, INK, GOLD, GOLDD, SAGE = (246,241,233), (43,38,32), (176,136,80), (138,100,54), (125,138,106)
img = Image.new("RGB", (S, S), INK)
DX, DY = 45, 12
d = ImageDraw.Draw(img)
# soft gold glow in the centre
glow = Image.new("L", (S, S), 0); ImageDraw.Draw(glow).ellipse([170,170,854,854], fill=90)
img.paste(GOLD, mask=glow.filter(ImageFilter.GaussianBlur(120)))
def card(box, fill, angle, shadow=True):
    w, h = box[2]-box[0], box[3]-box[1]
    layer = Image.new("RGBA", (S, S), (0,0,0,0)); ld = ImageDraw.Draw(layer)
    ld.rounded_rectangle(box, 28, fill=fill)
    return layer.rotate(angle, center=((box[0]+box[2])/2, (box[1]+box[3])/2), resample=Image.BICUBIC)
def put(layer, shadow=True):
    if shadow:
        sh = Image.new("RGBA", (S,S), (0,0,0,0)); sh.putalpha(layer.split()[-1].point(lambda a: a*0.45))
        sh = sh.filter(ImageFilter.GaussianBlur(18)); img.paste((0,0,0), (8,16), sh.split()[-1])
    img.paste(layer, (0,0), layer)
# two photos behind (the solution images)
put(card((250,250,640,740), SAGE+(255,), 14))
put(card((300,230,690,720), (214,196,166,255), 6))
# main document
doc = (330,200,730,760)
layer = Image.new("RGBA", (S,S), (0,0,0,0)); ld = ImageDraw.Draw(layer)
ld.rounded_rectangle(doc, 30, fill=PAPER+(255,))
ld.polygon([(625,190),(740,305),(740,190)], fill=(0,0,0,0))   # cut corner (incl. rounding)
ld.polygon([(630,200),(630,300),(730,300)], fill=(222,208,184,255))  # fold
for i, y in enumerate(range(330, 520, 46)):
    ld.rounded_rectangle((385, y, 675 - (90 if i == 3 else 0), y+18), 9, fill=(205,193,175,255))
put(layer)
# PDF badge
d = ImageDraw.Draw(img)
d.rounded_rectangle((250,560,610,720), 34, fill=GOLD)
font = ImageFont.truetype(sys.argv[1], 120)
d.text((430,640), "PDF", fill=PAPER, font=font, anchor="mm")
bg = Image.new("RGB", (S, S), INK)
g2 = Image.new("L", (S, S), 0); ImageDraw.Draw(g2).ellipse([170,170,854,854], fill=90)
bg.paste(GOLD, mask=g2.filter(ImageFilter.GaussianBlur(120)))
mask = Image.new("L", (S, S), 0); ImageDraw.Draw(mask).rectangle([0,0,S-DX-1,S-DY-1], fill=255)
bg.paste(img.crop((0,0,S-DX,S-DY)), (DX,DY))
img = bg
img = img.resize((640,640), Image.LANCZOS)
img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "avatar.png"))
print("saved")
