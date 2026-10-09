import math
R = 185  # squircle-ish corner for 824 body at 100,100

def orbit_pt(cx, cy, rx, ry, a, rot):
    x, y = rx * math.cos(a), ry * math.sin(a)
    c, s = math.cos(rot), math.sin(rot)
    return cx + x * c - y * s, cy + x * s + y * c

def half_ellipse(cx, cy, rx, ry, rot_deg, upper):
    # path of the upper (back) or lower (front) half of a rotated ellipse
    r = math.radians(rot_deg)
    a0, a1 = (math.pi, 2 * math.pi) if upper else (0, math.pi)
    pts = [orbit_pt(cx, cy, rx, ry, a0 + (a1 - a0) * i / 60, r) for i in range(61)]
    return 'M' + ' L'.join(f'{x:.1f},{y:.1f}' for x, y in pts)

def concept_a(p):
    # "Orbit": dark ink glass, white Codex dot behind, ice Claude dot in front
    cx = cy = 512; rx, ry, rot = 262, 96, -14
    a = math.radians(50)
    back = orbit_pt(cx, cy, rx, ry, a + math.pi, math.radians(rot))
    front = orbit_pt(cx, cy, rx, ry, a, math.radians(rot))
    return f'''
  <defs>
    <linearGradient id="{p}bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#26303D"/><stop offset="1" stop-color="#0A0E14"/></linearGradient>
    <linearGradient id="{p}ax" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#C9D6E3"/></linearGradient>
    <radialGradient id="{p}blue" cx="0.35" cy="0.3" r="0.8"><stop offset="0" stop-color="#C8EEFF"/><stop offset="0.45" stop-color="#6CCBFF"/><stop offset="1" stop-color="#1F8FD6"/></radialGradient>
    <radialGradient id="{p}white" cx="0.35" cy="0.3" r="0.8"><stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#B9C4D0"/></radialGradient>
    <radialGradient id="{p}glow" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#6CCBFF" stop-opacity="0.55"/><stop offset="1" stop-color="#6CCBFF" stop-opacity="0"/></radialGradient>
    <linearGradient id="{p}sheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFFFFF" stop-opacity="0.16"/><stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="{R}" fill="url(#{p}bg)"/>
  <rect x="100" y="100" width="824" height="824" rx="{R}" fill="url(#{p}sheen)"/>
  <path d="{half_ellipse(cx, cy, rx, ry, rot, True)}" fill="none" stroke="#FFFFFF" stroke-opacity="0.16" stroke-width="7" stroke-linecap="round"/>
  <circle cx="{back[0]:.1f}" cy="{back[1]:.1f}" r="54" fill="url(#{p}white)" opacity="0.62"/>
  <rect x="497" y="236" width="30" height="552" rx="15" fill="url(#{p}ax)"/>
  <path d="{half_ellipse(cx, cy, rx, ry, rot, False)}" fill="none" stroke="#FFFFFF" stroke-opacity="0.30" stroke-width="7" stroke-linecap="round"/>
  <circle cx="{front[0]:.1f}" cy="{front[1]:.1f}" r="150" fill="url(#{p}glow)"/>
  <circle cx="{front[0]:.1f}" cy="{front[1]:.1f}" r="68" fill="url(#{p}blue)"/>
'''

def concept_b(p):
    # "Reflection": pale ice, ink dot and its blue reflection, two half-orbits read as a sync loop
    return f'''
  <defs>
    <linearGradient id="{p}bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FBFDFF"/><stop offset="1" stop-color="#D5E2EE"/></linearGradient>
    <linearGradient id="{p}up" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#1C1C1E" stop-opacity="0.9"/><stop offset="1" stop-color="#1C1C1E" stop-opacity="0"/></linearGradient>
    <linearGradient id="{p}lo" x1="1" y1="0" x2="0" y2="0"><stop offset="0" stop-color="#0A7CC2" stop-opacity="0.9"/><stop offset="1" stop-color="#0A7CC2" stop-opacity="0"/></linearGradient>
    <linearGradient id="{p}blue" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3AA8EE"/><stop offset="1" stop-color="#0A6FB0"/></linearGradient>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="{R}" fill="url(#{p}bg)"/>
  <path d="M330,470 A182,150 0 0 1 694,470" fill="none" stroke="url(#{p}up)" stroke-width="14" stroke-linecap="round"/>
  <path d="M694,554 A182,150 0 0 1 330,554" fill="none" stroke="url(#{p}lo)" stroke-width="14" stroke-linecap="round"/>
  <rect x="496" y="268" width="32" height="488" rx="16" fill="#1C1C1E"/>
  <circle cx="330" cy="512" r="72" fill="#1C1C1E"/>
  <circle cx="694" cy="512" r="72" fill="url(#{p}blue)"/>
'''

