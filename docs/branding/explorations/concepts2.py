import math
R = 185
CX = CY = 512
RX, RY, ROT, A = 228, 78, math.radians(-12), math.radians(28)

def pt(a):
    x, y = RX * math.cos(a), RY * math.sin(a)
    c, s = math.cos(ROT), math.sin(ROT)
    return CX + x * c - y * s, CY + x * s + y * c

def half(upper):
    a0, a1 = (math.pi, 2 * math.pi) if upper else (0, math.pi)
    return 'M' + ' L'.join('%.1f,%.1f' % pt(a0 + (a1 - a0) * i / 64) for i in range(65))

def full():
    # back (upper) half only: the orbit goes behind the axis; the gap mask keeps it off the dots
    return 'M' + ' L'.join('%.1f,%.1f' % pt(math.pi + math.pi * i / 64) for i in range(65))

def sphere(p, name, hi, mid, lo):
    return (f'<radialGradient id="{p}{name}" cx="0.36" cy="0.3" r="0.78">'
            f'<stop offset="0" stop-color="{hi}"/><stop offset="0.5" stop-color="{mid}"/><stop offset="1" stop-color="{lo}"/></radialGradient>')

def hexagon(x, y, r, color, w):
    pts = ' '.join('%.1f,%.1f' % (x + r * math.cos(math.radians(30 + 60 * k)), y + r * math.sin(math.radians(30 + 60 * k))) for k in range(6))
    return f'<polygon points="{pts}" fill="none" stroke="{color}" stroke-width="{w}" stroke-linejoin="round"/>'

def spark(x, y, r, color, w):
    rays = []
    for k in range(6):
        ang = math.radians(90 + 60 * k)
        rays.append(f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + r * math.cos(ang):.1f}" y2="{y - r * math.sin(ang):.1f}" stroke="{color}" stroke-width="{w}" stroke-linecap="round"/>')
    return ''.join(rays)

def icon(p, bg, sheen, axis, ring, back_fill, front_fill, defs, back_r=100, front_r=112, back_extra=None, front_extra=None, back_op=1.0):
    bx, by = pt(A + math.pi)
    fx, fy = pt(A)
    return f'''<defs>{defs}
    <linearGradient id="{p}sheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFFFFF" stop-opacity="{sheen}"/><stop offset="0.55" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="{R}" fill="{bg}"/>
  <rect x="100" y="100" width="824" height="824" rx="{R}" fill="url(#{p}sheen)"/>
  <ellipse cx="512" cy="{CY + 14}" rx="338" ry="118" transform="rotate(-12 512 {CY + 14})" fill="none" stroke="{ring}" stroke-opacity="0.26" stroke-width="12"/>
  <g opacity="{back_op}"><circle cx="{bx:.1f}" cy="{by:.1f}" r="{back_r}" fill="{back_fill}"/>{back_extra(bx, by) if back_extra else ''}</g>
  <rect x="489" y="226" width="46" height="572" rx="23" fill="{axis}"/>
  <circle cx="{fx:.1f}" cy="{fy:.1f}" r="{front_r}" fill="{front_fill}"/>{front_extra(fx, fy) if front_extra else ''}'''

def a2(p):
    # A, enlarged: neutral ink glass, white Codex dot behind, ice Claude dot in front
    defs = (f'<linearGradient id="{p}bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2A3442"/><stop offset="1" stop-color="#0A0E14"/></linearGradient>'
            f'<linearGradient id="{p}ax" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#C9D6E3"/></linearGradient>'
            + sphere(p, 'w', '#FFFFFF', '#DCE3EA', '#9AA8B6') + sphere(p, 'b', '#D4F1FF', '#6CCBFF', '#1A86CC'))
    return icon(p, f'url(#{p}bg)', 0.16, f'url(#{p}ax)', '#FFFFFF', f'url(#{p}w)', f'url(#{p}b)', defs, back_op=0.75)

def d1(p):
    # D1 "tones", dark: OpenAI monochrome white dot, Claude terracotta dot, graphite field
    defs = (f'<linearGradient id="{p}bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2B2A28"/><stop offset="1" stop-color="#0E0E0D"/></linearGradient>'
            f'<linearGradient id="{p}ax" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#FAF9F5"/><stop offset="1" stop-color="#D8D4CA"/></linearGradient>'
            + sphere(p, 'w', '#FFFFFF', '#ECECEC', '#A9A9A9') + sphere(p, 'c', '#F2B095', '#D97757', '#A84E2F'))
    return icon(p, f'url(#{p}bg)', 0.12, f'url(#{p}ax)', '#FAF9F5', f'url(#{p}w)', f'url(#{p}c)', defs)

def d2(p):
    # D2 "tones + hints", light ivory: black dot with a hexagon facet, terracotta dot with a cream spark
    defs = (f'<linearGradient id="{p}bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FBFAF6"/><stop offset="1" stop-color="#E9E5DA"/></linearGradient>'
            + sphere(p, 'k', '#4A4A4A', '#1A1A1A', '#000000') + sphere(p, 'c', '#EFA383', '#D97757', '#B5583A'))
    return icon(p, f'url(#{p}bg)', 0.0, '#1A1A1A', '#1A1A1A', f'url(#{p}k)', f'url(#{p}c)', defs,
                back_extra=lambda x, y: hexagon(x, y, 52, '#F5F3EE', 12),
                front_extra=lambda x, y: spark(x, y, 50, '#FAF6EE', 15))

concepts = [('A2 · нейтральный', a2), ('D1 · тона', d1), ('D2 · тона + намёки', d2)]

def render(f, p):
    return f(p)

for i, (name, f) in enumerate(concepts):
    open(f'concept2_{i}.svg', 'w').write(f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">{render(f, "c%d" % i)}</svg>')

W, colw = 1500, 500
parts = []
for i, (name, f) in enumerate(concepts):
    x = i * colw
    parts.append(f'<text x="{x + 250}" y="70" font-family="-apple-system, Helvetica" font-size="34" font-weight="600" text-anchor="middle" fill="#1C1C1E">{name}</text>')
    parts.append(f'<svg x="{x + 30}" y="90" width="440" height="440" viewBox="0 0 1024 1024">{render(f, "s%d" % i)}</svg>')
    for row, bg in enumerate(['#F2F4F7', '#1E1E22']):
        y = 600 + row * 230
        parts.append(f'<rect x="{x + 15}" y="{y}" width="470" height="210" rx="20" fill="{bg}"/>')
        sx = x + 30
        for sz in (160, 80, 40, 20):
            parts.append(f'<svg x="{sx}" y="{y + 105 - sz / 2}" width="{sz}" height="{sz}" viewBox="0 0 1024 1024">{render(f, "z%d%d%d" % (row, sz, i))}</svg>')
            sx += sz + 34
# bottom: menu-bar/dock-like row at real-ish sizes for D1 vs A2
open('sheet2.svg', 'w').write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}"><rect width="{W}" height="{W}" fill="#FFFFFF"/>{"".join(parts)}</svg>')
print('ok')
