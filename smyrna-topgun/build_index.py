#!/usr/bin/env python3
"""Regenerate topgun/index.html from whatever is actually in img/.

Bug this prevents: photos were added to img/ and committed, but index.html was
hand-written with a fixed list of 60. The client saw 60 of 152. Always rebuild
the grid from the directory listing, never from a hardcoded count.

Delivery contract (Amber / City of Smyrna): 160 photos, each viewable full
screen on the page AND downloadable individually, plus one zip of all 160.
The whole page is generated here so the count can never drift from img/.
"""
import pathlib
import struct

HERE = pathlib.Path(__file__).parent
IMG = HERE / "img"
THUMB = IMG / "thumb"

photos = sorted(p.name for p in IMG.iterdir()
                if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
n = len(photos)

missing_thumbs = [f for f in photos if not (THUMB / f).exists()]
if missing_thumbs:
    raise SystemExit(
        f"{len(missing_thumbs)} photos have no thumb (e.g. {missing_thumbs[:3]}). Run:\n"
        "  magick mogrify -path img/thumb -resize 640x640 -quality 80 -strip "
        "-interlace Plane -colorspace sRGB img/*.jpg"
    )


def jpeg_dims(path):
    """Read WxH straight out of the JPEG SOF marker (no PIL, no sips-per-file)."""
    data = path.read_bytes()
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return w, h
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return 0, 0


dims = {f: jpeg_dims(THUMB / f) for f in photos}

zip_path = HERE / "smyrna-topgun-balocka.zip"
zip_mb = round(zip_path.stat().st_size / 1_000_000) if zip_path.exists() else 0

# Grid tiles load the 640px thumb (~38 KB), not the 2048px deliverable (~335 KB).
# 160 full-size tiles was a ~53 MB page: on phone data that is a spinner, not a gallery.
# The lightbox still opens img/<name> and "Save this photo" still saves the full file.
tiles = "".join(
    f'<button class="ph" data-i="{i-1}" aria-label="View photo {i}">'
    f'<img loading="lazy" decoding="async" width="{dims[f][0]}" height="{dims[f][1]}" '
    f'src="img/thumb/{f}" alt="9/11 Top Gun Run photo {i}"></button>'
    for i, f in enumerate(photos, 1)
)
files_js = "[" + ",".join(f'"{f}"' for f in photos) + "]"

HEAD = '''<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<!-- balocka-og -->
<meta name="description" content="Event coverage for the City of Smyrna's 9/11 Top Gun Run by Balocka Creative.">
<link rel="canonical" href="https://gooddayserviceshq-debug.github.io/balocka-catalog/smyrna-topgun/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Balocka Creative">
<meta property="og:url" content="https://gooddayserviceshq-debug.github.io/balocka-catalog/smyrna-topgun/">
<meta property="og:title" content="9/11 Top Gun Run &mdash; City of Smyrna">
<meta property="og:description" content="Event coverage for the City of Smyrna's 9/11 Top Gun Run by Balocka Creative.">
<meta property="og:image" content="https://gooddayserviceshq-debug.github.io/balocka-catalog/og/og-topgun.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="9/11 Top Gun Run &mdash; City of Smyrna &mdash; Balocka Creative">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="9/11 Top Gun Run &mdash; City of Smyrna">
<meta name="twitter:description" content="Event coverage for the City of Smyrna's 9/11 Top Gun Run by Balocka Creative.">
<meta name="twitter:image" content="https://gooddayserviceshq-debug.github.io/balocka-catalog/og/og-topgun.jpg">
<!-- /balocka-og -->
<title>9/11 Top Gun Run &mdash; City of Smyrna | Balocka Creative</title><style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0D0D1A;color:#EDEAF5;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{background:linear-gradient(135deg,#1a2f5e,#B22234);border-bottom:3px solid #fff;padding:30px 20px;text-align:center}
h1{letter-spacing:.04em;font-size:clamp(1.3rem,4vw,2rem)} header p{opacity:.9;margin-top:8px}
.dl{display:inline-block;margin-top:14px;background:#fff;color:#B22234;font-weight:800;padding:12px 26px;border-radius:10px;text-decoration:none}
.hint{max-width:760px;margin:18px auto 0;padding:0 20px;text-align:center;color:#B9B3CE;font-size:.92rem;line-height:1.5}
.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px;padding:16px;max-width:1500px;margin:auto}
.ph{border:0;padding:0;background:#15152a;border-radius:8px;overflow:hidden;display:block;cursor:zoom-in}
.ph img{width:100%;height:auto;display:block;background:#15152a}
footer{text-align:center;padding:24px;color:#9A93B5;font-size:.9rem;line-height:1.6}
#lb{position:fixed;inset:0;background:rgba(5,5,12,.97);display:none;z-index:99;flex-direction:column}
#lb.on{display:flex}
#lbimg{flex:1;min-height:0;object-fit:contain;width:100%;padding:8px}
.bar{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;padding:12px 14px;background:#0D0D1A;border-top:1px solid #2a2a44}
.bar a,.bar button{background:#1c1c33;color:#EDEAF5;border:1px solid #33335a;border-radius:8px;padding:10px 16px;font-size:.95rem;font-weight:600;text-decoration:none;cursor:pointer}
.bar a.save{background:#fff;color:#B22234;border-color:#fff}
#count{color:#9A93B5;font-size:.9rem;min-width:92px;text-align:center}
#x{position:absolute;top:10px;right:14px;background:rgba(0,0,0,.5);border:0;color:#fff;font-size:1.6rem;line-height:1;padding:8px 14px;border-radius:8px;cursor:pointer;z-index:2}
</style></head><body>
<header><h1>&#127482;&#127480; 9/11 Top Gun Run &mdash; City of Smyrna</h1>
<p>Full event gallery &middot; {N} photos &middot; shot by Blake McConnell</p>
<a class="dl" href="smyrna-topgun-balocka.zip" download>&#11015; Download all {N} ({MB} MB zip)</a></header>
<p class="hint">Tap any photo to open it full screen, then use <b>Save this photo</b> to download
that one. Arrow keys or swipe move through the set. The button above downloads all {N} at once.</p>
'''

FOOT = '''<footer>All {N} photos are free for City of Smyrna event use &middot; credit appreciated<br>
Full-resolution originals available on request<br>
&#128247; @balocka_creative &mdash; Blake McConnell</footer>
<div id="lb" role="dialog" aria-modal="true">
<button id="x" aria-label="Close">&times;</button>
<img id="lbimg" alt="">
<div class="bar">
<button id="prev">&#8592; Prev</button>
<span id="count"></span>
<button id="next">Next &#8594;</button>
<a id="save" class="save" href="#" download>&#11015; Save this photo</a>
</div></div>
<script>
var F={FILES},i=0,lb=document.getElementById('lb'),im=document.getElementById('lbimg'),
sv=document.getElementById('save'),ct=document.getElementById('count');
function preload(k){var f=F[(k+F.length)%F.length];var p=new Image();p.src='img/'+f;}
function show(k){i=(k+F.length)%F.length;var f=F[i];
/* thumb paints instantly, full-res swaps in when it lands -- no blank frame on phone data */
im.src='img/thumb/'+f;
var big=new Image();big.onload=function(){if(F[i]===f)im.src='img/'+f;};big.src='img/'+f;
sv.href='img/'+f;
sv.setAttribute('download',f);ct.textContent=(i+1)+' / '+F.length;lb.classList.add('on');
preload(i+1);preload(i-1);
document.body.style.overflow='hidden';}
function closeLb(){lb.classList.remove('on');im.src='';document.body.style.overflow='';}
document.querySelectorAll('.ph').forEach(function(b){b.onclick=function(){show(+b.dataset.i)}});
document.getElementById('x').onclick=closeLb;
document.getElementById('prev').onclick=function(){show(i-1)};
document.getElementById('next').onclick=function(){show(i+1)};
lb.onclick=function(e){if(e.target===lb||e.target===im)closeLb()};
document.onkeydown=function(e){if(!lb.classList.contains('on'))return;
if(e.key==='Escape')closeLb();if(e.key==='ArrowLeft')show(i-1);if(e.key==='ArrowRight')show(i+1)};
var sx=null;lb.addEventListener('touchstart',function(e){sx=e.touches[0].clientX},{passive:true});
lb.addEventListener('touchend',function(e){if(sx===null)return;var d=e.changedTouches[0].clientX-sx;
if(Math.abs(d)>50)show(i+(d<0?1:-1));sx=null},{passive:true});
</script>
</body></html>'''

html = (HEAD.replace("{N}", str(n)).replace("{MB}", str(zip_mb))
        + f'<div class="g">{tiles}</div>'
        + FOOT.replace("{N}", str(n)).replace("{FILES}", files_js))

(HERE / "index.html").write_text(html)
print(f"index.html rebuilt: {n} photos, zip {zip_mb} MB")