def concept_c(p):
    # "Eclipse": deep night, glowing axis, spheres on a tilted luminous ring with motion tails
    cx = cy = 512; rx, ry, rot = 300, 112, -20
    r = math.radians(rot)
    a = math.radians(40)
    front = orbit_pt(cx, cy, rx, ry, a, r)
    back = orbit_pt(cx, cy, rx, ry, a + math.pi, r)
    def tail(a_end, color, n=26, span=1.25):
        segs = []
        for i in range(n):
            t0, t1 = a_end - span * (i + 1) / n, a_end - span * i / n
            x0, y0 = orbit_pt(cx, cy, rx, ry, t0, r); x1, y1 = orbit_pt(cx, cy, rx, ry, t1, r)
            op = 0.75 * (1 - i / n) ** 1.6
            segs.append(f'<path d="M{x0:.1f},{y0:.1f} L{x1:.1f},{y1:.1f}" stroke="{color}" stroke-opacity="{op:.3f}" stroke-width="{16 - 10 * i / n:.1f}" stroke-linecap="round"/>')
        return '\n  '.join(segs)
    return f'''
  <defs>
    <radialGradient id="{p}bg" cx="0.5" cy="0.42" r="0.75"><stop offset="0" stop-color="#123A57"/><stop offset="0.6" stop-color="#0A1824"/><stop offset="1" stop-color="#05080D"/></radialGradient>
    <radialGradient id="{p}blue" cx="0.35" cy="0.3" r="0.8"><stop offset="0" stop-color="#E2F6FF"/><stop offset="0.4" stop-color="#6CCBFF"/><stop offset="1" stop-color="#0A6FB0"/></radialGradient>
    <radialGradient id="{p}white" cx="0.35" cy="0.3" r="0.8"><stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#9FB0C2"/></radialGradient>
    <linearGradient id="{p}ax" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFFFFF" stop-opacity="0"/><stop offset="0.2" stop-color="#FFFFFF"/><stop offset="0.8" stop-color="#FFFFFF"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>
    <filter id="{p}blur" x="-1" y="-1" width="3" height="3"><feGaussianBlur stdDeviation="14"/></filter>
  </defs>
  <rect x="100" y="100" width="824" height="824" rx="{R}" fill="url(#{p}bg)"/>
  <path d="{half_ellipse(cx, cy, rx, ry, rot, True)}" fill="none" stroke="#6CCBFF" stroke-opacity="0.18" stroke-width="4"/>
  {tail(a + math.pi, '#FFFFFF')}
  <circle cx="{back[0]:.1f}" cy="{back[1]:.1f}" r="50" fill="url(#{p}white)" opacity="0.7"/>
  <rect x="490" y="200" width="44" height="624" rx="22" fill="#BFE9FF" opacity="0.55" filter="url(#{p}blur)"/>
  <rect x="502" y="200" width="20" height="624" rx="10" fill="url(#{p}ax)"/>
  <path d="{half_ellipse(cx, cy, rx, ry, rot, False)}" fill="none" stroke="#6CCBFF" stroke-opacity="0.35" stroke-width="4"/>
  {tail(a, '#6CCBFF')}
  <circle cx="{front[0]:.1f}" cy="{front[1]:.1f}" r="70" fill="url(#{p}blue)"/>
'''

concepts = [('A · Orbit', concept_a), ('B · Reflection', concept_b), ('C · Eclipse', concept_c)]
for i, (name, f) in enumerate(concepts):
    open(f'concept_{"abc"[i]}.svg', 'w').write(f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">{f("c" + "abc"[i])}</svg>')

# contact sheet (square, qlmanage renders square thumbnails)
W = 1500; colw = 500
parts = []
for i, (name, f) in enumerate(concepts):
    x = i * colw
    parts.append(f'<text x="{x + 250}" y="70" font-family="-apple-system, Helvetica" font-size="34" font-weight="600" text-anchor="middle" fill="#1C1C1E">{name}</text>')
    parts.append(f'<svg x="{x + 30}" y="90" width="440" height="440" viewBox="0 0 1024 1024">{f("s" + "abc"[i])}</svg>')
    for row, bg in enumerate(['#F2F4F7', '#1E1E22']):
        y = 600 + row * 230
        parts.append(f'<rect x="{x + 15}" y="{y}" width="470" height="210" rx="20" fill="{bg}"/>')
        sx = x + 30
        for sz in (160, 80, 40, 20):
            parts.append(f'<svg x="{sx}" y="{y + 105 - sz / 2}" width="{sz}" height="{sz}" viewBox="0 0 1024 1024">{f("z" + str(row) + str(sz) + "abc"[i])}</svg>')
            sx += sz + 34
open('sheet.svg', 'w').write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}"><rect width="{W}" height="{W}" fill="#FFFFFF"/>{"".join(parts)}</svg>')
print('ok')
