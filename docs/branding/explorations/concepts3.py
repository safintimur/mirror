R = 185
BODY = 'x="100" y="100" width="824" height="824" rx="185"'
DR, DX, AW, AH = 112, 215, 52, 560   # dot radius, dot offset from axis, axis width/height

def base(bg, axis, left, right, ly=512, ry=512, left_ring=None, right_ring=None, extra_defs='', under=''):
    ax = f'<rect x="{512 - AW / 2}" y="{512 - AH / 2}" width="{AW}" height="{AH}" rx="{AW / 2}" fill="{axis}"/>' if axis else ''
    def dot(x, y, fill, ring):
        if ring:
            w = 30
            return f'<circle cx="{x}" cy="{y}" r="{DR - w / 2}" fill="none" stroke="{ring}" stroke-width="{w}"/>'
        return f'<circle cx="{x}" cy="{y}" r="{DR}" fill="{fill}"/>'
    return (f'<defs>{extra_defs}</defs><rect {BODY} fill="{bg}"/>{under}{ax}'
            f'{dot(512 - DX, ly, left, left_ring)}{dot(512 + DX, ry, right, right_ring)}')

def e1(p):  # Ink on paper
    return base('#F4F2EE', '#161616', '#161616', '#161616')

def e2(p):  # Paper on ink
    return base('#141414', '#F4F2EE', '#F4F2EE', '#F4F2EE')

def e3(p):  # Reflection: solid dot and its outline copy
    return base('#F4F2EE', '#161616', '#161616', None, right_ring='#161616')

def e4(p):  # Split: the axis is the mirror line, tones invert across it
    clip = f'<clipPath id="{p}c"><rect {BODY}/></clipPath>'
    under = (f'<g clip-path="url(#{p}c)"><rect x="100" y="100" width="412" height="824" fill="#161616"/>'
             f'<rect x="512" y="100" width="412" height="824" fill="#F4F2EE"/></g>')
    return base('#F4F2EE', None, '#F4F2EE', '#161616', extra_defs=clip, under=under)

def e5(p):  # Offset: a frame of the menu bar spin, two neutral tones on slate
    return base('#262A30', '#F2F1EE', '#F2F1EE', '#9C968C', ly=462, ry=562)

def e6(p):  # Flat provider tones, no marks
    return base('#F4F2EE', '#161616', '#161616', '#D97757')

concepts = [('E1 · тушь', e1), ('E2 · ночь', e2), ('E3 · отражение', e3),
            ('E4 · сплит', e4), ('E5 · вращение', e5), ('E6 · тона, плоско', e6)]

for i, (name, f) in enumerate(concepts):
    open(f'concept3_{i}.svg', 'w').write(f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">{f("c%d" % i)}</svg>')

W = 1500
parts = []
for i, (name, f) in enumerate(concepts):
    col, row = i % 3, i // 3
    x, y = col * 500, row * 740
    parts.append(f'<text x="{x + 250}" y="{y + 60}" font-family="-apple-system, Helvetica" font-size="32" font-weight="600" text-anchor="middle" fill="#1C1C1E">{name}</text>')
    parts.append(f'<svg x="{x + 60}" y="{y + 80}" width="380" height="380" viewBox="0 0 1024 1024">{f("s%d" % i)}</svg>')
    for strip, bg in enumerate(['#F2F4F7', '#1E1E22']):
        sy = y + 480 + strip * 125
        parts.append(f'<rect x="{x + 30}" y="{sy}" width="440" height="112" rx="18" fill="{bg}"/>')
        sx = x + 60
        for sz in (80, 48, 32, 16):
            parts.append(f'<svg x="{sx}" y="{sy + 56 - sz / 2}" width="{sz}" height="{sz}" viewBox="0 0 1024 1024">{f("z%d%d%d" % (strip, sz, i))}</svg>')
            sx += sz + 40
open('sheet3.svg', 'w').write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{W}" viewBox="0 0 {W} {W}"><rect width="{W}" height="{W}" fill="#FFFFFF"/>{"".join(parts)}</svg>')
print('ok')
